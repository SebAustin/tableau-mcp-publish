"""Tests for color encoding (G-01) in _build_worksheet.

Covers the three ``color.kind`` variants:
- ``measure_names``  → ``[:Measure Names]`` (wb1 ~3538-3540)
- ``dimension``      → ``_dim_instance(field)``
- ``measure``        → ``_measure_instance(field)``

Each test asserts:
1. XSD validity via the vendored twb_2026.1.0.xsd gate.
2. ``<encodings><color column='...'/>`` is present in the pane.
3. The exact column format matches the wb1 reference.

Both build paths are covered:
- ``build_twb_xml`` (sqlproxy)
- ``build_embedded_twb_xml`` (federated)
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
    """Tiny .hyper with Region (string) + Revenue (real) + Category (string)."""
    df = pd.DataFrame(
        {
            "Region": ["West", "East"],
            "Category": ["Furniture", "Tech"],
            "Revenue": [100.0, 200.0],
        }
    )
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Fixtures: sheet specs with color encoding
# ---------------------------------------------------------------------------

SHEET_COLOR_MEASURE_NAMES = {
    "title": "Revenue by Category",
    "mark_type": "bar",
    "cols": ["Category"],
    "rows": [],
    "measures": ["Revenue"],
    "color": {"field": "Measure Names", "kind": "measure_names"},
}

SHEET_COLOR_DIMENSION = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
    "color": {"field": "Region", "kind": "dimension"},
}

SHEET_COLOR_MEASURE = {
    "title": "Profit vs Revenue",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
    "color": {"field": "Revenue", "kind": "measure"},
}

SHEET_NO_COLOR = {
    "title": "Plain Bar",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}


# ---------------------------------------------------------------------------
# Helper: find the <color> encoding element in a parsed workbook
# ---------------------------------------------------------------------------


def _find_color_encoding(root: ET.Element) -> ET.Element | None:
    return root.find(".//pane/encodings/color")


# ---------------------------------------------------------------------------
# A — color.kind == "measure_names" (wb1 ~3538-3540)
# ---------------------------------------------------------------------------


def test_color_measure_names_emits_encoding_sqlproxy() -> None:
    """bar sheet with color.kind='measure_names' must emit <color column='..[:Measure Names]'>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_MEASURE_NAMES])
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None, "<encodings><color/> not found in pane"
    col = color_el.get("column", "")
    assert col.endswith(".[:Measure Names]"), (
        f"Expected column ending in '.[:Measure Names]', got {col!r} "
        "(mirrors wb1 line ~3539: column='[Sample - Superstore].[:Measure Names]')"
    )


def test_color_measure_names_xsd_valid_sqlproxy() -> None:
    """build_twb_xml with color.kind='measure_names' must be XSD-valid."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_MEASURE_NAMES])
    _assert_xsd_valid(xml, "color measure_names sqlproxy")


def test_color_measure_names_emits_encoding_embedded(hyper_file: Path) -> None:
    """build_embedded_twb_xml with color.kind='measure_names' must emit <color> encoding."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_MEASURE_NAMES]
    )
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None, "<encodings><color/> not found in embedded pane"
    col = color_el.get("column", "")
    assert col.endswith(".[:Measure Names]"), (
        f"Expected column ending '.[:Measure Names]', got {col!r}"
    )


def test_color_measure_names_xsd_valid_embedded(hyper_file: Path) -> None:
    """build_embedded_twb_xml with color.kind='measure_names' must be XSD-valid."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_MEASURE_NAMES]
    )
    _assert_xsd_valid(xml, "color measure_names embedded")


# ---------------------------------------------------------------------------
# B — color.kind == "dimension"
# ---------------------------------------------------------------------------


def test_color_dimension_emits_encoding_sqlproxy() -> None:
    """bar sheet with color.kind='dimension' must emit <color> with none:Field:nk instance."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_DIMENSION])
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None, "<encodings><color/> not found in pane"
    col = color_el.get("column", "")
    assert "[none:Region:nk]" in col, (
        f"Dimension color encoding must use '[none:Region:nk]', got {col!r}"
    )


def test_color_dimension_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_DIMENSION])
    _assert_xsd_valid(xml, "color dimension sqlproxy")


def test_color_dimension_emits_encoding_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_DIMENSION]
    )
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None
    col = color_el.get("column", "")
    assert "[none:Region:nk]" in col, (
        f"Dimension color encoding must use '[none:Region:nk]', got {col!r}"
    )


def test_color_dimension_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_DIMENSION]
    )
    _assert_xsd_valid(xml, "color dimension embedded")


# ---------------------------------------------------------------------------
# C — color.kind == "measure"
# ---------------------------------------------------------------------------


def test_color_measure_emits_encoding_sqlproxy() -> None:
    """bar sheet with color.kind='measure' must emit <color> with sum:Field:qk instance."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_MEASURE])
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None, "<encodings><color/> not found in pane"
    col = color_el.get("column", "")
    assert "[sum:Revenue:qk]" in col, (
        f"Measure color encoding must use '[sum:Revenue:qk]', got {col!r}"
    )


def test_color_measure_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_MEASURE])
    _assert_xsd_valid(xml, "color measure sqlproxy")


def test_color_measure_emits_encoding_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_MEASURE]
    )
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is not None
    col = color_el.get("column", "")
    assert "[sum:Revenue:qk]" in col


def test_color_measure_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_COLOR_MEASURE]
    )
    _assert_xsd_valid(xml, "color measure embedded")


# ---------------------------------------------------------------------------
# D — Regression: plain sheet without color is UNCHANGED
# ---------------------------------------------------------------------------


def test_no_color_spec_produces_no_color_encoding() -> None:
    """A sheet without a 'color' key must NOT emit any <color> encoding element."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_NO_COLOR])
    root = ET.fromstring(xml)
    color_el = _find_color_encoding(root)
    assert color_el is None, (
        "Plain bar sheet without color spec must not have <color> in <encodings>"
    )


def test_no_color_spec_xsd_valid() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_NO_COLOR])
    _assert_xsd_valid(xml, "no color regression")


# ---------------------------------------------------------------------------
# E — color field appears in datasource-dependencies
# ---------------------------------------------------------------------------


def test_color_dimension_field_in_dependencies() -> None:
    """The color dimension field must appear in <datasource-dependencies>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_DIMENSION])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[Region]" in col_names, (
        f"Color dimension field [Region] must be declared in datasource-dependencies; "
        f"got {col_names!r}"
    )


def test_color_measure_field_in_dependencies() -> None:
    """The color measure field must appear in <datasource-dependencies>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_COLOR_MEASURE])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[Revenue]" in col_names, (
        f"Color measure field [Revenue] must be declared in datasource-dependencies; "
        f"got {col_names!r}"
    )
