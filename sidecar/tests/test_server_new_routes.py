"""HTTP-level tests for the two new sidecar routes (PLAN §9.1, REQUIREMENTS DB-1/PA-1).

Covers:
  /workbook/dashboard  — happy path (sqlproxy + embedded-extract), token guard,
                         bad layout (400), zero canvas (400), missing hyperPath (400)
  /datasource/from-file — happy path (including hyperPath in response), token guard,
                          unknown extension (400), bad fileType (400), oversize file (400, PA-1)
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import hyper_builder
import server

client = TestClient(server.app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHEETS_PAYLOAD = [
    {"title": "Revenue by Region", "markType": "bar", "cols": ["Region"], "measures": ["Revenue"]},
    {"title": "Top Customers", "markType": "text", "cols": [], "measures": ["Revenue"]},
]

_WORKBOOK_BASE = {
    "datasourceName": "SalesDS",
    "datasourceContentUrl": "salesds",
    "site": "mysite",
    "sheets": _SHEETS_PAYLOAD,
}


# ===========================================================================
# /workbook/dashboard — happy path (DB-1)
# ===========================================================================


def test_workbook_dashboard_happy_path_returns_twbx() -> None:
    """POST /workbook/dashboard → 200, path ends with .twbx, file is a valid zip."""
    payload = {**_WORKBOOK_BASE, "dashboardLayout": "tiled_vertical"}
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 200
    path = res.json()["path"]
    assert path.endswith(".twbx"), f"Expected .twbx, got {path!r}"
    assert zipfile.is_zipfile(path), "Returned path is not a valid zip"


def test_workbook_dashboard_contains_dashboard_element() -> None:
    """The generated .twb inside the .twbx must have a <dashboards> element (DB-1)."""
    import xml.etree.ElementTree as ET

    payload = {**_WORKBOOK_BASE, "dashboardLayout": "tiled_horizontal"}
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 200
    path = res.json()["path"]
    with zipfile.ZipFile(path) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        assert len(twb_files) == 1
        root = ET.fromstring(archive.read(twb_files[0]))
    dashboards_el = root.find("dashboards")
    assert dashboards_el is not None, "<dashboards> element missing from .twb"
    # Worksheet zones are identified by @name with NO type/type-v2 (vs wb1/wb7).
    worksheet_zones = dashboards_el.findall(".//zone[@name]")
    assert len(worksheet_zones) == len(_SHEETS_PAYLOAD), (
        f"Expected {len(_SHEETS_PAYLOAD)} worksheet zones, got {len(worksheet_zones)}"
    )


def test_workbook_dashboard_canvas_size_in_xml() -> None:
    """canvas_width / canvas_height are reflected in the <size> element (§6.2, §4.1)."""
    import xml.etree.ElementTree as ET

    payload = {
        **_WORKBOOK_BASE,
        "dashboardLayout": "tiled_vertical",
        "canvasWidth": 1200,
        "canvasHeight": 900,
    }
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 200
    path = res.json()["path"]
    with zipfile.ZipFile(path) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        root = ET.fromstring(archive.read(twb_files[0]))
    size_el = root.find(".//dashboard/size")
    assert size_el is not None, "<size> element missing"
    assert size_el.get("maxwidth") == "1200"
    assert size_el.get("maxheight") == "900"


def test_workbook_dashboard_rejects_bad_layout() -> None:
    """An unsupported dashboardLayout value must return 400, not 500."""
    payload = {**_WORKBOOK_BASE, "dashboardLayout": "floating"}
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 400
    body = res.json()
    assert "detail" in body


def test_workbook_dashboard_rejects_zero_canvas_width() -> None:
    """canvasWidth <= 0 must return 400."""
    payload = {**_WORKBOOK_BASE, "canvasWidth": 0}
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 400


def test_workbook_dashboard_rejects_zero_canvas_height() -> None:
    """canvasHeight <= 0 must return 400."""
    payload = {**_WORKBOOK_BASE, "canvasHeight": -1}
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 400


def test_workbook_dashboard_token_guard_blocks_without_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SIDECAR_TOKEN set, /workbook/dashboard must reject requests missing the header."""
    monkeypatch.setenv("SIDECAR_TOKEN", "tok123")
    res = client.post("/workbook/dashboard", json=_WORKBOOK_BASE)
    assert res.status_code == 403


def test_workbook_dashboard_token_guard_passes_with_correct_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SIDECAR_TOKEN set, the route passes when the header matches."""
    monkeypatch.setenv("SIDECAR_TOKEN", "tok123")
    res = client.post(
        "/workbook/dashboard",
        json=_WORKBOOK_BASE,
        headers={"X-Sidecar-Token": "tok123"},
    )
    assert res.status_code == 200


# ===========================================================================
# /datasource/from-file — happy path (PA-1)
# ===========================================================================


def test_datasource_from_file_csv_returns_tdsx(tmp_path: Path) -> None:
    """POST /datasource/from-file with a CSV path → 200, .tdsx, valid zip."""
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,x\n2,y\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "TestDS", "filePath": str(csv), "fileType": "csv"},
    )
    assert res.status_code == 200
    body = res.json()
    path = body["path"]
    assert path.endswith(".tdsx"), f"Expected .tdsx, got {path!r}"
    assert zipfile.is_zipfile(path)
    # hyperPath is required for the embedded-extract dashboard path.
    assert "hyperPath" in body, "Response must include 'hyperPath'"
    assert body["hyperPath"].endswith(".hyper"), (
        f"hyperPath must end with .hyper (got {body['hyperPath']!r})"
    )


def test_datasource_from_file_parquet_returns_tdsx(tmp_path: Path) -> None:
    """Parquet file path → 200, valid .tdsx."""
    p = tmp_path / "data.parquet"
    pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]}).to_parquet(str(p))
    res = client.post(
        "/datasource/from-file",
        json={"name": "ParquetDS", "filePath": str(p), "fileType": "parquet"},
    )
    assert res.status_code == 200
    assert res.json()["path"].endswith(".tdsx")
    assert zipfile.is_zipfile(res.json()["path"])


def test_datasource_from_file_xlsx_returns_tdsx(tmp_path: Path) -> None:
    """Excel .xlsx → 200, valid .tdsx."""
    p = tmp_path / "data.xlsx"
    pd.DataFrame({"x": [10, 20], "y": ["p", "q"]}).to_excel(str(p), index=False)
    res = client.post(
        "/datasource/from-file",
        json={"name": "ExcelDS", "filePath": str(p), "fileType": "xlsx"},
    )
    assert res.status_code == 200
    assert res.json()["path"].endswith(".tdsx")


def test_datasource_from_file_json_returns_tdsx(tmp_path: Path) -> None:
    """JSON array file → 200, valid .tdsx."""
    p = tmp_path / "data.json"
    p.write_text(json.dumps([{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]))
    res = client.post(
        "/datasource/from-file",
        json={"name": "JsonDS", "filePath": str(p), "fileType": "json"},
    )
    assert res.status_code == 200
    assert res.json()["path"].endswith(".tdsx")


def test_datasource_from_file_infers_extension_csv(tmp_path: Path) -> None:
    """When fileType is omitted, the server infers it from the .csv extension."""
    csv = tmp_path / "sales.csv"
    csv.write_text("a,b\n1,x\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "InferredDS", "filePath": str(csv)},
    )
    assert res.status_code == 200
    assert res.json()["path"].endswith(".tdsx")


def test_datasource_from_file_rejects_unknown_extension(tmp_path: Path) -> None:
    """A file with an unsupported extension and no explicit fileType must return 400."""
    p = tmp_path / "data.xml"
    p.write_text("<root/>")
    res = client.post(
        "/datasource/from-file",
        json={"name": "BadDS", "filePath": str(p)},
    )
    assert res.status_code == 400
    body = res.json()
    assert "detail" in body


def test_datasource_from_file_rejects_unsupported_explicit_filetype(tmp_path: Path) -> None:
    """Passing an unsupported fileType explicitly must return 4xx, not 500.

    file_type is a free-form string in FileRequest (not an enum), so the sidecar
    handler catches the ValueError from file_to_dataframe and returns 400.
    """
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,x\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "BadType", "filePath": str(csv), "fileType": "xml"},
    )
    assert res.status_code == 400  # handler raises HTTPException(400) on ValueError
    body = res.json()
    assert "detail" in body


def test_datasource_from_file_missing_required_fields() -> None:
    """Missing required 'name' field → 422 validation error, not a 500."""
    with tempfile.TemporaryDirectory() as d:
        csv = Path(d) / "data.csv"
        csv.write_text("a\n1\n")
        res = client.post(
            "/datasource/from-file",
            json={"filePath": str(csv)},
        )
    assert res.status_code == 422


def test_datasource_from_file_token_guard_blocks_without_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With SIDECAR_TOKEN set, /datasource/from-file rejects requests missing the header."""
    monkeypatch.setenv("SIDECAR_TOKEN", "secret2")
    csv = tmp_path / "data.csv"
    csv.write_text("a\n1\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "DS", "filePath": str(csv), "fileType": "csv"},
    )
    assert res.status_code == 403


def test_datasource_from_file_token_guard_passes_with_correct_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With SIDECAR_TOKEN set, the route passes when the X-Sidecar-Token header matches."""
    monkeypatch.setenv("SIDECAR_TOKEN", "secret2")
    csv = tmp_path / "data.csv"
    csv.write_text("a\n1\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "DS", "filePath": str(csv), "fileType": "csv"},
        headers={"X-Sidecar-Token": "secret2"},
    )
    assert res.status_code == 200


# ===========================================================================
# /datasource/from-file — columns schema in response
# ===========================================================================


def test_datasource_from_file_csv_returns_columns(tmp_path: Path) -> None:
    """POST /datasource/from-file must include 'columns' with correct names and categories."""
    csv = tmp_path / "data.csv"
    csv.write_text("region,customer,revenue\nWest,Acme,128400\nEast,Globex,98200\n")
    res = client.post(
        "/datasource/from-file",
        json={"name": "TestDS", "filePath": str(csv), "fileType": "csv"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "columns" in body, "Response missing 'columns' key"
    cols = {c["name"]: c["dataType"] for c in body["columns"]}
    assert cols == {"region": "string", "customer": "string", "revenue": "number"}, (
        f"Unexpected columns mapping: {cols}"
    )


def test_datasource_from_file_parquet_returns_columns(tmp_path: Path) -> None:
    """Parquet ingest must include 'columns' with correct dtype categories."""
    p = tmp_path / "data.parquet"
    pd.DataFrame({"id": [1, 2], "label": ["a", "b"], "score": [1.5, 2.5]}).to_parquet(str(p))
    res = client.post(
        "/datasource/from-file",
        json={"name": "PqDS", "filePath": str(p), "fileType": "parquet"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "columns" in body
    cols = {c["name"]: c["dataType"] for c in body["columns"]}
    assert cols["id"] == "number"
    assert cols["label"] == "string"
    assert cols["score"] == "number"


def test_datasource_from_file_xlsx_returns_columns(tmp_path: Path) -> None:
    """Excel ingest must include 'columns' with correct dtype categories."""
    p = tmp_path / "data.xlsx"
    pd.DataFrame({"x": [10, 20], "y": ["p", "q"]}).to_excel(str(p), index=False)
    res = client.post(
        "/datasource/from-file",
        json={"name": "XlDS", "filePath": str(p), "fileType": "xlsx"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "columns" in body
    cols = {c["name"]: c["dataType"] for c in body["columns"]}
    assert cols["x"] == "number"
    assert cols["y"] == "string"


def test_datasource_from_file_rejects_oversized_file(tmp_path: Path) -> None:
    """An oversized file must return 400 with a 'size limit' message, not a 500 (PA-1).

    Uses a mock to make hyper_builder.file_to_dataframe raise the size-limit ValueError
    so we can verify the HTTP layer converts it to a 400 rather than a 500.
    """
    csv = tmp_path / "huge.csv"
    csv.write_text("a,b\n1,x\n")

    # Patch file_to_dataframe to raise the exact error the size guard would raise.
    with patch.object(
        hyper_builder,
        "file_to_dataframe",
        side_effect=ValueError("File exceeds the 500 MB size limit (600 MB)."),
    ):
        res = client.post(
            "/datasource/from-file",
            json={"name": "HugeDS", "filePath": str(csv), "fileType": "csv"},
        )
    assert res.status_code == 400
    body = res.json()
    assert "detail" in body
    assert "size limit" in body["detail"]


# ===========================================================================
# /workbook/dashboard — embedded-extract path (hyperPath provided)
# ===========================================================================


def test_workbook_dashboard_embedded_happy_path(tmp_path: Path) -> None:
    """POST /workbook/dashboard with hyperPath → 200, .twbx with embedded .hyper."""
    import xml.etree.ElementTree as ET

    # Build a real .hyper extract first.
    csv = tmp_path / "data.csv"
    csv.write_text("Region,Revenue\nWest,100\nEast,200\n")
    ds_res = client.post(
        "/datasource/from-file",
        json={"name": "EmbeddedDS", "filePath": str(csv), "fileType": "csv"},
    )
    assert ds_res.status_code == 200
    hyper_path = ds_res.json()["hyperPath"]
    assert hyper_path and hyper_path.endswith(".hyper"), (
        f"Expected a .hyper path in response, got {hyper_path!r}"
    )

    payload = {
        **_WORKBOOK_BASE,
        "datasourceName": "EmbeddedDS",
        "hyperPath": hyper_path,
        "dashboardLayout": "tiled_vertical",
    }
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 200, res.text
    path = res.json()["path"]
    assert path.endswith(".twbx")
    assert zipfile.is_zipfile(path)

    # Verify the zip contains Data/*.hyper
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        hyper_entries = [n for n in names if n.startswith("Data/") and n.endswith(".hyper")]
        assert len(hyper_entries) == 1, (
            f"Expected exactly one Data/*.hyper entry, got {hyper_entries}"
        )
        # The .twb must use a federated datasource, not sqlproxy
        twb_files = [n for n in names if n.endswith(".twb")]
        assert len(twb_files) == 1
        root = ET.fromstring(archive.read(twb_files[0]))

    assert root.find(".//connection[@class='sqlproxy']") is None, (
        "sqlproxy must NOT appear when hyperPath is provided"
    )
    assert root.find(".//connection[@class='federated']") is not None, (
        "federated connection must be present in the embedded workbook"
    )


def test_workbook_dashboard_embedded_rejects_missing_hyper_path(tmp_path: Path) -> None:
    """POST /workbook/dashboard with a non-existent hyperPath must return 400."""
    payload = {
        **_WORKBOOK_BASE,
        "hyperPath": str(tmp_path / "ghost.hyper"),
    }
    res = client.post("/workbook/dashboard", json=payload)
    assert res.status_code == 400
    body = res.json()
    assert "detail" in body
