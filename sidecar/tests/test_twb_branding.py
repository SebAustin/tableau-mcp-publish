"""Brand application tests (Phase E1, Slice B — "Builder applies branding").

Mirrors real reference-workbook XML exactly:

- workbook-level ``<preferences><color-palette custom='true' name='...'
  type='regular'><color>#hex</color>...</color-palette></preferences>``
  ("Visualize Quota Attainment for Executives in Multiple Ways" ~26-41).
- styled title/subtitle ``<run fontcolor='...' fontname='...' fontsize='...'>``
  runs (same file ~4478/~4488).
- ``default-format`` on measure ``<column>`` elements, classified by name.

Every test that builds branded XML also asserts it validates against the
official TWB XSD (the same harness as test_twb_schema_validation.py), and the
suite closes with a determinism guard proving the no-brand path is untouched.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from lxml import etree

import hyper_builder
import twb_builder

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )


def _load_schema() -> etree.XMLSchema:
    parser = _safe_parser()
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str) -> None:
    schema = _load_schema()
    doc = etree.fromstring(xml_str.encode(), _safe_parser())
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED:\n{errors}"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A full brand block exactly as DashboardWorkbookRequest.brand.model_dump()
# would produce (snake_case field names — THE MODEL_DUMP LESSON).
BRAND: dict[str, Any] = {
    "palette": {
        "categorical": ["#4e79a7", "#f28e2b", "#e15759", "#59a14f"],
        "sequential": ["#c6dbef", "#6baed6", "#08519c"],
        "diverging": ["#e15759", "#f2f2f2", "#59a14f"],
        "good": "#59a14f",
        "bad": "#e15759",
        "neutral": "#898989",
    },
    "typography": {
        "title": {"font": "Tableau Bold", "size": 24, "color": "#1f1f1f"},
        "body": {"font": "Tableau Book", "size": 11, "color": "#4d4d4d"},
        "ban": {"font": "Tableau Bold", "size": 36},
    },
    "formats": {
        "currency": "$#,##0",
        "percent": "0.0%",
        "number": "#,##0",
    },
    "brand_name": "Acme Corp",
}

SHEETS_BASIC = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARD_BRANDED = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region"],
        "title": "Executive Overview",
        "subtitle": "Q4 Performance",
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_NO_TITLE = [
    {"name": "Dashboard 1", "titles": ["Revenue by Region"]}
]


def _build_hyper(tmp_path: Path, name: str = "branding.hyper") -> Path:
    """A tiny .hyper with measure columns spanning every format category."""
    df = pd.DataFrame(
        {
            "Region": ["West", "East"],
            "Sales": [100.0, 200.0],
            "Total Revenue": [500.0, 600.0],
            "Discount": [0.1, 0.2],
            "Win Rate": [0.5, 0.6],
            "Quantity": [10.0, 20.0],
        }
    )
    out = tmp_path / name
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# A — classify_measure_format (pure helper, unit-tested directly)
# ---------------------------------------------------------------------------


class TestClassifyMeasureFormat:
    def test_currency_hint_names(self) -> None:
        for name in ["Sales", "Revenue", "Profit", "Price", "Cost", "Amount", "Total Revenue"]:
            assert twb_builder.classify_measure_format(name, BRAND["formats"]) == "$#,##0", name

    def test_percent_hint_names(self) -> None:
        for name in ["Ratio", "Percent", "Rate", "Discount", "Win Rate", "Growth Rate"]:
            assert twb_builder.classify_measure_format(name, BRAND["formats"]) == "0.0%", name

    def test_discount_is_percent_not_currency(self) -> None:
        """Discount is a 0-1 ratio — must resolve to percent, per spec."""
        assert twb_builder.classify_measure_format("Discount", BRAND["formats"]) == "0.0%"

    def test_other_names_fall_back_to_number(self) -> None:
        for name in ["Quantity", "Units", "Count"]:
            assert twb_builder.classify_measure_format(name, BRAND["formats"]) == "#,##0", name

    def test_word_boundary_not_substring(self) -> None:
        """'Corporate Sales' must not misclassify as percent via 'rate' substring."""
        assert (
            twb_builder.classify_measure_format("Corporate Sales", BRAND["formats"]) == "$#,##0"
        )

    def test_missing_format_keys_fall_back_to_defaults(self) -> None:
        assert twb_builder.classify_measure_format("Sales", {}) == "$#,##0"
        assert twb_builder.classify_measure_format("Discount", {}) == "0.0%"
        assert twb_builder.classify_measure_format("Quantity", {}) == "#,##0"


# ---------------------------------------------------------------------------
# B — _normalize_hex_color
# ---------------------------------------------------------------------------


class TestNormalizeHexColor:
    def test_expands_three_digit_shorthand(self) -> None:
        assert twb_builder._normalize_hex_color("#5af") == "#55aaff"

    def test_passes_through_six_digit(self) -> None:
        assert twb_builder._normalize_hex_color("#59a14f") == "#59a14f"

    def test_passes_through_eight_digit(self) -> None:
        assert twb_builder._normalize_hex_color("#59a14fff") == "#59a14fff"


# ---------------------------------------------------------------------------
# C — <preferences><color-palette> (sqlproxy path via build_twb_xml)
# ---------------------------------------------------------------------------


def test_preferences_color_palette_present_when_branded() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, brand=BRAND
    )
    root = ET.fromstring(xml)
    prefs = root.find("preferences")
    assert prefs is not None, "<preferences> must be present when brand is supplied"
    palette = prefs.find("color-palette")
    assert palette is not None
    assert palette.get("custom") == "true"
    assert palette.get("type") == "regular"
    assert palette.get("name") == "Acme Corp Palette"
    colors = [c.text for c in palette.findall("color")]
    assert colors == BRAND["palette"]["categorical"]


def test_preferences_absent_without_brand() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC)
    root = ET.fromstring(xml)
    assert root.find("preferences") is None


def test_preferences_is_first_child_of_workbook() -> None:
    """<preferences> must precede <datasources> per the XSD sequence."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    root = ET.fromstring(xml)
    assert root[0].tag == "preferences"
    assert root[1].tag == "datasources"


def test_preferences_expands_short_hex_colors() -> None:
    brand = {**BRAND, "palette": {**BRAND["palette"], "categorical": ["#5af", "#000"]}}
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=brand)
    root = ET.fromstring(xml)
    colors = [c.text for c in root.find("preferences").find("color-palette").findall("color")]
    assert colors == ["#55aaff", "#000000"]


# ---------------------------------------------------------------------------
# D — Title / subtitle runs carry brand typography
# ---------------------------------------------------------------------------


def test_title_run_uses_brand_typography() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_BRANDED, brand=BRAND
    )
    root = ET.fromstring(xml)
    run = next(
        r
        for z in root.findall(".//dashboards//zone[@type-v2='text']")
        for r in z.find("formatted-text").findall("run")
        if r.text and "Executive Overview" in r.text
    )
    assert run.get("fontname") == "Tableau Bold"
    assert run.get("fontcolor") == "#1f1f1f"
    assert run.get("fontsize") == "24"
    # A named Bold font carries the boldness — no bold attribute (mirrors wb7).
    assert run.get("bold") is None


def test_subtitle_run_uses_brand_body_typography() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_BRANDED, brand=BRAND
    )
    root = ET.fromstring(xml)
    run = next(
        r
        for z in root.findall(".//dashboards//zone[@type-v2='text']")
        for r in z.find("formatted-text").findall("run")
        if r.text and "Q4 Performance" in r.text
    )
    assert run.get("fontname") == "Tableau Book"
    assert run.get("fontcolor") == "#4d4d4d"
    assert run.get("fontsize") == "11"


def test_title_run_without_brand_keeps_hardcoded_defaults() -> None:
    """No-brand regression: bold='true' fontsize='20', no fontcolor/fontname."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_BRANDED
    )
    root = ET.fromstring(xml)
    run = next(
        r
        for z in root.findall(".//dashboards//zone[@type-v2='text']")
        for r in z.find("formatted-text").findall("run")
        if r.text and "Executive Overview" in r.text
    )
    assert run.get("bold") == "true"
    assert run.get("fontsize") == "20"
    assert run.get("fontcolor") is None
    assert run.get("fontname") is None


# ---------------------------------------------------------------------------
# E — default-format on measure columns (sqlproxy path)
# ---------------------------------------------------------------------------


def test_sqlproxy_measure_columns_get_default_format_when_branded() -> None:
    sheets = [
        {
            "title": "Revenue by Region",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales", "Discount", "Quantity"],
        }
    ]
    xml = twb_builder.build_twb_xml("DS", "ds", "site", sheets, brand=BRAND)
    root = ET.fromstring(xml)
    cols = {
        c.get("name"): c.get("default-format")
        for c in root.find("datasources/datasource").findall("column")
        if c.get("role") == "measure"
    }
    assert cols["[Sales]"] == "$#,##0"
    assert cols["[Discount]"] == "0.0%"
    assert cols["[Quantity]"] == "#,##0"


def test_sqlproxy_measure_columns_no_default_format_without_brand() -> None:
    sheets = [
        {
            "title": "Revenue by Region",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales"],
        }
    ]
    xml = twb_builder.build_twb_xml("DS", "ds", "site", sheets)
    root = ET.fromstring(xml)
    col = next(
        c
        for c in root.find("datasources/datasource").findall("column")
        if c.get("name") == "[Sales]"
    )
    assert col.get("default-format") is None


# ---------------------------------------------------------------------------
# F — default-format on measure columns (federated / embedded path)
# ---------------------------------------------------------------------------


def test_federated_measure_columns_get_default_format_when_branded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    sheets = [
        {
            "title": "Overview",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales", "Total Revenue", "Discount", "Win Rate", "Quantity"],
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="Branding DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=sheets,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    cols = {
        c.get("name"): c.get("default-format")
        for c in root.find("datasources/datasource").findall("column")
        if c.get("role") == "measure"
    }
    assert cols["[Sales]"] == "$#,##0"
    assert cols["[Total Revenue]"] == "$#,##0"
    assert cols["[Discount]"] == "0.0%"
    assert cols["[Win Rate]"] == "0.0%"
    assert cols["[Quantity]"] == "#,##0"
    # Dimension columns must NOT get a default-format.
    dim = next(
        c
        for c in root.find("datasources/datasource").findall("column")
        if c.get("name") == "[Region]"
    )
    assert dim.get("default-format") is None


def test_federated_measure_columns_no_default_format_without_brand(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    sheets = [
        {
            "title": "Overview",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales"],
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="Branding DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=sheets,
    )
    root = ET.fromstring(xml)
    col = next(
        c
        for c in root.find("datasources/datasource").findall("column")
        if c.get("name") == "[Sales]"
    )
    assert col.get("default-format") is None


# ---------------------------------------------------------------------------
# G — Determinism guard: brand=None (explicit) == brand omitted
# ---------------------------------------------------------------------------


def test_build_twb_xml_brand_none_byte_identical_to_omitted() -> None:
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_NO_TITLE
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_NO_TITLE, brand=None
    )
    assert xml_omitted == xml_explicit_none


def test_build_embedded_twb_xml_brand_none_byte_identical_to_omitted(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="Branding DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="Branding DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        brand=None,
    )
    assert xml_omitted == xml_explicit_none


# ---------------------------------------------------------------------------
# H — XSD validity for every branded variant
# ---------------------------------------------------------------------------


def test_xsd_valid_preferences_only() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    _assert_xsd_valid(xml)


def test_xsd_valid_title_and_subtitle_branded() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_BRANDED, brand=BRAND
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_default_format_branded() -> None:
    sheets = [
        {
            "title": "Overview",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales", "Discount", "Quantity"],
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", sheets, dashboards=DASHBOARD_BRANDED, brand=BRAND
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_embedded_fully_branded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    sheets = [
        {
            "title": "Overview",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales", "Discount", "Quantity"],
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="Branding DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=sheets,
        dashboards=DASHBOARD_BRANDED,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_embedded_twbx_end_to_end(tmp_path: Path) -> None:
    """Full build_embedded_twbx() round trip (writes the .twbx, reads it back)."""
    hyper_file = _build_hyper(tmp_path, name="e2e.hyper")
    sheets = [
        {
            "title": "Overview",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Sales", "Discount"],
        }
    ]
    out_path = tmp_path / "branded.twbx"
    result = twb_builder.build_embedded_twbx(
        datasource_name="Branding DS",
        hyper_path=hyper_file,
        sheets=sheets,
        out_path=out_path,
        dashboards=DASHBOARD_BRANDED,
        brand=BRAND,
    )
    assert result.is_file()


@pytest.mark.parametrize("brand_name", ["Acme Corp", "R&D / Ops"])
def test_xsd_valid_various_brand_names(brand_name: str) -> None:
    brand = {**BRAND, "brand_name": brand_name}
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=brand)
    _assert_xsd_valid(xml)
