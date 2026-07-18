"""Design Excellence, Slice D3 — chrome rules + mark labels.

Mirrors the mined vocabulary in ``design/corpus/recipes/chrome_rules.yaml``:

- Workbook-level ``<style><style-rule element='gridline'|'zeroline'>`` with a
  plain ``line-visibility='off'`` format — mirrors ``WB-062``
  ``/workbook/style/style-rule[2]`` (gridline) and ``WB-095``
  ``/workbook/style/style-rule[2]`` (zeroline). Emitted right after
  ``<preferences>`` (or as the first child when unbranded), before
  ``<datasources>`` — the XSD's ``Workbook-Preferences-G`` ->
  ``Workbook-StyleTheme-G`` -> ``Workbook-Styles-G`` -> ... ->
  ``Workbook-DataSources-G`` sequence.
- Worksheet TABLE-level ``<style><style-rule element='axis'>`` with
  ``line-visibility='off'`` + ``tick-color='#00000000'`` — mirrors
  WB-117's ``WB-117.twbx``
  ``worksheet[10]/table/style/style-rule[1]`` EXACTLY (including the
  ``#00000000`` transparent tick color).
- Worksheet PANE-level ``<style>`` carrying ``datalabel`` THEN ``mark``
  ``<style-rule>`` children — mirrors the SAME source's
  ``worksheet[10]/table/panes/pane/style/style-rule[1..2]`` (datalabel before
  mark) and WB-114's ``worksheet[15]`` (datalabel style-rule[2],
  mark style-rule[3]). The mined xpath for BOTH ``mark`` and ``datalabel``
  rules is ``.../table/panes/pane/style/style-rule`` — i.e. pane-level, NOT
  table-level (the PLAN.md brief's explicit call-out).
- ``SheetModel.style_rules`` pass-through: an arbitrary ``{element, formats}``
  escape hatch appended to the TABLE-level ``<style>`` (after the theme-driven
  axis-tick rule), sorted by ``element`` name for determinism.

Mark-labels/datalabel scoping decision (documented per the D3 brief's
explicit fallback instruction): ``design/corpus/recipes/chrome_rules.yaml``
mined evidence for ``mark-labels-show`` on MAP worksheets is genuinely mixed
(some map layers carry ``mark-labels-show='true'``, others ``'false'`` across
different reference workbooks — see the D3 implementation report). Per the
brief's explicit fallback ("if ambiguous, apply to bar/line only — Few
discipline: labels on bars, not on maps"), this slice restricts
``chrome.show_mark_labels`` / ``chrome.datalabel`` styling to ``kind='chart'``
sheets whose ``mark_type`` is ``bar`` or ``line`` — NOT ``kpi_tile`` sheets
(their ``<text>`` marks are not "labels" in this sense; KPI styling lands in
Slice D4) and NOT ``map_filled`` sheets.

Test groups
-----------
D3-1  No-theme byte-identical guards (``build_twb_xml`` / ``build_embedded_twb_xml``)
D3-2  Workbook-level gridline/zeroline style-rules when themed
D3-3  Workbook <style> absent when design_theme has no chrome
D3-4  Worksheet axis tick-color style-rule (table-level)
D3-5  Pane-level mark-labels-show on bar/line charts
D3-6  Mark labels/datalabel NOT emitted on KPI tiles or filled maps
D3-7  Datalabel style-rule formats (font-size/font-weight/color-mode + defensive color)
D3-8  SheetModel.style_rules pass-through, verbatim + deterministic order
D3-9  Deterministic <style-rule>/<format> ordering
D3-10 XSD validity (workbook-level + worksheet-level chrome, sqlproxy + embedded)

D3-11 (FastAPI integration) and D3-12 (defensive chrome.title_color) live in
the sibling ``test_twb_chrome_integration.py`` — split out to keep both files
under the repo's ~800-line-per-file guideline.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree

import hyper_builder
import twb_builder
from server import DesignThemeModel

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_twb_design_theme.py's pattern)
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _load_schema() -> etree.XMLSchema:
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str) -> None:
    schema = _load_schema()
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED:\n{errors}"


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "Region": ["East", "West"],
            "Revenue": [1.0, 2.0],
            "Quarter": ["Q1", "Q2"],
            "Profit": [1.0, 2.0],
            "State": ["California", "Texas"],
        }
    )
    out = tmp_path / "chrome.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Fixtures — sheets / dashboards / theme dicts
# ---------------------------------------------------------------------------

KPI_SHEET = {
    "title": "KPI Revenue",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Revenue"],
    "kpi": {"primary_measure": "Revenue"},
}
CHART_SHEET_BAR = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}
CHART_SHEET_LINE = {
    "title": "Profit Trend",
    "mark_type": "line",
    "cols": ["Quarter"],
    "rows": [],
    "measures": ["Profit"],
}
MAP_SHEET = {
    "title": "Sales by State",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {"geo_field": "State", "geo_role": "state", "color_measure": "Revenue"},
}

SHEETS_BASIC = [CHART_SHEET_BAR, CHART_SHEET_LINE]
SHEETS_MIXED = [KPI_SHEET, CHART_SHEET_BAR, MAP_SHEET]

DASHBOARD_PLAIN = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region", "Profit Trend"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]
DASHBOARD_MIXED = [
    {
        "name": "Dashboard 1",
        "titles": ["KPI Revenue", "Revenue by Region", "Sales by State"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]


def _theme(**overrides: object) -> dict[str, object]:
    """Build a ``DesignThemeModel.model_dump()`` (snake_case) dict.

    Constructed through the real Pydantic model — same discipline as
    ``test_twb_design_theme.py``'s ``_theme()``.
    """
    base: dict[str, object] = {"name": "executive_dark"}
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_CHROME_FULL = _theme(
    chrome={
        "hide_gridlines": True,
        "hide_zeroline": True,
        "hide_axis_ticks": True,
        "show_mark_labels": True,
        "datalabel": {"font_size": 11, "font_weight": "bold", "color_mode": "auto"},
    }
)
THEME_NO_CHROME = _theme(dashboard_background="#2f2e41")


def _raw_theme(chrome: dict[str, Any]) -> dict[str, Any]:
    """A hand-built (non-Pydantic) design_theme dict.

    Used ONLY for the defensive ``.get()`` reads that Slice D1's
    ``ThemeChromeModel``/``ThemeDatalabelModel`` don't declare yet
    (``chrome.title_color``, ``chrome.datalabel.color``) — see the D3-7
    defensive-color case (title_color itself is exercised in the sibling
    ``test_twb_chrome_integration.py``). ``twb_builder`` accepts any
    ``dict[str, Any]`` for ``design_theme`` (it is never validated against
    the Pydantic model internally), so this is a legitimate way to exercise
    builder-level behaviour that isn't reachable through the current wire
    schema.
    """
    return {"name": "raw", "chrome": chrome}


# ---------------------------------------------------------------------------
# D3-1  No-theme byte-identical guards
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_build_twb_xml() -> None:
    xml_pre_d3 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN
    )
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=None
    )
    assert xml_pre_d3 == xml_omitted == xml_explicit_none


def test_no_theme_byte_identical_build_embedded_twb_xml(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)

    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def test_no_chrome_theme_produces_no_style_rules_anywhere() -> None:
    """A design_theme WITHOUT a chrome block must not add any <style-rule>
    at workbook OR worksheet level (only D2's zone-style constructs apply)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_NO_CHROME
    )
    root = ET.fromstring(xml)
    assert root.find("style") is None
    for worksheet in root.findall(".//worksheets/worksheet"):
        table_style = worksheet.find("table/style")
        assert table_style is not None  # required, but must be empty
        assert len(table_style) == 0
        pane_style = worksheet.find("table/panes/pane/style")
        assert pane_style is None


# ---------------------------------------------------------------------------
# D3-2  Workbook-level gridline/zeroline style-rules when themed
# ---------------------------------------------------------------------------


def test_workbook_style_rules_gridline_zeroline_when_themed() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    style = root.find("style")
    assert style is not None, "Workbook-level <style> must be present when chrome is themed"
    rules = {r.get("element"): r for r in style.findall("style-rule")}
    assert set(rules) >= {"gridline", "zeroline"}
    gridline_formats = {f.get("attr"): f.get("value") for f in rules["gridline"].findall("format")}
    assert gridline_formats == {"line-visibility": "off"}
    zeroline_formats = {f.get("attr"): f.get("value") for f in rules["zeroline"].findall("format")}
    assert zeroline_formats == {"line-visibility": "off"}


def test_workbook_style_is_positioned_after_preferences_before_datasources() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    child_tags = [c.tag for c in root]
    assert child_tags.index("style") < child_tags.index("datasources")


def test_workbook_style_rules_gridline_zeroline_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    style = root.find("style")
    assert style is not None
    rules = {r.get("element") for r in style.findall("style-rule")}
    assert rules >= {"gridline", "zeroline"}


# ---------------------------------------------------------------------------
# D3-3  Workbook <style> absent when design_theme has no chrome
# ---------------------------------------------------------------------------


def test_workbook_style_absent_without_chrome() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_NO_CHROME
    )
    root = ET.fromstring(xml)
    assert root.find("style") is None


def test_workbook_style_absent_when_chrome_flags_all_false() -> None:
    theme = _theme(chrome={"hide_gridlines": False, "hide_zeroline": False})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    assert root.find("style") is None


# ---------------------------------------------------------------------------
# D3-4  Worksheet axis tick-color style-rule (table-level)
# ---------------------------------------------------------------------------


def test_worksheet_axis_tick_transparent() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    table_style = worksheet.find("table/style")
    assert table_style is not None
    axis_rule = table_style.find("style-rule[@element='axis']")
    assert axis_rule is not None
    formats = {f.get("attr"): f.get("value") for f in axis_rule.findall("format")}
    assert formats == {"line-visibility": "off", "tick-color": "#00000000"}


def test_worksheet_table_style_bare_when_no_axis_tick_theme() -> None:
    """hide_axis_ticks=False (default) -> table-level <style> stays empty."""
    theme = _theme(chrome={"hide_gridlines": True})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    table_style = worksheet.find("table/style")
    assert table_style is not None
    assert len(table_style) == 0


def test_axis_tick_rule_applies_even_to_kpi_and_map_sheets() -> None:
    """hide_axis_ticks is a blanket chrome setting — unlike mark-labels, it is
    NOT scoped to bar/line charts (harmless no-op on sheets with no visible
    axis, e.g. KPI tiles)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    for title in ("KPI Revenue", "Sales by State"):
        worksheet = root.find(f".//worksheets/worksheet[@name='{title}']")
        assert worksheet is not None
        axis_rule = worksheet.find("table/style/style-rule[@element='axis']")
        assert axis_rule is not None, f"{title} must still carry the axis tick-color rule"


# ---------------------------------------------------------------------------
# D3-5  Pane-level mark-labels-show on bar/line charts
# ---------------------------------------------------------------------------


def test_pane_mark_labels_show_on_bar_chart() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    pane = worksheet.find("table/panes/pane")
    assert pane is not None
    # Pane-level <style> must be the LAST child of <pane> (Stylesheet-G ordering).
    assert pane[-1].tag == "style"
    mark_rule = pane.find("style/style-rule[@element='mark']")
    assert mark_rule is not None
    formats = {f.get("attr"): f.get("value") for f in mark_rule.findall("format")}
    assert formats == {"mark-labels-show": "true", "mark-labels-cull": "false"}


def test_pane_mark_labels_absent_when_show_mark_labels_false() -> None:
    theme = _theme(chrome={"hide_gridlines": True, "show_mark_labels": False})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    assert worksheet.find("table/panes/pane/style") is None


# ---------------------------------------------------------------------------
# D3-6  Mark labels/datalabel NOT emitted on KPI tiles or filled maps
# ---------------------------------------------------------------------------


def test_mark_labels_not_on_kpi_tiles() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    kpi_worksheet = root.find(".//worksheets/worksheet[@name='KPI Revenue']")
    assert kpi_worksheet is not None
    assert kpi_worksheet.find("table/panes/pane/style") is None, (
        "KPI-tile panes must NOT carry a mark-labels/datalabel <style>"
    )


def test_mark_labels_not_on_filled_maps() -> None:
    """Map worksheets have TWO <pane> elements (id=1 shadow, id=0 fill); NEITHER
    carries a mark-labels/datalabel pane-level <style> — mined chrome_rules.yaml
    evidence for mark-labels-show on map layers is mixed across exemplars, so
    per the D3 brief's explicit fallback this slice scopes theme-driven labels
    to bar/line chart sheets only (Few discipline: labels on bars, not maps)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    map_worksheet = root.find(".//worksheets/worksheet[@name='Sales by State']")
    assert map_worksheet is not None
    panes = map_worksheet.findall("table/panes/pane")
    assert len(panes) == 2
    for pane in panes:
        assert pane.find("style") is None


def test_mark_labels_present_on_line_chart_too() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Profit Trend']")
    assert worksheet is not None
    assert worksheet.find("table/panes/pane/style/style-rule[@element='mark']") is not None


# ---------------------------------------------------------------------------
# D3-7  Datalabel style-rule formats
# ---------------------------------------------------------------------------


def test_datalabel_style_rule_formats() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    datalabel_rule = worksheet.find("table/panes/pane/style/style-rule[@element='datalabel']")
    assert datalabel_rule is not None
    formats = {f.get("attr"): f.get("value") for f in datalabel_rule.findall("format")}
    assert formats == {"font-size": "11", "font-weight": "bold", "color-mode": "auto"}


def test_datalabel_before_mark_in_pane_style() -> None:
    """Mirrors WB-117 worksheet[10]/[15]: datalabel style-rule precedes
    mark style-rule within the SAME <pane><style> block."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    style = worksheet.find("table/panes/pane/style")
    assert style is not None
    elements = [r.get("element") for r in style.findall("style-rule")]
    assert elements == ["datalabel", "mark"]


def test_datalabel_color_read_defensively_when_present() -> None:
    """chrome.datalabel.color is NOT declared by Slice D1's ThemeDatalabelModel
    (font_size/font_weight/color_mode only); the builder reads it defensively
    via .get() so a raw theme dict CAN carry it, but it is unreachable through
    the current Pydantic/zod wire schema (documented deferral)."""
    theme = _raw_theme(
        {
            "show_mark_labels": True,
            "datalabel": {"color_mode": "manual", "color": "#ffffff"},
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    datalabel_rule = worksheet.find("table/panes/pane/style/style-rule[@element='datalabel']")
    assert datalabel_rule is not None
    formats = {f.get("attr"): f.get("value") for f in datalabel_rule.findall("format")}
    assert formats == {"color-mode": "manual", "color": "#ffffff"}


# ---------------------------------------------------------------------------
# D3-8  SheetModel.style_rules pass-through, verbatim + deterministic order
# ---------------------------------------------------------------------------


def test_sheet_style_rules_passthrough_verbatim() -> None:
    sheet = {
        **CHART_SHEET_BAR,
        "style_rules": [
            {"element": "cell", "formats": {"width": "183", "text-align": "center"}},
        ],
    }
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [sheet, CHART_SHEET_LINE], dashboards=DASHBOARD_PLAIN
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    table_style = worksheet.find("table/style")
    assert table_style is not None
    cell_rule = table_style.find("style-rule[@element='cell']")
    assert cell_rule is not None
    formats = {f.get("attr"): f.get("value") for f in cell_rule.findall("format")}
    assert formats == {"width": "183", "text-align": "center"}


def test_sheet_style_rules_work_without_any_design_theme() -> None:
    """style_rules is a per-sheet escape hatch independent of design_theme."""
    sheet = {
        **CHART_SHEET_BAR,
        "style_rules": [{"element": "header", "formats": {"border-width": "0"}}],
    }
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [sheet], dashboards=None)
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='header']") is not None


def test_sheet_style_rules_after_theme_axis_rule_sorted_by_element() -> None:
    sheet = {
        **CHART_SHEET_BAR,
        "style_rules": [
            {"element": "header", "formats": {"border-width": "0"}},
            {"element": "cell", "formats": {"width": "100"}},
        ],
    }
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [sheet, CHART_SHEET_LINE], dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    table_style = worksheet.find("table/style")
    assert table_style is not None
    elements = [r.get("element") for r in table_style.findall("style-rule")]
    # theme-driven axis rule first, then sheet.style_rules sorted alphabetically.
    assert elements == ["axis", "cell", "header"]


# ---------------------------------------------------------------------------
# D3-9  Deterministic <style-rule>/<format> ordering
# ---------------------------------------------------------------------------


def test_workbook_style_rules_sorted_by_element() -> None:
    xml_1 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_CHROME_FULL
    )
    xml_2 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_CHROME_FULL
    )
    assert xml_1 == xml_2
    root = ET.fromstring(xml_1)
    style = root.find("style")
    assert style is not None
    elements = [r.get("element") for r in style.findall("style-rule")]
    assert elements == sorted(elements)


def test_format_children_sorted_alphabetically_within_style_rule() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_CHROME_FULL
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    axis_rule = worksheet.find("table/style/style-rule[@element='axis']")
    assert axis_rule is not None
    attrs = [f.get("attr") for f in axis_rule.findall("format")]
    assert attrs == sorted(attrs)


# ---------------------------------------------------------------------------
# D3-10  XSD validity
# ---------------------------------------------------------------------------


def test_xsd_valid_workbook_style() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=THEME_CHROME_FULL,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_worksheet_chrome() -> None:
    sheet = {
        **CHART_SHEET_BAR,
        "style_rules": [{"element": "cell", "formats": {"width": "183"}}],
    }
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [sheet, CHART_SHEET_LINE], dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_workbook_style_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=THEME_CHROME_FULL,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_worksheet_chrome_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    sheet = {
        **CHART_SHEET_BAR,
        "style_rules": [{"element": "cell", "formats": {"width": "183"}}],
    }
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=[sheet, CHART_SHEET_LINE],
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    _assert_xsd_valid(xml)
