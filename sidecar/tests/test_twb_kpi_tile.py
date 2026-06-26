"""Tests for KPI tile (kind='kpi_tile') in _build_worksheet.

The KPI tile mirrors the wb1 Sales KPI / Customer KPI / Order KPI worksheets:
- Mark class is 'Automatic' (wb1 ~4946, ~3392, ~4107)
- Multiple <text column='...'> encodings inside <encodings> (wb1 ~3394-3397)
- primaryMeasure is required; comparisonMeasure and deltaMeasure are optional
- All declared measures appear in <datasource-dependencies>
- Output is XSD-valid

Regression:
- A plain text sheet (mark_type='text', no kind='kpi_tile') still uses a
  single <text> encoding and is structurally unchanged.
- All new fields being absent leaves a bar/line sheet byte-equivalent.

Both build paths are covered: ``build_twb_xml`` and ``build_embedded_twb_xml``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest
from lxml import etree

import hyper_builder
import twb_builder

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


def _load_schema() -> etree.XMLSchema:
    parser = _safe_parser()
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str, label: str) -> None:
    schema = _load_schema()
    parser = _safe_parser()
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED [{label}]:\n{errors}"


@pytest.fixture()
def hyper_file(tmp_path: Path) -> Path:
    """Tiny .hyper with three measure columns matching the KPI spec."""
    df = pd.DataFrame(
        {
            "Current Sales": [500_000.0],
            "Previous Sales": [450_000.0],
            "Sales Delta": [50_000.0],
        }
    )
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Sheet specs
# ---------------------------------------------------------------------------

SHEET_KPI_PRIMARY_ONLY = {
    "title": "Sales KPI",
    "kind": "kpi_tile",
    "mark_type": "text",
    "cols": [],
    "rows": [],
    "measures": [],
    "kpi": {"primaryMeasure": "Current Sales"},
}

SHEET_KPI_FULL = {
    "title": "Sales KPI Full",
    "kind": "kpi_tile",
    "mark_type": "text",
    "cols": [],
    "rows": [],
    "measures": [],
    "kpi": {
        "primaryMeasure": "Current Sales",
        "comparisonMeasure": "Previous Sales",
        "deltaMeasure": "Sales Delta",
    },
}

SHEET_PLAIN_TEXT = {
    "title": "Top Customers",
    "mark_type": "text",
    "cols": [],
    "rows": [],
    "measures": ["Revenue"],
}

SHEET_PLAIN_BAR = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}


# ---------------------------------------------------------------------------
# A — Mark class is 'Automatic' (mirrors wb1 KPI sheets ~4946, ~3392, ~4107)
# ---------------------------------------------------------------------------


def test_kpi_tile_emits_automatic_mark_sqlproxy() -> None:
    """kind='kpi_tile' must emit <mark class='Automatic'/> (mirrors wb1 ~4946)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_PRIMARY_ONLY])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None, "<mark> not found in pane"
    assert mark.get("class") == "Automatic", (
        f"KPI tile must use class='Automatic' (mirrors wb1 ~4946); "
        f"got {mark.get('class')!r}"
    )


def test_kpi_tile_emits_automatic_mark_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_KPI_PRIMARY_ONLY]
    )
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Automatic"


# ---------------------------------------------------------------------------
# B — Primary measure text encoding
# ---------------------------------------------------------------------------


def test_kpi_tile_primary_measure_in_text_encoding() -> None:
    """primaryMeasure must appear as a <text column='...'> inside <encodings>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_PRIMARY_ONLY])
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None, "<encodings> must be present for kpi_tile"
    text_cols = [el.get("column", "") for el in encodings.findall("text")]
    assert any("[sum:Current Sales:qk]" in c for c in text_cols), (
        f"primaryMeasure 'Current Sales' must appear as [sum:Current Sales:qk] "
        f"in a <text> encoding; text cols: {text_cols!r}"
    )


def test_kpi_tile_primary_measure_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_KPI_PRIMARY_ONLY]
    )
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None
    text_cols = [el.get("column", "") for el in encodings.findall("text")]
    assert any("[sum:Current Sales:qk]" in c for c in text_cols)


# ---------------------------------------------------------------------------
# C — Full KPI: three <text> encodings (primary + comparison + delta)
# ---------------------------------------------------------------------------


def test_kpi_tile_full_has_three_text_encodings() -> None:
    """Full KPI spec must emit three <text> encoding elements (wb1 ~3394-3397)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_FULL])
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None
    text_els = encodings.findall("text")
    assert len(text_els) == 3, (
        f"Full KPI tile must have 3 <text> encodings "
        f"(primary + comparison + delta, mirrors wb1 ~3394-3397); "
        f"got {len(text_els)}"
    )


def test_kpi_tile_full_text_columns() -> None:
    """All three KPI measures must appear as <text column='...'>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_FULL])
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None
    text_cols = [el.get("column", "") for el in encodings.findall("text")]
    assert any("[sum:Current Sales:qk]" in c for c in text_cols), (
        f"primaryMeasure must be in text encodings; got {text_cols!r}"
    )
    assert any("[sum:Previous Sales:qk]" in c for c in text_cols), (
        f"comparisonMeasure must be in text encodings; got {text_cols!r}"
    )
    assert any("[sum:Sales Delta:qk]" in c for c in text_cols), (
        f"deltaMeasure must be in text encodings; got {text_cols!r}"
    )


def test_kpi_tile_full_text_columns_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_KPI_FULL]
    )
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None
    text_cols = [el.get("column", "") for el in encodings.findall("text")]
    assert any("[sum:Current Sales:qk]" in c for c in text_cols)
    assert any("[sum:Previous Sales:qk]" in c for c in text_cols)
    assert any("[sum:Sales Delta:qk]" in c for c in text_cols)


# ---------------------------------------------------------------------------
# D — All KPI measures in datasource-dependencies
# ---------------------------------------------------------------------------


def test_kpi_tile_full_measures_in_dependencies() -> None:
    """All KPI measures must appear in <datasource-dependencies>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_FULL])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    for field in ("[Current Sales]", "[Previous Sales]", "[Sales Delta]"):
        assert field in col_names, (
            f"KPI measure {field} must be declared in datasource-dependencies; "
            f"got {col_names!r}"
        )


# ---------------------------------------------------------------------------
# E — XSD validity
# ---------------------------------------------------------------------------


def test_kpi_tile_primary_only_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_PRIMARY_ONLY])
    _assert_xsd_valid(xml, "kpi_tile primary-only sqlproxy")


def test_kpi_tile_full_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_FULL])
    _assert_xsd_valid(xml, "kpi_tile full sqlproxy")


def test_kpi_tile_primary_only_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_KPI_PRIMARY_ONLY]
    )
    _assert_xsd_valid(xml, "kpi_tile primary-only embedded")


def test_kpi_tile_full_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_KPI_FULL]
    )
    _assert_xsd_valid(xml, "kpi_tile full embedded")


# ---------------------------------------------------------------------------
# F — Regression: plain text mark path is UNCHANGED
# ---------------------------------------------------------------------------


def test_plain_text_mark_still_single_encoding() -> None:
    """A plain 'text' mark sheet (no kind='kpi_tile') must keep its single <text> encoding."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_PLAIN_TEXT])
    root = ET.fromstring(xml)
    encodings = root.find(".//pane/encodings")
    assert encodings is not None, "Plain text mark must still have <encodings>"
    text_els = encodings.findall("text")
    assert len(text_els) == 1, (
        f"Plain text mark must have exactly 1 <text> encoding; got {len(text_els)}"
    )


def test_plain_text_mark_xsd_valid() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_PLAIN_TEXT])
    _assert_xsd_valid(xml, "plain text mark regression")


def test_plain_bar_unaffected_by_kpi_fields() -> None:
    """A plain bar sheet with no 'kind' or 'kpi' key must emit <mark class='Bar'/> unchanged."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_PLAIN_BAR])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Bar", (
        f"Plain bar sheet must not be affected by kpi_tile path; got {mark.get('class')!r}"
    )
    # No <encodings> element on a plain bar
    encodings = root.find(".//pane/encodings")
    assert encodings is None, "Plain bar sheet must not have <encodings>"


def test_kpi_tile_mark_type_automatic_not_text() -> None:
    """KPI tile mark class must be 'Automatic', not 'Text', even though mark_type='text'."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_KPI_PRIMARY_ONLY])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Automatic", (
        f"KPI tile (kind='kpi_tile') must override mark_type='text' to 'Automatic'; "
        f"got {mark.get('class')!r}"
    )
