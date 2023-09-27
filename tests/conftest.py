import os
import pytest


@pytest.fixture(scope="function")
def minimal_hosts():
    yield [
        {
            "enabled": True,
            "hostname": "foo.example.com",
        },
    ]


@pytest.fixture(scope="function")
def full_hosts():
    yield [
        {
            "enabled": True,
            "hostname": "foo.example.com",
            "importance": 1,
            "interfaces": [
                {
                    "endpoint": "foo.example.com",
                    "port": "10050",
                    "type": 1,
                },
                {
                    "endpoint": "foo.example.com",
                    "details": {
                        "version": 2,
                        "community": "{$SNMP_COMMUNITY}",
                    },
                    "port": "161",
                    "type": 2,
                },
            ],
            "inventory": None,
            "macros": None,
            "properties": {"prop1", "prop2"},
            "proxy_pattern": r"^zbx-proxy\d+\.example\.com$",
            "siteadmins": {"bob@example.com", "alice@example.com"},
            "sources": {"source1", "source2"},
            "tags": [["tag1", "x"], ["tag2", "y"]],
        },
    ]


@pytest.fixture(scope="function")
def invalid_hosts():
    yield [
        {
            "enabled": True,
            "hostname": "invalid-proxy-pattern.example.com",
            "proxy_pattern": "[",
        },
        {
            "enabled": True,
            "hostname": "invalid-interface.example.com",
            "interfaces": [
                {
                    "endpoint": "type-2-sans-details.example.com",
                    "port": "10050",
                    "type": 2,
                },
            ],
        },
        {
            "enabled": True,
            "hostname": "duplicate-interface.example.com",
            "interfaces": [
                {
                    "endpoint": "endpoint1.example.com",
                    "port": "10050",
                    "type": 1,
                },
                {
                    "endpoint": "endpoint2.example.com",
                    "port": "10050",
                    "type": 1,
                },
            ],
        },
        {
            "enabled": True,
            "hostname": "invalid-importance.example.com",
            "importance": -1,
        },
    ]


@pytest.fixture(scope="function")
def sample_config():
    with open(
        os.path.dirname(os.path.dirname(__file__)) + "/config.sample.toml"
    ) as config:
        yield config.read()
