"""Live-connection `.tds` structural tests (Phase E2 slice C).

Mirrors the style of `test_tds_builder.py`: parse the returned XML with
`xml.etree.ElementTree` and assert structure. No XSD gate — the official TWB
XSD does not cover `.tds` (see `test_tds_builder.py`'s precedent).
"""

import xml.etree.ElementTree as ET

import pytest

import tds_builder

_SNOWFLAKE_SPEC = {
    "type": "snowflake",
    "server": "myaccount.snowflakecomputing.com",
    "db_schema": "PUBLIC",
    "table": "ORDERS",
    "warehouse": "COMPUTE_WH",
    "dbname": "ANALYTICS",
    "authentication": "username-password",
    "role": None,
    "port": None,
    "catalog": None,
    "ssl": True,
}

_PRESTO_SPEC = {
    "type": "presto",
    "server": "presto.internal.example.com",
    "db_schema": "default",
    "table": "orders",
    "port": 8443,
    "catalog": "hive",
    "ssl": True,
    "warehouse": None,
    "dbname": None,
    "authentication": "username-password",
    "role": None,
}


def test_snowflake_live_tds_has_connection_class_and_attrs() -> None:
    xml = tds_builder.build_live_tds("Orders (Snowflake)", _SNOWFLAKE_SPEC)
    root = ET.fromstring(xml)
    conn = root.find(".//named-connection/connection[@class='snowflake']")
    assert conn is not None, "expected a class='snowflake' connection"
    assert conn.get("server") == "myaccount.snowflakecomputing.com"
    assert conn.get("warehouse") == "COMPUTE_WH"
    assert conn.get("dbname") == "ANALYTICS"
    assert conn.get("schema") == "PUBLIC"
    assert conn.get("authentication") == "username-password"
    # role was None in the spec -> omitted entirely, not emitted as role="None".
    assert conn.get("role") is None


def test_snowflake_live_tds_has_no_extract_element() -> None:
    xml = tds_builder.build_live_tds("Orders (Snowflake)", _SNOWFLAKE_SPEC)
    root = ET.fromstring(xml)
    assert root.find(".//extract") is None, "a live connection must not carry an <extract>"


def test_snowflake_live_tds_relation_targets_the_table() -> None:
    xml = tds_builder.build_live_tds("Orders (Snowflake)", _SNOWFLAKE_SPEC)
    root = ET.fromstring(xml)
    relation = root.find(".//relation")
    assert relation is not None
    assert relation.get("table") == "[PUBLIC].[ORDERS]"
    assert relation.get("type") == "table"


def test_presto_live_tds_has_connection_class_and_attrs() -> None:
    xml = tds_builder.build_live_tds("Orders (Presto)", _PRESTO_SPEC)
    root = ET.fromstring(xml)
    conn = root.find(".//named-connection/connection[@class='presto']")
    assert conn is not None, "expected a class='presto' connection"
    assert conn.get("server") == "presto.internal.example.com"
    assert conn.get("port") == "8443"
    assert conn.get("catalog") == "hive"
    assert conn.get("schema") == "default"
    assert conn.get("ssl") == "yes"


def test_presto_live_tds_relation_targets_the_table() -> None:
    xml = tds_builder.build_live_tds("Orders (Presto)", _PRESTO_SPEC)
    root = ET.fromstring(xml)
    relation = root.find(".//relation")
    assert relation is not None
    assert relation.get("table") == "[default].[orders]"


def test_live_tds_never_contains_credentials() -> None:
    """The .tds itself must never carry a username/password *value* — those
    are embedded separately at publish time (see src/rest/credentials.ts).

    Note: `authentication="username-password"` is a legitimate connection
    *mode* attribute (contains the substring "password" but no secret), so
    this asserts the absence of an actual `password="..."` attribute and of
    the `<connectionCredentials>` element, not the bare substring.
    """
    for spec in (_SNOWFLAKE_SPEC, _PRESTO_SPEC):
        xml = tds_builder.build_live_tds("DS", spec)
        assert 'password="' not in xml.lower()
        assert 'username="' not in xml.lower()
        assert "<connectioncredentials" not in xml.lower()


def test_unsupported_connection_type_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported live connection type"):
        tds_builder.build_live_tds("DS", {**_SNOWFLAKE_SPEC, "type": "bigquery"})


def test_attr_tables_are_single_source_of_truth_for_correction() -> None:
    """SNOWFLAKE_ATTRS/PRESTO_ATTRS are the one place to correct a VERIFY-LIVE
    attribute name once a real Desktop-exported .tds is available (PLAN.md)."""
    assert set(tds_builder.SNOWFLAKE_ATTRS) == {
        "server",
        "warehouse",
        "dbname",
        "db_schema",
        "authentication",
        "role",
    }
    assert set(tds_builder.PRESTO_ATTRS) == {"server", "port", "catalog", "db_schema", "ssl"}
