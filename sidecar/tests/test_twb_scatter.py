"""Tests for scatter (Circle mark / G-02) in _build_worksheet.

Verifies:
- ``mark_type="scatter"`` emits ``<mark class='Circle'/>`` (wb1 ~3815)
- ``scatter.x`` measure appears on ``<cols>``
- ``scatter.y`` measure appears on ``<rows>``
- Both measures appear in ``<datasource-dependencies>``
- Optional ``scatter.breakdown`` dimension appears as a ``<color>`` encoding
  and in ``<datasource-dependencies>``
- Output is XSD-valid via the vendored twb_2026.1.0.xsd gate
- Both build paths covered: ``build_twb_xml`` and ``build_embedded_twb_xml``
- Regression: a plain bar sheet is byte-structurally unchanged
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
    """Tiny .hyper with Sales, Profit (real) and Category (string)."""
    df = pd.DataFrame(
        {
            "Category": ["Furniture", "Tech"],
            "Sales": [100.0, 200.0],
            "Profit": [10.0, 40.0],
        }
    )
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Sheet specs
# ---------------------------------------------------------------------------

SHEET_SCATTER = {
    "title": "Sales vs Profit",
    "mark_type": "scatter",
    "cols": [],
    "rows": [],
    "measures": [],
    "scatter": {"x": "Sales", "y": "Profit"},
}

SHEET_SCATTER_WITH_BREAKDOWN = {
    "title": "Sales vs Profit by Category",
    "mark_type": "scatter",
    "cols": [],
    "rows": [],
    "measures": [],
    "scatter": {"x": "Sales", "y": "Profit", "breakdown": "Category"},
}

SHEET_SCATTER_VIA_FIELD = {
    # Scatter triggered via the 'scatter' dict, mark_type may be omitted or "bar".
    "title": "Sales vs Profit (field trigger)",
    "mark_type": "bar",
    "cols": [],
    "rows": [],
    "measures": [],
    "scatter": {"x": "Sales", "y": "Profit"},
}

SHEET_PLAIN_BAR = {
    "title": "Plain Bar",
    "mark_type": "bar",
    "cols": ["Category"],
    "rows": [],
    "measures": ["Sales"],
}


# ---------------------------------------------------------------------------
# A — Circle mark class (wb1 ~3815)
# ---------------------------------------------------------------------------


def test_scatter_emits_circle_mark_sqlproxy() -> None:
    """mark_type='scatter' must emit <mark class='Circle'/> (mirrors wb1 line ~3815)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None, "<mark> element not found in pane"
    assert mark.get("class") == "Circle", (
        f"Expected class='Circle', got {mark.get('class')!r} "
        "(mirrors wb1 line ~3815: <mark class='Circle'/>)"
    )


def test_scatter_emits_circle_mark_embedded(hyper_file: Path) -> None:
    """build_embedded_twb_xml: scatter must emit <mark class='Circle'/>."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_SCATTER]
    )
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Circle", (
        f"Expected class='Circle', got {mark.get('class')!r}"
    )


def test_scatter_via_scatter_field_emits_circle() -> None:
    """The scatter spec field alone (without mark_type='scatter') triggers Circle mark."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER_VIA_FIELD])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Circle", (
        f"'scatter' dict field must trigger Circle even when mark_type is 'bar'; "
        f"got {mark.get('class')!r}"
    )


# ---------------------------------------------------------------------------
# B — x measure on <cols>, y measure on <rows>
# ---------------------------------------------------------------------------


def test_scatter_x_on_cols_sqlproxy() -> None:
    """scatter.x measure must appear on <cols>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    root = ET.fromstring(xml)
    cols_el = root.find(".//table/cols")
    assert cols_el is not None
    cols_text = cols_el.text or ""
    assert "[sum:Sales:qk]" in cols_text, (
        f"scatter.x='Sales' must be on <cols> as [sum:Sales:qk]; got {cols_text!r}"
    )


def test_scatter_y_on_rows_sqlproxy() -> None:
    """scatter.y measure must appear on <rows>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    root = ET.fromstring(xml)
    rows_el = root.find(".//table/rows")
    assert rows_el is not None
    rows_text = rows_el.text or ""
    assert "[sum:Profit:qk]" in rows_text, (
        f"scatter.y='Profit' must be on <rows> as [sum:Profit:qk]; got {rows_text!r}"
    )


def test_scatter_x_on_cols_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_SCATTER]
    )
    root = ET.fromstring(xml)
    cols_el = root.find(".//table/cols")
    assert cols_el is not None
    assert "[sum:Sales:qk]" in (cols_el.text or "")


def test_scatter_y_on_rows_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_SCATTER]
    )
    root = ET.fromstring(xml)
    rows_el = root.find(".//table/rows")
    assert rows_el is not None
    assert "[sum:Profit:qk]" in (rows_el.text or "")


# ---------------------------------------------------------------------------
# C — Both measures in datasource-dependencies
# ---------------------------------------------------------------------------


def test_scatter_x_y_in_dependencies_sqlproxy() -> None:
    """Both scatter.x and scatter.y measures must appear in datasource-dependencies."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[Sales]" in col_names, f"[Sales] must be in deps; got {col_names!r}"
    assert "[Profit]" in col_names, f"[Profit] must be in deps; got {col_names!r}"


# ---------------------------------------------------------------------------
# D — Optional breakdown dimension as color encoding
# ---------------------------------------------------------------------------


def test_scatter_breakdown_emits_color_encoding() -> None:
    """scatter.breakdown must produce <color> encoding with the dimension instance."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER_WITH_BREAKDOWN])
    root = ET.fromstring(xml)
    color_el = root.find(".//pane/encodings/color")
    assert color_el is not None, (
        "scatter.breakdown must produce <encodings><color/> in the pane"
    )
    col = color_el.get("column", "")
    assert "[none:Category:nk]" in col, (
        f"breakdown color encoding must use [none:Category:nk]; got {col!r}"
    )


def test_scatter_breakdown_in_dependencies() -> None:
    """scatter.breakdown dimension must appear in datasource-dependencies."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER_WITH_BREAKDOWN])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[Category]" in col_names, (
        f"breakdown dimension [Category] must be in deps; got {col_names!r}"
    )


def test_scatter_no_breakdown_no_color_encoding() -> None:
    """A scatter without breakdown must not emit a <color> encoding."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    root = ET.fromstring(xml)
    color_el = root.find(".//pane/encodings/color")
    assert color_el is None, (
        "Scatter without breakdown must not produce <color> encoding"
    )


# ---------------------------------------------------------------------------
# E — XSD validity
# ---------------------------------------------------------------------------


def test_scatter_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER])
    _assert_xsd_valid(xml, "scatter sqlproxy")


def test_scatter_with_breakdown_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_SCATTER_WITH_BREAKDOWN])
    _assert_xsd_valid(xml, "scatter breakdown sqlproxy")


def test_scatter_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_SCATTER]
    )
    _assert_xsd_valid(xml, "scatter embedded")


def test_scatter_with_breakdown_xsd_valid_embedded(hyper_file: Path) -> None:
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "DS", hyper_file.name, columns, [SHEET_SCATTER_WITH_BREAKDOWN]
    )
    _assert_xsd_valid(xml, "scatter breakdown embedded")


# ---------------------------------------------------------------------------
# F — Regression: plain bar sheet unchanged
# ---------------------------------------------------------------------------


def test_plain_bar_not_circle() -> None:
    """A plain bar sheet must still emit <mark class='Bar'/>, not Circle."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_PLAIN_BAR])
    root = ET.fromstring(xml)
    mark = root.find(".//pane/mark")
    assert mark is not None
    assert mark.get("class") == "Bar", (
        f"Plain bar sheet must not be affected by scatter path; got {mark.get('class')!r}"
    )
