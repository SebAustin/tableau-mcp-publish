"""Design Excellence, Slice D4 — styled KPI tiles.

Fixes the three live-probe #1 findings on the KPI band (workbook 2527341):
(a) KPI numbers showing ``###`` (numeric overflow in ~250px tiles) — fixed by
    emitting a COMPACT number/currency format on the primary measure;
(b) KPI worksheet title labels dark-on-navy (illegible) — fixed by a
    per-worksheet ``element='title'`` style-rule using ``kpi_tile.ban_color``;
(c) KPI tiles carrying no zone-style at all (correct for D2, which
    deliberately deferred KPI-tile styling to this slice) — fixed by a themed
    ``<zone-style>`` on both the tile zones and the KPI band container.

Mined-location summary (see the D4 report for full exemplar-XML citations)
----------------------------------------------------------------------------
- Compact numbers/currency and BAN font-size/color: a per-FIELD ``<format
  attr='...' field='[ds].[column-instance]' value='...'/>`` inside the
  worksheet's own TABLE-level ``<style><style-rule element='cell'>`` — mirrors
  WB-117's ``worksheet[10]/table/style/style-rule[1]`` (``text-format
  field='[Sample - Superstore].[sum:Sales:qk]'
  value='c"$"#,##0;("$"#,##0)'``, ``font-size field='[...].[:Measure
  Names]' value='9'``) and WB-015's ``worksheet[5]/table/
  style/style-rule[2]`` (``text-format value='n#,##0,.0K;-#,##0,.0K'``,
  no ``field=`` — the blanket/table-wide variant, cited for the compact
  NUMBER pattern only). ``design/corpus/recipes/chrome_rules.yaml`` carries
  both shapes.
- Title legibility: a per-WORKSHEET (not workbook-level) ``element='title'``
  style-rule at the same TABLE-level ``<style>`` — mirrors
  ``/workbook/worksheets/worksheet[9]/table/style/style-rule[3]`` (attr=
  'color') and ``worksheet[1]/table/style/style-rule[4]`` (attr='font-family')
  in ``chrome_rules.yaml``. Deliberately NOT the workbook-level
  ``_workbook_style_rules`` rule D3 already built for ``chrome.title_color``
  — that rule is workbook-WIDE and would incorrectly recolor every chart
  worksheet's title too.
- Zone-style box model (tiles + band): the SAME mined ``<zone-style>``
  vocabulary D2 already established (``design/corpus/recipes/
  zone_styles.yaml``), now also applied to ``kpi_band_over_charts`` KPI-tile
  zones and their containing band flow zone.

Deviation from the plan's speculative BAN-styling location
------------------------------------------------------------
The plan speculated BAN typography "likely" sits on a pane-level
``style-rule element='mark'``/``'datalabel'``. Investigating the actual
exemplar XML (all 3 Superstore exemplars) shows every real BAN worksheet
instead uses a ``<customized-label>`` block with placeholder-substitution
runs (``<run fontsize='26'><![CDATA[<[ds].[usr:Calc:nk]>]]></run>``,
verified XSD-legal via ``PaneSpecification-CustomLabel-G``). This builder
DELIBERATELY does not adopt that construct: ``customized-label`` REPLACES a
mark's entire rendered label, and our KPI tiles emit MULTIPLE ``<text>``
encodings (primary/comparison/delta) on one mark — switching to a
single-run custom label would silently drop comparison/delta measures from
the visible tile, a correctness regression outside this slice's scope. The
field-scoped ``<style-rule element='cell'>`` approach above is a REAL mined
sibling construct (see citations above) that only ADDS formatting and never
replaces the mark's label composition.

Delta-color assessment (item 6)
---------------------------------
True "color the delta by sign" needs either (a) a NEW calculated boolean
field (e.g. ``[Delta] < 0``) plus the mined value-to-color ``<encoding
attr='color' type='palette'><map to='#hex'><bucket>...</bucket></map>
</encoding>`` shape (WB-133), or (b) a format-code trick. This
builder has NO calculated-field emission anywhere — adding it is a new
capability, not a styling tweak, and out of scope here. Per the plan's
explicit fallback, this slice instead ships the mined FORMAT-based arrow
pattern (``*▲ #,##;▼ #,##``, WB-117's
``default-format='*▲ #,##;▼ #,##'`` on ``[MOM - Sales (copy)_...]``) on the
delta measure's field when ``kpi_tile.use_semantic_delta_colors`` is true:
direction is visible (▲/▼), no new calc machinery, real mined XML.
``design/corpus/recipes/chrome_rules.yaml`` also independently mines the
bare ``*▲;▼`` variant (WB-114) and percent variants
(``*▲0.0%; ▼0.0%``) at the SAME ``element='cell'`` location, corroborating
the format-based arrow technique generally (not just this one field value).

Test groups
-----------
D4-1  Tile zone-style from ``kpi_tile`` (background/border/padding)
D4-2  KPI band container gets ``kpi_tile.background`` only (continuous band)
D4-3  BAN font-size/font-family/color at the mined per-field ``cell`` rule
D4-4  Compact format per classification (currency/number/percent) on primary
D4-5  Title legibility rule (per-worksheet, not workbook-wide)
D4-6  Delta arrow-format (mined fallback for color-by-sign)
D4-7  No-theme byte-identical (both entry points)
D4-8  No ``kpi_tile`` block -> tiles remain fully unstyled

D4-9 (XSD validity) and D4-10 (FastAPI integration) live in the companion
file ``test_twb_kpi_styling_integration.py`` — split at the ~800-line
file-size guideline, same discipline as Slice D3's
``test_twb_chrome.py``/``test_twb_chrome_integration.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd

import hyper_builder
import twb_builder
from server import BrandModel, DesignThemeModel

# XSD validity (D4-9) and FastAPI integration (D4-10) live in the companion
# ``test_twb_kpi_styling_integration.py`` file — see its module docstring.


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "Sales": [100.0, 200.0],
            "Sales Delta": [10.0, -5.0],
            "Discount": [0.1, 0.2],
            "Quantity": [10.0, 20.0],
            "Region": ["East", "West"],
        }
    )
    out = tmp_path / "kpi_styling.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Fixtures — sheets / dashboards / theme / brand
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
KPI_SHEET_DISCOUNT = {
    "title": "KPI Discount",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Discount"],
    "kpi": {"primary_measure": "Discount"},
}
KPI_SHEET_QUANTITY = {
    "title": "KPI Quantity",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Quantity"],
    "kpi": {"primary_measure": "Quantity"},
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Sales"],
}

SHEETS_MIXED = [KPI_SHEET_SALES, KPI_SHEET_DISCOUNT, KPI_SHEET_QUANTITY, CHART_SHEET]

DASHBOARD_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Sales", "KPI Discount", "KPI Quantity", "Revenue by Region"],
        "title": "Executive Dashboard",
        "subtitle": "Q4 2024 Performance",
        "text_zones": [],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Sales", "KPI Discount", "KPI Quantity"],
            "chart_titles": ["Revenue by Region"],
        },
    }
]


def _kpi_theme(**overrides: object) -> dict[str, object]:
    """Build a ``DesignThemeModel.model_dump()`` (snake_case) dict with a full
    ``kpi_tile`` block — constructing through the real Pydantic model keeps
    this fixture honest to the D1 wire shape (MODEL_DUMP LESSON, same
    discipline as ``test_twb_design_theme.py``'s ``_theme()``)."""
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
THEME_NO_KPI_TILE = DesignThemeModel(name="executive_dark").model_dump()  # type: ignore[arg-type]

BRAND: dict[str, Any] = BrandModel(
    palette={
        "categorical": ["#4e79a7", "#f28e2b"],
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

DS_REF = "[sqlproxy.kpi_ds]"


def _field(measure: str) -> str:
    return f"{DS_REF}.{twb_builder._measure_instance(measure)}"


def _cell_formats(worksheet: ET.Element) -> list[dict[str, str | None]]:
    rule = worksheet.find("table/style/style-rule[@element='cell']")
    if rule is None:
        return []
    return [dict(f.attrib) for f in rule.findall("format")]


# ---------------------------------------------------------------------------
# D4-1  Tile zone-style from kpi_tile (background/border/padding)
# ---------------------------------------------------------------------------


def test_kpi_tile_zone_gets_full_box_model_from_theme() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    zone_style = tile_zone.find("zone-style")
    assert zone_style is not None, "KPI tile zone must carry <zone-style> when kpi_tile is themed"
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats == {
        "background-color": "#2f2e41",
        "border-color": "#000000",
        "border-style": "none",
        "border-width": "0",
        "padding": "4",
    }


def test_kpi_tile_zone_style_is_last_child() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    assert tile_zone[-1].tag == "zone-style"


# ---------------------------------------------------------------------------
# D4-2  KPI band container gets background-only zone-style (continuous band)
# ---------------------------------------------------------------------------


def test_kpi_band_flow_gets_background_color_only() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    band = root.find(".//dashboards/dashboard/zones//zone[@h='20000'][@param='horz']")
    assert band is not None, "KPI band flow container (h=20000, param=horz) must exist"
    zone_style = band.find("zone-style")
    assert zone_style is not None
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats == {"background-color": "#2f2e41"}, (
        "Band container gets ONLY background-color (continuous band look) — "
        "no border/padding, those are per-tile"
    )
    assert band[-1].tag == "zone-style"


# ---------------------------------------------------------------------------
# D4-3  BAN font-size / font-family / color at the mined per-field cell rule
# ---------------------------------------------------------------------------


def test_ban_font_size_font_family_and_color_on_primary_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    primary_field = _field("Sales")

    font_size = next(f for f in formats if f["attr"] == "font-size")
    assert font_size["field"] == primary_field
    assert font_size["value"] == "36"

    font_family = next(f for f in formats if f["attr"] == "font-family")
    assert font_family["field"] == primary_field
    assert font_family["value"] == "Tableau Bold"

    color = next(f for f in formats if f["attr"] == "color")
    assert color["field"] == primary_field
    assert color["value"] == "#ffffff"


def test_ban_font_absent_without_brand_typography() -> None:
    """kpi_tile present but no brand -> font-size/font-family absent; color
    (from kpi_tile.ban_color, independent of brand) still present."""
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    attrs = {f["attr"] for f in formats}
    assert "font-size" not in attrs
    assert "font-family" not in attrs
    assert "color" in attrs


def test_cell_style_rule_formats_sorted_deterministic() -> None:
    xml_1 = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    xml_2 = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    assert xml_1 == xml_2
    root = ET.fromstring(xml_1)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    keys = [(f["attr"], f["field"]) for f in formats]
    assert keys == sorted(keys), f"<format> children must be deterministically sorted: {keys}"


# ---------------------------------------------------------------------------
# D4-4  Compact format per classification (currency/number/percent)
# ---------------------------------------------------------------------------


def test_compact_currency_format_on_currency_classified_primary() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    text_format = next(
        f for f in formats if f["attr"] == "text-format" and f["field"] == _field("Sales")
    )
    assert text_format["value"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


def test_compact_number_format_on_number_classified_primary() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    text_format = next(
        f for f in formats if f["attr"] == "text-format" and f["field"] == _field("Quantity")
    )
    assert text_format["value"] == "n#,##0,.0K;-#,##0,.0K"


def test_percent_format_unchanged_not_compacted() -> None:
    """Percents don't overflow a ~250px tile: the percent format is passed
    through UNCHANGED (classify_measure_format's own output), never K-suffixed."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Discount']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    text_format = next(
        f for f in formats if f["attr"] == "text-format" and f["field"] == _field("Discount")
    )
    expected = twb_builder.classify_measure_format("Discount", BRAND["formats"])
    assert text_format["value"] == expected == "0.0%"


def test_compact_currency_symbol_parameterized_from_brand() -> None:
    """The ONLY permitted parameterization: the currency SYMBOL, extracted from
    brand.formats.currency. The surrounding #,##0,.0K grammar is verbatim."""
    theme = _kpi_theme()
    brand_eur = BrandModel(
        **{**BRAND, "formats": {"currency": "€#,##0", "percent": "0.0%", "number": "#,##0"}}
    ).model_dump()  # type: ignore[arg-type]
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=theme,
        brand=brand_eur,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    text_format = next(
        f for f in formats if f["attr"] == "text-format" and f["field"] == _field("Sales")
    )
    assert text_format["value"] == 'c"€"#,##0,.0K;-"€"#,##0,.0K'


def test_compact_format_absent_without_kpi_tile_block_even_with_brand() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_NO_KPI_TILE,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert _cell_formats(worksheet) == []


# ---------------------------------------------------------------------------
# D4-5  Title legibility rule (per-worksheet, NOT workbook-wide)
# ---------------------------------------------------------------------------


def test_kpi_tile_title_color_rule_present() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    title_rule = worksheet.find("table/style/style-rule[@element='title']")
    assert title_rule is not None
    formats = {f.get("attr"): f.get("value") for f in title_rule.findall("format")}
    assert formats == {"color": "#ffffff"}


def test_chart_sheet_title_unaffected_by_kpi_tile_ban_color() -> None:
    """Scoping proof: the title rule lands on the KPI worksheet's OWN
    table-level <style> only — the chart worksheet's title must be untouched
    (unlike D3's workbook-level chrome.title_color, which would be global)."""
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    assert chart_ws.find("table/style/style-rule[@element='title']") is None
    # No workbook-level <style> either (THEME_KPI has no `chrome` block).
    assert root.find("style") is None


def test_title_color_rule_absent_when_ban_color_unset() -> None:
    theme = _kpi_theme(
        kpi_tile={
            "background": "#2f2e41",
            "border": None,
            "padding": 4,
            "ban_color": None,
            "use_semantic_delta_colors": True,
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='title']") is None


# ---------------------------------------------------------------------------
# D4-6  Delta arrow-format (mined fallback for color-by-sign)
# ---------------------------------------------------------------------------


def test_delta_arrow_format_when_semantic_delta_colors_true() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    delta_field = _field("Sales Delta")
    delta_format = next(
        f for f in formats if f["attr"] == "text-format" and f["field"] == delta_field
    )
    assert delta_format["value"] == "*▲ #,##;▼ #,##"


def test_delta_arrow_format_absent_when_semantic_delta_colors_false() -> None:
    theme = _kpi_theme(
        kpi_tile={
            "background": "#2f2e41",
            "border": None,
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": False,
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    delta_field = _field("Sales Delta")
    assert not any(f["field"] == delta_field for f in formats)


def test_delta_arrow_format_absent_when_no_delta_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    formats = _cell_formats(worksheet)
    # Only the primary measure's own formats should be present (no delta field
    # exists on this sheet's kpi spec at all).
    fields = {f["field"] for f in formats}
    assert fields == {_field("Quantity")}


# ---------------------------------------------------------------------------
# D4-7  No-theme byte-identical (both entry points)
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_build_twb_xml() -> None:
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, brand=BRAND
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        brand=BRAND,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def test_no_theme_byte_identical_build_embedded_twb_xml(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        brand=BRAND,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        brand=BRAND,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def test_no_brand_byte_identical_with_kpi_tile_theme_present() -> None:
    """kpi_tile block alone (no brand) must not change if `brand` kwarg is
    omitted vs. explicitly None — the compact-format/BAN helpers must treat
    both identically (formats.get() on an empty dict, never a crash)."""
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=None,
    )
    assert xml_omitted == xml_explicit_none


# ---------------------------------------------------------------------------
# D4-8  No kpi_tile block -> tiles remain fully unstyled
# ---------------------------------------------------------------------------


def test_no_kpi_tile_block_leaves_tiles_fully_unstyled() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_NO_KPI_TILE,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    assert tile_zone.find("zone-style") is None

    band = root.find(".//dashboards/dashboard/zones//zone[@h='20000'][@param='horz']")
    assert band is not None
    assert band.find("zone-style") is None

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='cell']") is None
    assert worksheet.find("table/style/style-rule[@element='title']") is None

