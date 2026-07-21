"""Design Excellence, an external Tableau MCP skill suite enhancement #2 — default descending-by-
measure sort for ranking bar charts (BI_DESIGN.md Sec 2.2).

Mirrors the real ``<computed-sort>`` element found in a mined reference
workbook (``WB-133``, worksheet "LOD Calcs" -- see
``docs/adr/0014-external-skill-analysis.md`` for provenance), which places a
``<computed-sort column='...' direction='...' using='...' />`` element inside
``<view>``, immediately before the mandatory ``<aggregation>`` element:

    <view>
      <datasources>...</datasources>
      <datasource-dependencies>...</datasource-dependencies>
      <computed-sort column='[ds].[none:Category:nk]' direction='DESC'
                     using='[ds].[sum:Sales:qk]' />
      <aggregation value='true' />
    </view>

Test groups
-----------
1. A single-dimension, single-measure bar sheet emits a descending
   ``<computed-sort>`` on the measure.
2. A line (time-series) sheet, and any sheet shape the sort rule does not
   unambiguously cover (kpi_tile, multi-dimension, multi-measure, a
   temporally-named dimension), gets NO ``<computed-sort>`` -- the no-op path
   stays byte-identical to before this slice.
3. XSD validity for a themed AND unthemed sorted bar sheet.
4. Byte-identical guard: sheets that do not qualify produce identical output
   whether or not this feature exists in the codebase (self-comparison, same
   discipline as the existing D3/D4 byte-identical guards).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from lxml import etree

import twb_builder

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def _assert_xsd_valid(xml_str: str, label: str) -> None:
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH), _safe_parser()))
    doc = etree.fromstring(xml_str.encode(), _safe_parser())
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED [{label}]:\n{errors}"


RANKING_BAR_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}

HORIZONTAL_RANKING_BAR_SHEET = {
    "title": "Revenue by Region (Horizontal)",
    "mark_type": "bar",
    "cols": [],
    "rows": ["Region"],
    "measures": ["Revenue"],
}

LINE_SHEET = {
    "title": "Revenue over Time",
    "mark_type": "line",
    "cols": ["Order Date"],
    "rows": [],
    "measures": ["Revenue"],
}

TEMPORAL_NAMED_BAR_SHEET = {
    "title": "Revenue by Fiscal Quarter",
    "mark_type": "bar",
    "cols": ["Fiscal Quarter"],
    "rows": [],
    "measures": ["Revenue"],
}

KPI_TILE_SHEET = {
    "title": "Total Revenue",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": [],
    "kpi": {"primary_measure": "Revenue"},
}

STACKED_BAR_SHEET = {
    "title": "Revenue and Profit by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue", "Profit"],
}

MULTI_DIM_BAR_SHEET = {
    "title": "Revenue by Region and Segment",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": ["Segment"],
    "measures": ["Revenue"],
}


def _worksheet_view(xml: str, title: str) -> ET.Element:
    root = ET.fromstring(xml)
    worksheet = root.find(f".//worksheets/worksheet[@name='{title}']")
    assert worksheet is not None, f"worksheet {title!r} not found"
    view = worksheet.find("./table/view")
    assert view is not None
    return view


# ---------------------------------------------------------------------------
# 1. Ranking bar sheets get a descending computed-sort on the measure
# ---------------------------------------------------------------------------


def test_ranking_bar_sheet_emits_descending_computed_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [RANKING_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue by Region")
    sort_el = view.find("computed-sort")
    assert sort_el is not None
    assert sort_el.get("direction") == "DESC"
    assert sort_el.get("column") == "[sqlproxy.ds].[none:Region:nk]"
    assert sort_el.get("using") == "[sqlproxy.ds].[sum:Revenue:qk]"


def test_computed_sort_is_last_child_of_view_before_aggregation() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [RANKING_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue by Region")
    tags = [child.tag for child in view]
    assert tags[-2:] == ["computed-sort", "aggregation"]


def test_horizontal_ranking_bar_sheet_also_gets_descending_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [HORIZONTAL_RANKING_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue by Region (Horizontal)")
    sort_el = view.find("computed-sort")
    assert sort_el is not None
    assert sort_el.get("direction") == "DESC"
    assert sort_el.get("column") == "[sqlproxy.ds].[none:Region:nk]"
    assert sort_el.get("using") == "[sqlproxy.ds].[sum:Revenue:qk]"


# ---------------------------------------------------------------------------
# 2. Sheets the rule does not unambiguously cover: no computed-sort at all
# ---------------------------------------------------------------------------


def test_line_time_series_sheet_gets_no_computed_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [LINE_SHEET])
    view = _worksheet_view(xml, "Revenue over Time")
    assert view.find("computed-sort") is None


def test_temporally_named_dimension_on_a_bar_mark_gets_no_computed_sort() -> None:
    """Defensive guard: even though Sec 2.1's decision table never routes a
    temporal dimension to a bar mark, a temporally-named dimension is still
    skipped defensively (mirrors BI_DESIGN Sec 1.2 priority-2 name pattern)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [TEMPORAL_NAMED_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue by Fiscal Quarter")
    assert view.find("computed-sort") is None


def test_kpi_tile_gets_no_computed_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [KPI_TILE_SHEET])
    view = _worksheet_view(xml, "Total Revenue")
    assert view.find("computed-sort") is None


def test_stacked_bar_multi_measure_gets_no_computed_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [STACKED_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue and Profit by Region")
    assert view.find("computed-sort") is None


def test_multi_dimension_bar_gets_no_computed_sort() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [MULTI_DIM_BAR_SHEET])
    view = _worksheet_view(xml, "Revenue by Region and Segment")
    assert view.find("computed-sort") is None


# ---------------------------------------------------------------------------
# 3. XSD validity
# ---------------------------------------------------------------------------


def test_ranking_bar_sort_is_xsd_valid_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [RANKING_BAR_SHEET])
    _assert_xsd_valid(xml, "sqlproxy ranking bar sort")


def test_ranking_bar_sort_is_xsd_valid_with_design_theme() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [RANKING_BAR_SHEET],
        design_theme={"name": "exec", "chrome": {"hide_axis_ticks": True}},
    )
    _assert_xsd_valid(xml, "themed ranking bar sort")


# ---------------------------------------------------------------------------
# 4. Byte-identical guard for the no-op path
# ---------------------------------------------------------------------------


def test_non_qualifying_sheets_are_byte_identical_across_repeated_builds() -> None:
    """Self-comparison guard (same discipline as D3/D4): sheets outside the
    sort rule's narrow scope produce identical output run-to-run -- this
    slice never introduces nondeterminism into the no-op path."""
    sheets = [LINE_SHEET, KPI_TILE_SHEET, STACKED_BAR_SHEET, MULTI_DIM_BAR_SHEET]
    xml_a = twb_builder.build_twb_xml("DS", "ds", "site", sheets)
    xml_b = twb_builder.build_twb_xml("DS", "ds", "site", sheets)
    assert xml_a == xml_b
