import datetime
import importlib
import importlib.metadata
import json
import logging
import multiprocessing
import os
import os.path
import sys
import time
import tomllib

import multiprocessing_logging
import psycopg2
import pyzabbix
import requests.exceptions

from . import exceptions
from . import models
from . import processing


__version__ = importlib.metadata.version(os.path.basename(os.path.dirname(__file__)))


def get_source_collectors(config):
    source_collector_dir = config.zac.source_collector_dir
    sys.path.append(source_collector_dir)

    source_collectors = []
    for source_collector_name, source_collector_values in config.source_collectors.items():
        try:
            module = importlib.import_module(source_collector_values.module_name)
        except ModuleNotFoundError:
            logging.error("Unable to find source collector named '%s' in '%s'", source_collector_values.module_name, source_collector_dir)
            continue

        source_collector = {
            "name": source_collector_name,
            "module": module,
            "config": source_collector_values.dict(),
        }

        source_collectors.append(source_collector)

    return source_collectors


def get_config():
    cwd = os.getcwd()
    config_file = os.path.join(cwd, "config.toml")
    with open(config_file) as f:
        content = f.read()

    config = tomllib.loads(content)
    config = models.Settings(**config)

    return config


def write_health(health_file, processes, queues, failsafe):
    now = datetime.datetime.now()
    health = {
        "date": now.isoformat(timespec="seconds"),
        "date_unixtime": int(now.timestamp()),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "all_ok": True,
        "processes": [],
        "queues": [],
        "failsafe": failsafe,
    }

    for process in processes:
        health["processes"].append({
            "name": process.name,
            "pid": process.pid,
            "alive": process.is_alive(),
            "ok": process.state.get("ok")
        })

    health["all_ok"] = all([p["ok"] for p in health["processes"]])

    for queue in queues:
        health["queues"].append({
            "size": queue.qsize(),
        })

    try:
        with open(health_file, "w") as f:
            f.write(json.dumps(health))
    except:
        logging.error("Unable to write health file: %s", health_file)


def log_process_status(processes):
    process_statuses = []

    for process in processes:
        process_name = process.name
        process_status = "alive" if process.is_alive() else "dead"
        process_statuses.append(f"{process_name} is {process_status}")

    logging.debug("Process status: %s", ', '.join(process_statuses))


def preflight(config):
    # Test database connectivity
    try:
        db_connection = psycopg2.connect(config.zac.db_uri)
        # TODO: Perform a better schema check?
        with db_connection, db_connection.cursor() as db_cursor:
            db_cursor.execute("SELECT * FROM hosts")
            db_cursor.execute("SELECT * FROM hosts_source")
    except (psycopg2.OperationalError, psycopg2.errors.UndefinedTable) as e:
        raise exceptions.ZACException(*e.args)

    # Test API connectivity
    api = pyzabbix.ZabbixAPI(config.zabbix.url, timeout=config.zabbix.timeout)
    try:
        api.login(config.zabbix.username, config.zabbix.password)
    except Exception as e:
        raise exceptions.ZACException(*e.args)

    api_version = api.api_version()
    logging.info(f"Connected to Zabbix (version {api_version}) at {config.zabbix.url}")

    # Create required host groups if missing
    for hostgroup_name in (config.zabbix.hostgroup_all, config.zabbix.hostgroup_disabled):
        if not api.hostgroup.get(filter={"name": hostgroup_name}):
            logging.warning(f"Missing required host group. Will create: {hostgroup_name}")
            hostgroup = api.hostgroup.create(name=hostgroup_name)
            logging.info(f"Created hostgroup: {hostgroup_name} ({hostgroup['groupids'][0]})")


def main():
    config = get_config()

    logging.basicConfig(format='%(asctime)s %(levelname)s [%(processName)s %(process)d] [%(name)s] %(message)s', datefmt="%Y-%m-%dT%H:%M:%S%z", level=logging.DEBUG)
    multiprocessing_logging.install_mp_handler()
    logging.getLogger("pyzabbix").setLevel(logging.ERROR)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

    if config.zac.health_file is not None:
        health_file = os.path.abspath(config.zac.health_file)

    logging.info("Main start (%d) version %s", os.getpid(), __version__)

    try:
        preflight(config)
    except exceptions.ZACException as e:
        logging.error("Failed to perform preflight. Exiting because of error: %s", repr(str(e)))
        time.sleep(1)  # Prevent exit too fast for logging
        sys.exit(1)

    stop_event = multiprocessing.Event()
    state_manager = multiprocessing.Manager()
    processes = []

    source_hosts_queues = []
    source_collectors = get_source_collectors(config)
    for source_collector in source_collectors:
        source_hosts_queue = multiprocessing.Queue()
        process = processing.SourceCollectorProcess(source_collector["name"], state_manager.dict(), source_collector["module"], source_collector["config"], source_hosts_queue)
        source_hosts_queues.append(source_hosts_queue)
        processes.append(process)

    try:
        process = processing.SourceHandlerProcess("source-handler", state_manager.dict(), config.zac, source_hosts_queues)
        processes.append(process)

        process = processing.SourceMergerProcess("source-merger", state_manager.dict(), config.zac, config.zac.host_modifier_dir)
        processes.append(process)

        process = processing.ZabbixHostUpdater("zabbix-host-updater", state_manager.dict(), config.zac, config.zabbix)
        processes.append(process)

        process = processing.ZabbixHostgroupUpdater("zabbix-hostgroup-updater", state_manager.dict(), config.zac, config.zabbix)
        processes.append(process)

        process = processing.ZabbixTemplateUpdater("zabbix-template-updater", state_manager.dict(), config.zac, config.zabbix)
        processes.append(process)
    except exceptions.ZACException as e:
        logging.error("Failed to initialize child processes. Exiting: %s", str(e))
        sys.exit(1)

    for process in processes:
        process.start()

    with processing.SignalHandler(stop_event):
        status_interval = 60
        next_status = datetime.datetime.now()

        while not stop_event.is_set():
            if next_status < datetime.datetime.now():
                if health_file is not None:
                    write_health(health_file, processes, source_hosts_queues, config.zabbix.failsafe)
                log_process_status(processes)
                next_status = datetime.datetime.now() + datetime.timedelta(seconds=status_interval)

            dead_process_names = [process.name for process in processes if not process.is_alive()]
            if dead_process_names:
                logging.error("A child has died: %s. Exiting", ', '.join(dead_process_names))
                stop_event.set()

            time.sleep(1)

        logging.debug("Queues: %s", ", ".join([str(queue.qsize()) for queue in source_hosts_queues]))

        for process in processes:
            logging.info("Terminating: %s(%d)", process.name, process.pid)
            process.terminate()

        alive_processes = [process for process in processes if process.is_alive()]
        while alive_processes:
            process = alive_processes[0]
            logging.info("Waiting for: %s(%d)", process.name, process.pid)
            log_process_status(processes)  # TODO: Too verbose?
            process.join(10)
            if process.exitcode is None:
                logging.warning("Process hanging. Signaling new terminate: %s(%d)", process.name, process.pid)
                process.terminate()
            time.sleep(1)
            alive_processes = [process for process in processes if process.is_alive()]

    logging.info("Main exit")
