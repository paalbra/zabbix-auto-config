# About

Zabbix-auto-config is an utility that aims to automatically configure hosts, host groups, host inventories and templates in the monitoring software [Zabbix](https://www.zabbix.com/).

Note: This is meant to work with Zabbix versions: 7.0.

# Quick start

This is a crash course in how to quickly get this application up and running in a local test environment:

## Zabbix test instance

Follow the [README in the zabbix-simple submodule](./test-instance/zabbix-simple/README.md).

## Zabbix prerequisites

A host group for all disabled hosts and all hosts (a default host group) is required and will be created if missing:

* All-auto-disabled-hosts (configurable under `zabbix.hostgroup_disabled`)
* All-hosts (configurable under `zabbix.hostgroup_all`)

For automatic linking in templates you could create the templates:

* Template-barry
* Template-pizza

## Database

```bash
PGPASSWORD=something psql -h localhost -U postgres -p 5432 -U zabbix << EOF
CREATE DATABASE zac;
\c zac
CREATE TABLE hosts (
    data jsonb
);
CREATE TABLE hosts_source (
    data jsonb
);
EOF
```

## Application

```bash
python3 -m venv venv
. venv/bin/activate
pip install -e .
cp config.sample.toml config.toml
sed -i 's/^dryrun = true$/dryrun = false/g' config.toml
mkdir -p path/to/source_collector_dir/ path/to/host_modifier_dir/ path/to/map_dir/
cat > path/to/source_collector_dir/mysource.py << EOF
import zabbix_auto_config.models

HOSTS = [
    {
        "hostname": "foo.example.com",
    },
    {
        "hostname": "bar.example.com",
    },
]

def collect(*args, **kwargs):
    hosts = []
    for host in HOSTS:
        host["enabled"] = True
        host["siteadmins"] = ["bob@example.com"]
        host["properties"] = ["pizza"]
        source = kwargs.get("source")
        if source:
            host["properties"].append(source)
        hosts.append(zabbix_auto_config.models.Host(**host))

    return hosts

if __name__ == "__main__":
    for host in collect():
        print(host.json())
EOF
cat > path/to/host_modifier_dir/mod.py << EOF
def modify(host):
    if host.hostname == "bar.example.com":
        host.properties.add("barry")
    return host
EOF
cat > path/to/map_dir/property_template_map.txt << EOF
pizza:Template-pizza
barry:Template-barry
EOF
cat > path/to/map_dir/property_hostgroup_map.txt << EOF
other:Hostgroup-other-hosts
EOF
cat > path/to/map_dir/siteadmin_hostgroup_map.txt << EOF
bob@example.com:Hostgroup-bob-hosts
EOF
```

Run the application:

```bash
zac
```

## Systemd unit

You could run this as a systemd service:

```ini
[Unit]
Description=Zabbix auto config
After=network.target

[Service]
User=zabbix
Group=zabbix
WorkingDirectory=/home/zabbix/zabbix-auto-config
Environment=PATH=/home/zabbix/zabbix-auto-config/venv/bin
ExecStart=/home/zabbix/zabbix-auto-config/venv/bin/zac
TimeoutSec=300

[Install]
WantedBy=multi-user.target
```

## Host inventory

Zac manages only inventory properties configured as `managed_inventory` in `config.toml`. An inventory property will not be removed/blanked from Zabbix even if the inventory property is removed from `managed_inventory` list or from the host in the source e.g:

1. Add "location=x" to a host in a source and wait for sync
2. Remove the "location" property from the host in the source
3. "location=x" will remain in Zabbix

