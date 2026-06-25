"""Tests for the embedded-extract (.hyper) workbook builder.

Verifies that build_embedded_twbx / build_embedded_twb_xml:
- emit a federated datasource block (not sqlproxy)
- use [federated.{slug}] field references in worksheets
- embed Data/{file}.hyper in the .twbx zip
- pass the XSD gate (schema-valid output)
- fail loudly when the hyper_path is missing
- include a <dashboards> element when dashboards are requested
- produce a self-contained .twbx that Tableau Cloud can render
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import hyper_builder
import twb_builder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hyper_file(tmp_path: Path) -> Path:
    """Build a tiny .hyper extract with two columns (Region: string, Revenue: real)."""
    df = pd.DataFrame({"Region": ["West", "East"], "Revenue": [100.0, 200.0]})
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


SHEETS = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    }
]

SHEETS_2 = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
    {
        "title": "Top Customers",
        "mark_type": "text",
        "cols": [],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARDS = [{"name": "Dashboard 1", "titles": ["Revenue by Region", "Top Customers"]}]


# ---------------------------------------------------------------------------
# Datasource structure
# ---------------------------------------------------------------------------


def test_embedded_datasource_is_federated(hyper_file: Path) -> None:
    """The datasource must use class='federated', not sqlproxy."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    assert root.find(".//connection[@class='sqlproxy']") is None, (
        "sqlproxy connection must NOT appear in an embedded-extract workbook"
    )
    assert root.find(".//connection[@class='federated']") is not None, (
        "federated connection is required for an embedded-extract workbook"
    )


def test_embedded_datasource_has_hyper_named_connection(hyper_file: Path) -> None:
    """The federated block must have a named-connection pointing at a hyper connection."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    hyper_conn = root.find(".//named-connection/connection[@class='hyper']")
    assert hyper_conn is not None, (
        "A named-connection with class='hyper' must exist inside the federated block"
    )
    dbname = hyper_conn.get("dbname", "")
    assert dbname.startswith("Data/"), (
        f"hyper connection dbname must start with 'Data/' (got {dbname!r})"
    )
    assert dbname.endswith(hyper_file.name), (
        f"hyper connection dbname must end with the .hyper filename (got {dbname!r})"
    )


def test_embedded_datasource_internal_name_is_federated_slug(hyper_file: Path) -> None:
    """The datasource @name must be 'federated.{slug}' (not 'sqlproxy.*')."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "My Datasource", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    ds = root.find("./datasources/datasource")
    assert ds is not None
    name = ds.get("name", "")
    assert name.startswith("federated."), (
        f"datasource @name must start with 'federated.' (got {name!r})"
    )


def test_embedded_datasource_has_extract_block(hyper_file: Path) -> None:
    """The datasource must have an <extract> block pointing at the .hyper."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    extract_conn = root.find(".//extract/connection[@class='hyper']")
    assert extract_conn is not None, (
        "<extract><connection class='hyper'> must be present in the datasource"
    )
    assert extract_conn.get("schema") == "Extract"
    assert extract_conn.get("tablename") == "Extract"


def test_embedded_datasource_has_metadata_records(hyper_file: Path) -> None:
    """metadata-records must contain one <metadata-record class='column'> per column."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    records = root.findall(".//metadata-records/metadata-record[@class='column']")
    assert len(records) == len(columns), (
        f"Expected {len(columns)} metadata-record elements, got {len(records)}"
    )
    names = {r.findtext("remote-name") for r in records}
    assert {"Region", "Revenue"}.issubset(names), (
        f"Expected Region and Revenue in metadata-records, got {names}"
    )


# ---------------------------------------------------------------------------
# Worksheet field references
# ---------------------------------------------------------------------------


def test_worksheet_references_federated_datasource(hyper_file: Path) -> None:
    """The worksheet view must reference the 'federated.*' datasource (not sqlproxy)."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    ws_ds = root.find(".//worksheets/worksheet/table/view/datasources/datasource")
    assert ws_ds is not None
    name = ws_ds.get("name", "")
    assert name.startswith("federated."), (
        f"worksheet datasource reference must start with 'federated.' (got {name!r})"
    )


def test_worksheet_rows_use_federated_prefix(hyper_file: Path) -> None:
    """<rows> and <cols> must use [federated.*] field references, not [sqlproxy.*]."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    for tag in ("rows", "cols"):
        el = root.find(f".//worksheets/worksheet/table/{tag}")
        if el is not None and el.text:
            assert "[federated." in el.text, (
                f"<{tag}> must reference [federated.*] fields (got {el.text!r})"
            )
            assert "sqlproxy" not in el.text, (
                f"<{tag}> must not reference sqlproxy (got {el.text!r})"
            )


def test_worksheet_dependencies_use_federated_name(hyper_file: Path) -> None:
    """datasource-dependencies must point at the federated.* name."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    ds_ref = deps.get("datasource", "")
    assert ds_ref.startswith("federated."), (
        f"datasource-dependencies @datasource must start with 'federated.' (got {ds_ref!r})"
    )


# ---------------------------------------------------------------------------
# Zip structure
# ---------------------------------------------------------------------------


def test_embedded_twbx_contains_hyper_under_data(
    tmp_path: Path, hyper_file: Path
) -> None:
    """The .twbx zip must contain Data/{filename}.hyper."""
    out = tmp_path / "wb.twbx"
    twb_builder.build_embedded_twbx(
        datasource_name="Sales DS",
        hyper_path=hyper_file,
        sheets=SHEETS,
        out_path=out,
    )
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert f"Data/{hyper_file.name}" in names, (
        f"Data/{hyper_file.name} must be in the zip, got {names}"
    )


def test_embedded_twbx_contains_twb(tmp_path: Path, hyper_file: Path) -> None:
    """The .twbx must contain exactly one .twb file."""
    out = tmp_path / "wb.twbx"
    twb_builder.build_embedded_twbx(
        datasource_name="Sales DS",
        hyper_path=hyper_file,
        sheets=SHEETS,
        out_path=out,
    )
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
    assert len(twb_files) == 1, f"Expected 1 .twb in zip, got {twb_files}"


def test_embedded_twbx_twb_is_valid_xml(tmp_path: Path, hyper_file: Path) -> None:
    """The .twb inside the .twbx must parse as valid XML with root <workbook>."""
    out = tmp_path / "wb.twbx"
    twb_builder.build_embedded_twbx(
        datasource_name="Sales DS",
        hyper_path=hyper_file,
        sheets=SHEETS,
        out_path=out,
    )
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        root = ET.fromstring(archive.read(twb_files[0]))
    assert root.tag == "workbook"


# ---------------------------------------------------------------------------
# Dashboard integration
# ---------------------------------------------------------------------------


def test_embedded_twbx_with_dashboard_has_dashboards_element(
    tmp_path: Path, hyper_file: Path
) -> None:
    """When dashboards are specified, the .twb must contain a <dashboards> element."""
    out = tmp_path / "wb.twbx"
    twb_builder.build_embedded_twbx(
        datasource_name="Sales DS",
        hyper_path=hyper_file,
        sheets=SHEETS_2,
        out_path=out,
        dashboards=DASHBOARDS,
    )
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        root = ET.fromstring(archive.read(twb_files[0]))
    assert root.find("dashboards") is not None, "<dashboards> element must be present"


def test_embedded_twbx_with_dashboard_has_correct_zone_count(
    tmp_path: Path, hyper_file: Path
) -> None:
    """The dashboard must have one zone per sheet title."""
    out = tmp_path / "wb.twbx"
    twb_builder.build_embedded_twbx(
        datasource_name="Sales DS",
        hyper_path=hyper_file,
        sheets=SHEETS_2,
        out_path=out,
        dashboards=DASHBOARDS,
    )
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        root = ET.fromstring(archive.read(twb_files[0]))
    ws_zones = root.findall(".//dashboards//zone[@name]")
    assert len(ws_zones) == len(SHEETS_2), (
        f"Expected {len(SHEETS_2)} worksheet zones, got {len(ws_zones)}"
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_embedded_twbx_raises_on_missing_hyper(tmp_path: Path) -> None:
    """build_embedded_twbx must raise FileNotFoundError when the .hyper is absent."""
    missing = tmp_path / "does_not_exist.hyper"
    with pytest.raises(FileNotFoundError, match="hyper extract not found"):
        twb_builder.build_embedded_twbx(
            datasource_name="DS",
            hyper_path=missing,
            sheets=SHEETS,
            out_path=tmp_path / "wb.twbx",
        )


# ---------------------------------------------------------------------------
# XSD gate — embedded output must be schema-valid (same gate as sqlproxy path)
# ---------------------------------------------------------------------------


def test_embedded_twb_is_schema_valid(hyper_file: Path) -> None:
    """build_embedded_twb_xml must produce XSD-valid output.

    Uses the same vendored XSD gate (twb_2026.1.0.xsd) that guards the
    sqlproxy path.  If this is RED the embedded XML has a structural issue
    the XSD can detect; schema error_log is attached to the assertion.
    """
    from pathlib import Path as _P

    from lxml import etree

    schemas_dir = _P(__file__).parent / "schemas"
    xsd_path = schemas_dir / "twb_2026.1.0.xsd"
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    xsd_doc = etree.parse(str(xsd_path), parser)
    schema = etree.XMLSchema(xsd_doc)

    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_str = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS
    )
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"TWB XSD validation FAILED for embedded-extract output:\n{errors}"


def test_embedded_twb_with_dashboard_is_schema_valid(hyper_file: Path) -> None:
    """build_embedded_twb_xml with dashboards must also be XSD-valid."""
    from pathlib import Path as _P

    from lxml import etree

    schemas_dir = _P(__file__).parent / "schemas"
    xsd_path = schemas_dir / "twb_2026.1.0.xsd"
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    xsd_doc = etree.parse(str(xsd_path), parser)
    schema = etree.XMLSchema(xsd_doc)

    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_str = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, SHEETS_2,
        dashboards=DASHBOARDS,
    )
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, (
        f"TWB XSD validation FAILED for embedded-extract + dashboard output:\n{errors}"
    )
