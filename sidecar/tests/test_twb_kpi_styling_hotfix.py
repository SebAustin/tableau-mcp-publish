"""Design Excellence, Slice D4 — live-probe #2b hotfix.

Probe #2b (workbook rebuilt from commit ``a8d2483``, the D4 probe-#2
hotfix) still showed ``###`` and white (unstyled-looking) tiles. An offline
diagnostic found four concrete, mined-evidence-backed defects, all fixed
here:

1. DOUBLE-QUOTING BUG (the ``###`` root cause). ``_extract_currency_symbol``'s
   prefix regex (``^[^#0-9]*``) does not stop at a literal ``"`` — when
   ``brand.formats.currency`` is ALREADY in Tableau-native quoted form
   (e.g. ``'"$"#,##0'``, matching real mined ``default-format`` values like
   ``c"$"#,##0;-"$"#,##0``), the quote characters were captured as part of
   the "symbol" and then wrapped in a SECOND pair by
   ``_compact_currency_format`` — producing ``c""$""#,##0,.0K;...``, which
   Tableau cannot parse and silently falls back on, leaving the tile
   un-compacted (``###`` in a ~250px tile). Fixed by stripping any
   pre-existing ``"`` from the extracted prefix before re-wrapping —
   :func:`twb_builder._extract_currency_symbol` now always produces exactly
   ONE quote pair, regardless of which convention the input already used.
2. COMPARISON MEASURE UNFORMATTED. ``kpi.comparison_measure`` (e.g. a raw
   "Sales PP" float shown next to the primary BAN) got NO
   ``default-format`` at all — :func:`twb_builder._kpi_tile_local_default_formats`
   only ever handled ``primary_measure``/``delta_measure``. Fixed: the
   comparison measure now gets the SAME classified compact format as the
   primary, unconditionally (independent of ``use_semantic_delta_colors``,
   which only governs the delta's arrow-direction format).
3. WHITE TILES. The D2 zone-style ``background-color`` on the KPI tile
   ZONE was already correct, but the worksheet's own TABLE has an opaque
   white fill that paints on top of it. Mined verbatim from WB-118's
   real, published "Sales KPI (BAN) New" worksheet
   (WB-118.twbx):
   ``<style-rule element='table'><format attr='background-color'
   value='#00000000'/></style-rule>`` — :func:`twb_builder._kpi_tile_table_transparency_rule`
   emits this whenever ``kpi_tile.background`` is set, so the zone's navy
   background shows through.
4. OPTIONAL — centered BAN text, mined verbatim from the SAME WB-118
   worksheet's OWN ``<table><panes><pane><style>`` (a DIFFERENT XSD
   location from finding 3's table-level style):
   ``<style-rule element='cell'><format attr='text-align'
   value='center'/></style-rule>``. :func:`twb_builder._kpi_tile_pane_style_rules`
   emits this whenever ``design_theme.kpi_tile`` is present.

Tightening discipline (per the coordinator's explicit ask): the currency
regression test asserts the DECODED attribute value against the literal
mined target string, not a re-derivation via the same helper the
implementation uses — a double-quoting regression would otherwise pass a
test that only checks internal self-consistency.

See ``test_twb_kpi_styling.py``'s module docstring for the D4 baseline
(zone-style/BAN-typography/title-legibility/delta-arrow mechanics this
hotfix builds on) and ``test_twb_kpi_styling_integration.py`` for XSD +
FastAPI integration coverage.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import twb_builder
from server import BrandModel, DesignThemeModel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

KPI_SHEET_SALES = {
    "title": "KPI Sales",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales"],
    "kpi": {"primary_measure": "Sales", "delta_measure": "Sales Delta"},
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Sales"],
}
SHEETS_BASIC = [KPI_SHEET_SALES, CHART_SHEET]

DASHBOARD_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Sales", "Revenue by Region"],
        "title": "Executive Dashboard",
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Sales"],
            "chart_titles": ["Revenue by Region"],
        },
    }
]


def _kpi_theme(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "executive_dark",
        "kpi_tile": {
            "background": "#2f2e41",
            "border": {"color": "#000000", "style": "none", "width": 0},
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": True,
        },
    }
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_KPI = _kpi_theme()

BRAND: dict[str, Any] = BrandModel(
    palette={
        "categorical": ["#4e79a7"],
        "sequential": [],
        "diverging": [],
        "good": "#59a14f",
        "bad": "#e15759",
        "neutral": "#898989",
    },
    typography={
        "title": {"font": "Tableau Bold", "size": 24, "color": "#1f1f1f"},
        "body": {"font": "Tableau Book", "size": 11, "color": "#4d4d4d"},
        "ban": {"font": "Tableau Bold", "size": 36},
    },
    formats={"currency": "$#,##0", "percent": "0.0%", "number": "#,##0"},
    brand_name="Acme Corp",
).model_dump()  # type: ignore[arg-type]


def _local_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    return {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }


# ---------------------------------------------------------------------------
# Finding 1 — double-quoting bug (the ### root cause)
# ---------------------------------------------------------------------------


def test_compact_currency_strips_pre_existing_quotes_no_double_quoting() -> None:
    """brand.formats.currency ALREADY in Tableau-native quoted form
    (e.g. '"$"#,##0', matching real mined default-format values) must still
    produce exactly ONE quote pair — the literal mined target pattern,
    asserted here directly against the string constant (NOT re-derived via
    _extract_currency_symbol/_compact_currency_format), so a regression
    that reintroduces double-quoting fails this assertion instead of
    silently passing a self-consistent-but-wrong value."""
    brand_quoted = BrandModel(
        **{**BRAND, "formats": {"currency": '"$"#,##0', "percent": "0.0%", "number": "#,##0"}}
    ).model_dump()  # type: ignore[arg-type]
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=brand_quoted,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    value = local_formats["[Sales]"]
    assert value == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert '""' not in (value or ""), f"double-quoting regression: {value!r}"


def test_compact_currency_unquoted_input_unaffected() -> None:
    """Non-regression: the ORDINARY unquoted brand.yaml convention
    ("$#,##0", no embedded quote characters) must be unaffected by the
    quote-stripping fix — same output as before this hotfix."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Sales]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


# ---------------------------------------------------------------------------
# Finding 2 — comparison measure unformatted
# ---------------------------------------------------------------------------


def test_comparison_measure_gets_compact_format_too() -> None:
    """The comparison measure (e.g. "Sales PP", shown alongside the primary
    BAN) must get the same classified compact format as the primary,
    unconditionally — independent of use_semantic_delta_colors, which only
    governs the DELTA's arrow-direction format."""
    sheet = {
        "title": "KPI Sales Comparison",
        "mark_type": "text",
        "kind": "kpi_tile",
        "cols": [],
        "rows": [],
        "measures": ["Sales", "Sales PP"],
        "kpi": {"primary_measure": "Sales", "comparison_measure": "Sales PP"},
    }
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Sales Comparison"],
            "title": "Executive",
            "subtitle": None,
            "text_zones": [],
            "layout_grammar": {
                "kind": "kpi_band_over_charts",
                "kpi_tile_titles": ["KPI Sales Comparison"],
                "chart_titles": [],
            },
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", [sheet], dashboards=dashboard, design_theme=THEME_KPI, brand=BRAND
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales Comparison']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Sales]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert local_formats["[Sales PP]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


def test_comparison_measure_absent_when_not_set() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    # KPI_SHEET_SALES has no comparison_measure — only primary + delta keys.
    local_formats = _local_default_formats(worksheet)
    assert set(local_formats) == {"[Sales]", "[Sales Delta]"}


def test_comparison_measure_percent_classified_unchanged() -> None:
    """A percent-hinted comparison measure follows the SAME classification
    rule as the primary — unchanged (not K-suffixed)."""
    sheet = {
        "title": "KPI Discount Comparison",
        "mark_type": "text",
        "kind": "kpi_tile",
        "cols": [],
        "rows": [],
        "measures": ["Sales", "Discount"],
        "kpi": {"primary_measure": "Sales", "comparison_measure": "Discount"},
    }
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Discount Comparison"],
            "title": "Executive",
            "subtitle": None,
            "text_zones": [],
            "layout_grammar": {
                "kind": "kpi_band_over_charts",
                "kpi_tile_titles": ["KPI Discount Comparison"],
                "chart_titles": [],
            },
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", [sheet], dashboards=dashboard, design_theme=THEME_KPI, brand=BRAND
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Discount Comparison']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Discount]"] == "0.0%"


# ---------------------------------------------------------------------------
# Finding 3 — white tiles (transparent table background)
# ---------------------------------------------------------------------------


def test_kpi_tile_table_background_transparent_when_background_set() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    table_rule = worksheet.find("table/style/style-rule[@element='table']")
    assert table_rule is not None
    formats = {f.get("attr"): f.get("value") for f in table_rule.findall("format")}
    assert formats == {"background-color": "#00000000"}


def test_kpi_tile_table_transparency_absent_when_background_unset() -> None:
    theme = _kpi_theme(
        kpi_tile={
            "background": None,
            "border": None,
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": True,
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='table']") is None


def test_chart_sheet_table_background_unaffected() -> None:
    """Scoping proof: only kpi_tile worksheets get the transparency rule —
    the chart worksheet's table-level style must be untouched."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    assert chart_ws.find("table/style/style-rule[@element='table']") is None


# ---------------------------------------------------------------------------
# Finding 4 — optional: centered BAN text (pane-scoped, mined verbatim)
# ---------------------------------------------------------------------------


def test_kpi_tile_pane_text_align_center() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    pane_cell_rule = pane.find("style/style-rule[@element='cell']")
    assert pane_cell_rule is not None
    formats = {f.get("attr"): f.get("value") for f in pane_cell_rule.findall("format")}
    assert formats == {"text-align": "center"}
    # MUST be the last child of <pane> (Stylesheet-G ordering).
    assert pane[-1].tag == "style"


def test_kpi_tile_pane_style_absent_without_kpi_tile_block() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=DesignThemeModel(name="executive_dark").model_dump(),  # type: ignore[arg-type]
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    assert pane.find("style") is None


def test_chart_sheet_pane_style_unaffected_by_kpi_tile_theme() -> None:
    """Scoping proof: the pane-level text-align rule is elif-gated against
    _is_labelable_chart_sheet — a bar chart still gets ITS OWN D3 pane
    style-rules (mark-labels/datalabel), not the KPI tile's text-align."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    pane = chart_ws.find(".//panes/pane")
    assert pane is not None
    # No design_theme.chrome set on THEME_KPI -> no D3 pane style either;
    # the key assertion is that text-align never leaks onto a chart sheet.
    pane_style = pane.find("style")
    if pane_style is not None:
        attrs = {f.get("attr") for f in pane_style.findall(".//format")}
        assert "text-align" not in attrs


# ---------------------------------------------------------------------------
# Cross-cutting: no-theme / no-kpi_tile-block byte-identical guards still hold
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_with_all_four_fixes_present() -> None:
    """All four fixes are strictly additive and design_theme-gated — a build
    with NO design_theme at all must stay byte-identical."""
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, brand=BRAND
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        brand=BRAND,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none
