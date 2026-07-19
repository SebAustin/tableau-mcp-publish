"""Design Excellence, Slice D4 — live-probe #2b hotfix (updated for the
FINAL SHAPE, live-probe #3's bisect ladder).

Probe #2b (workbook rebuilt from commit ``a8d2483``, the D4 probe-#2
hotfix) still showed ``###`` and white (unstyled-looking) tiles. An offline
diagnostic found four concrete, mined-evidence-backed defects:

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
   Still relevant after the FINAL SHAPE change: the format now lives on the
   primary's CALCULATED column (:func:`twb_builder._append_kpi_ban_calc_column`)
   instead of the raw field's local column, but ``_kpi_compact_format``/
   ``_extract_currency_symbol`` compute the SAME value either way.
2. COMPARISON MEASURE FORMAT — REVERSED after the FINAL SHAPE bisect ladder.
   This round originally gave ``kpi.comparison_measure`` (e.g. a raw
   "Sales PP" float shown next to the primary BAN) the SAME classified
   compact format as the primary, stamped on its RAW field's worksheet-local
   ``<column>``. Live-probe #3's bisect ladder (V7) proved a RAW field's
   local ``default-format`` is silently IGNORED by Tableau Cloud for a naked
   BAN view — the comparison measure's compact format was therefore always
   dead weight, identical in kind to the (already-removed) primary/delta
   raw-field mechanism. Per the FINAL SHAPE decision, the comparison
   measure is NOT promoted to a calc column either (no mined worksheet
   stacks more than one explicit value placeholder + a secondary delta line
   in a single label; adding a THIRD calc column purely to carry a format
   nothing reads would be speculative). It now stays a plain, UNFORMATTED
   raw-field ``<text>`` encoding — see "Finding 2" below for the (inverted)
   regression tests.
3. WHITE TILES. The D2 zone-style ``background-color`` on the KPI tile
   ZONE was already correct, but the worksheet's own TABLE has an opaque
   white fill that paints on top of it. Mined verbatim from WB-118's
   real, published "Sales KPI (BAN) New" worksheet
   (WB-118.twbx):
   ``<style-rule element='table'><format attr='background-color'
   value='#00000000'/></style-rule>`` — :func:`twb_builder._kpi_tile_table_transparency_rule`
   emits this whenever ``kpi_tile.background`` is set, so the zone's navy
   background shows through. Unaffected by the FINAL SHAPE change.
4. Centered BAN text, mined verbatim from the SAME WB-118 worksheet's OWN
   ``<table><panes><pane><style>`` (a DIFFERENT XSD location from finding 3's
   table-level style): ``<style-rule element='cell'><format attr='text-align'
   value='center'/></style-rule>``. :func:`twb_builder._kpi_tile_pane_style_rules`
   emits this whenever ``design_theme.kpi_tile`` is present — and, per the
   FINAL SHAPE change, now ALSO emits a SECOND ``element='mark'`` rule
   (``mark-labels-show``/``mark-labels-cull``) whenever the BAN label
   mechanism is active (see that function's docstring for the bisect
   provenance — V9/V10 proved this rule is REQUIRED for the label to render
   at all).

Tightening discipline (per the coordinator's explicit ask): the currency
regression test asserts the DECODED attribute value against the literal
mined target string, not a re-derivation via the same helper the
implementation uses — a double-quoting regression would otherwise pass a
test that only checks internal self-consistency.

See ``test_twb_kpi_styling.py``'s module docstring for the D4 baseline and
FINAL SHAPE summary, and ``test_twb_kpi_styling_customized_label.py`` for
the customized-label mechanism's full run-by-run coverage.
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


def _calc_field_name(field: str, *, delta: bool = False) -> str:
    suffix = "_Delta" if delta else ""
    return f"[{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug(field)}{suffix}]"


def _calc_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    prefix = f"[{twb_builder._KPI_BAN_CALC_PREFIX}"
    return {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
        if (c.get("name") or "").startswith(prefix)
    }


def _local_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    return {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }


# ---------------------------------------------------------------------------
# Finding 1 — double-quoting bug (the ### root cause), now on the calc column
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
    calc_formats = _calc_default_formats(worksheet)
    value = calc_formats[_calc_field_name("Sales")]
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
    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Sales")] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


# ---------------------------------------------------------------------------
# Finding 2 — comparison measure: REVERSED under the FINAL SHAPE.
#
# The round-2 hotfix gave the comparison measure the same compact format as
# the primary, stamped on its RAW field's local column. Live-probe #3's
# bisect ladder (V7) proved a raw field's local default-format is silently
# ignored by Cloud for a naked BAN view — the comparison measure's compact
# format was ALWAYS dead weight (identical mechanism to the one already
# proven inert for primary/delta). It is no longer emitted: the comparison
# measure stays a plain, unformatted raw-field <text> encoding.
# ---------------------------------------------------------------------------


def test_comparison_measure_gets_no_local_default_format_dead_weight_removed() -> None:
    """FINAL SHAPE reversal: the comparison measure's raw local column never
    gets a default-format — that mechanism was proven inert (bisect V7) and
    is not replicated for comparison (no calc column either, since no mined
    worksheet stacks a comparison value inside the same label as the
    primary/delta)."""
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
    assert local_formats.get("[Sales PP]") is None
    # No calc column for the comparison measure either.
    calc_formats = _calc_default_formats(worksheet)
    assert _calc_field_name("Sales PP") not in calc_formats
    # The comparison measure's <text> encoding stays a plain raw-field ref.
    encoded_columns = [t.get("column") for t in worksheet.findall(".//panes/pane/encodings/text")]
    assert any(c is not None and c.endswith(".[sum:Sales PP:qk]") for c in encoded_columns)


def test_comparison_measure_absent_when_not_set() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    # KPI_SHEET_SALES has no comparison_measure — only primary + delta calc
    # columns exist (both compact/arrow-formatted).
    calc_formats = _calc_default_formats(worksheet)
    assert set(calc_formats) == {
        _calc_field_name("Sales"),
        _calc_field_name("Sales Delta", delta=True),
    }


def test_comparison_measure_percent_unformatted_regardless_of_classification() -> None:
    """A percent-hinted comparison measure gets no format either — the
    dead-weight removal applies uniformly, independent of classification."""
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
    assert local_formats.get("[Discount]") is None


# ---------------------------------------------------------------------------
# Finding 3 — white tiles (transparent table background); unaffected by the
# FINAL SHAPE change
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
# Finding 4 — centered BAN text + (FINAL SHAPE) mark-labels-show/cull
# ---------------------------------------------------------------------------


def test_kpi_tile_pane_text_align_center_and_mark_labels_rule() -> None:
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

    # Design Excellence, Slice D4 FINAL SHAPE (live-probe #3's V9-V12): a
    # SECOND style-rule, element='mark', is REQUIRED for the
    # <customized-label> to render at all.
    pane_mark_rule = pane.find("style/style-rule[@element='mark']")
    assert pane_mark_rule is not None
    mark_formats = {f.get("attr"): f.get("value") for f in pane_mark_rule.findall("format")}
    assert mark_formats == {"mark-labels-show": "true", "mark-labels-cull": "true"}

    # MUST be the last child of <pane> (Stylesheet-G ordering).
    assert pane[-1].tag == "style"
    # selection-relaxation-option is set on the pane itself (mined fidelity).
    assert pane.get("selection-relaxation-option") == "selection-relaxation-allow"


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
    assert pane.get("selection-relaxation-option") is None


def test_chart_sheet_pane_style_unaffected_by_kpi_tile_theme() -> None:
    """Scoping proof: the pane-level text-align/mark-labels rules are
    elif-gated against _is_labelable_chart_sheet — a bar chart still gets
    ITS OWN D3 pane style-rules (mark-labels/datalabel), not the KPI tile's."""
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
    # the key assertion is that text-align/mark-labels never leak onto a
    # chart sheet.
    pane_style = pane.find("style")
    if pane_style is not None:
        attrs = {f.get("attr") for f in pane_style.findall(".//format")}
        assert "text-align" not in attrs
        assert "mark-labels-show" not in attrs


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
