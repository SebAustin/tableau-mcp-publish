"""FastAPI sidecar: health, authoring endpoints, and the token guard."""

import zipfile

import pytest
from fastapi.testclient import TestClient

import server

client = TestClient(server.app)


def test_health_ok() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_datasource_from_table_records_returns_tdsx() -> None:
    res = client.post(
        "/datasource/from-table",
        json={"name": "Top Customers", "records": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]},
    )
    assert res.status_code == 200
    path = res.json()["path"]
    assert path.endswith(".tdsx")
    assert zipfile.is_zipfile(path)


def test_datasource_from_table_requires_input() -> None:
    res = client.post("/datasource/from-table", json={"name": "T"})
    assert res.status_code == 400


def test_workbook_starter_returns_twbx() -> None:
    res = client.post(
        "/workbook/starter",
        json={
            "datasourceName": "DS",
            "datasourceContentUrl": "ds",
            "site": "mysite",
            "sheets": [{"title": "S", "markType": "bar", "cols": ["D"], "measures": ["M"]}],
        },
    )
    assert res.status_code == 200
    assert res.json()["path"].endswith(".twbx")


def test_token_guard_blocks_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDECAR_TOKEN", "secret")
    assert client.get("/health").status_code == 403
    assert client.get("/health", headers={"X-Sidecar-Token": "secret"}).status_code == 200
