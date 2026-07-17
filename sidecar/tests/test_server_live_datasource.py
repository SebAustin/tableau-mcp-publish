"""HTTP-level tests for POST /datasource/live (Phase E2 slice C).

Covers: snowflake happy path, presto happy path, missing-field validation
(422), business-rule 400s (missing warehouse/dbname, missing catalog, bad/
impossible authentication), the token guard, and the "no credentials
anywhere in the response XML" guarantee even if a caller mistakenly
includes one.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server

client = TestClient(server.app)

_SNOWFLAKE_PAYLOAD = {
    "name": "Orders (Snowflake)",
    "connection": {
        "type": "snowflake",
        "server": "myaccount.snowflakecomputing.com",
        "schema": "PUBLIC",
        "table": "ORDERS",
        "warehouse": "COMPUTE_WH",
        "dbname": "ANALYTICS",
    },
}

_PRESTO_PAYLOAD = {
    "name": "Orders (Presto)",
    "connection": {
        "type": "presto",
        "server": "presto.internal.example.com",
        "schema": "default",
        "table": "orders",
        "port": 8443,
        "catalog": "hive",
    },
}


def test_snowflake_happy_path_returns_tds() -> None:
    res = client.post("/datasource/live", json=_SNOWFLAKE_PAYLOAD)
    assert res.status_code == 200, res.text
    path = res.json()["path"]
    assert path.endswith(".tds")
    xml_text = Path(path).read_text(encoding="utf-8")
    root = ET.fromstring(xml_text)
    assert root.find(".//connection[@class='snowflake']") is not None


def test_presto_happy_path_returns_tds() -> None:
    res = client.post("/datasource/live", json=_PRESTO_PAYLOAD)
    assert res.status_code == 200, res.text
    path = res.json()["path"]
    xml_text = Path(path).read_text(encoding="utf-8")
    root = ET.fromstring(xml_text)
    assert root.find(".//connection[@class='presto']") is not None


def test_missing_required_field_returns_422() -> None:
    """server-only payload is missing schema/table -> FastAPI/Pydantic 422, not 500."""
    payload = {"name": "DS", "connection": {"type": "snowflake", "server": "x"}}
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 422


def test_snowflake_missing_warehouse_and_dbname_returns_400() -> None:
    payload = {
        "name": "DS",
        "connection": {
            "type": "snowflake",
            "server": "x.snowflakecomputing.com",
            "schema": "PUBLIC",
            "table": "T",
        },
    }
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 400
    assert "warehouse" in res.json()["detail"]


def test_presto_missing_catalog_returns_400() -> None:
    payload = {
        "name": "DS",
        "connection": {
            "type": "presto",
            "server": "presto.example.com",
            "schema": "default",
            "table": "t",
        },
    }
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 400
    assert "catalog" in res.json()["detail"]


def test_snowflake_key_pair_auth_returns_clean_400() -> None:
    """Known-impossible case (PLAN.md): key-pair auth cannot be REST-published."""
    payload = {
        **_SNOWFLAKE_PAYLOAD,
        "connection": {**_SNOWFLAKE_PAYLOAD["connection"], "authentication": "key-pair"},
    }
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "key-pair" in detail
    assert "desktop" in detail


def test_snowflake_unsupported_authentication_returns_400() -> None:
    payload = {
        **_SNOWFLAKE_PAYLOAD,
        "connection": {**_SNOWFLAKE_PAYLOAD["connection"], "authentication": "ldap"},
    }
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 400
    assert "unsupported" in res.json()["detail"].lower()


def test_response_never_contains_a_smuggled_credential_field() -> None:
    """Even if a caller mistakenly includes username/password on the connection
    object, LiveConnectionSpec doesn't declare those fields, so Pydantic drops
    them and they never reach the emitted XML."""
    payload = {
        "name": "DS",
        "connection": {
            **_SNOWFLAKE_PAYLOAD["connection"],
            "username": "smuggled-user",
            "password": "smuggled-secret",
        },
    }
    res = client.post("/datasource/live", json=payload)
    assert res.status_code == 200, res.text
    xml_text = Path(res.json()["path"]).read_text(encoding="utf-8")
    assert "smuggled-secret" not in xml_text
    assert "smuggled-user" not in xml_text


def test_token_guard_blocks_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDECAR_TOKEN", "livetok")
    res = client.post("/datasource/live", json=_SNOWFLAKE_PAYLOAD)
    assert res.status_code == 403


def test_token_guard_passes_with_correct_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDECAR_TOKEN", "livetok")
    res = client.post(
        "/datasource/live", json=_SNOWFLAKE_PAYLOAD, headers={"X-Sidecar-Token": "livetok"}
    )
    assert res.status_code == 200
