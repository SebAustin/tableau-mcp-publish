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

BAN-styling location — REVISED after live probe #3
------------------------------------------------------------
D4's original assessment (see git history) speculated then rejected a
``<customized-label>`` approach, believing it would REPLACE a mark's
entire rendered label and drop comparison/delta measures from view. Live
probe #3's fresh renders proved the TABLE-level ``element='cell'``
font-size/font-family/color rule that assessment led to renders NOTHING
for a naked BAN view. A direct diff against WB-118's real, published
"Sales KPI (BAN) New" worksheet (WB-118.twbx)
showed the earlier assessment was wrong on the specific "replaces the
label" claim: that worksheet's ``<pane>`` keeps its full 3-field
``<encodings>`` list AND adds a ``<customized-label>`` — the two coexist;
the label is a presentation template layered on top. BAN typography (and
making the worksheet-local compact/arrow default-formats VISIBLE, since
Tableau's placeholder substitution renders a field's value via its
resolved default-format) now lives in ``<customized-label>`` — see
``test_twb_kpi_styling_customized_label.py`` for that mechanism's full
test coverage and mined-evidence citations, including the documented
divergences (no caption run, plain newline, primary+delta only — not all
three encoded measures get their own label run).

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
D4-3  Table-level cell rule dead-mechanism regression guard (removed, D4-3
      moved to ``test_twb_kpi_styling_customized_label.py``)
D4-4  Compact format per classification (currency/number/percent) on primary
D4-5  Title legibility rule (per-worksheet, not workbook-wide)
D4-6  Delta arrow-format (mined fallback for color-by-sign)
D4-7  No-theme byte-identical (both entry points)
D4-8  No ``kpi_tile`` block -> tiles remain fully unstyled

D4-9 (XSD validity) and D4-10 (FastAPI integration) live in the companion
file ``test_twb_kpi_styling_integration.py``. Live-probe-driven hotfix
rounds live in ``test_twb_kpi_styling_hotfix.py`` (round 2: local
default-formats, table transparency, text-align) and
``test_twb_kpi_styling_customized_label.py`` (round 3: BAN typography via
``<customized-label>``) — split at the ~800-line file-size guideline, same
discipline as Slice D3's
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


def _text_encoding_columns(worksheet: ET.Element) -> list[str]:
    """The worksheet's ACTUAL ``<text>`` encoding column values.

    Live-probe #2 tightening: tests cross-check style/format targets against
    THIS (an independent read of what the mark really encodes) rather than
    only recomputing the expected value via the same ``_measure_instance()``
    helper the implementation itself uses — recomputing via the same helper
    cannot catch a future divergence between the encoding-building and
    format-building code paths (exactly the class of bug this hotfix closes).
    """
    return [t.get("column") for t in worksheet.findall(".//panes/pane/encodings/text")]


def _local_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    """``{raw bracketed field name: default-format}`` for THIS worksheet's own
    ``<datasource-dependencies><column>`` declarations (the live-probe #2
    hotfix location — see ``twb_builder._kpi_tile_local_default_formats``)."""
    return {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }


def _shared_default_formats(root: ET.Element) -> dict[str, str | None]:
    """``{raw bracketed field name: default-format}`` for the SHARED/global
    ``<datasources><datasource><column>`` declarations (brand-driven,
    Slice D3/E1's ``classify_measure_format`` — must stay UNCHANGED by the
    KPI-tile-local override, proving "don't disturb brand's default-format
    behavior" for every other worksheet referencing the same raw field)."""
    ds = root.find(".//datasources/datasource")
    assert ds is not None
    return {c.get("name"): c.get("default-format") for c in ds.findall("column")}


def _raw_field_from_instance(column: str) -> str:
    """Decode the raw field name embedded in an instance-qualified column
    string like ``"[ds].[sum:Sales:qk]"`` -> ``"Sales"`` — used to
    independently verify a local default-format target against what a
    ``<text>`` encoding ACTUALLY references, not what a test fixture assumes."""
    instance = column.split(".", 1)[1]  # "[sum:Sales:qk]" (or "[sum:Sales Delta:qk]")
    return instance[1:-1].split(":", 1)[1].rsplit(":", 1)[0]


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
# D4-3  BAN typography — TABLE-level per-field cell rule (REMOVED, live
# probe #3)
#
# Live probe #2 believed this rendered correctly; fresh live-probe #3
# renders proved it does NOT render BAN typography for a naked (rows/cols
# empty) Text mark. The mechanism was DELETED (``_kpi_tile_field_style_rule``
# no longer exists) — BAN font-size/font-name/color now render via the
# pane's ``<customized-label>`` instead (see
# ``test_twb_kpi_styling_customized_label.py``). This section is now a
# permanent regression guard: the TABLE-level ``element='cell'`` rule must
# NEVER reappear for a kpi_tile worksheet, themed or not.
# ---------------------------------------------------------------------------


def test_table_level_cell_rule_never_emitted_dead_mechanism_stays_removed() -> None:
    """Regression guard: BAN typography moved to <customized-label> in the
    live-probe #3 hotfix. The table-level element='cell' rule this slice
    ORIGINALLY used for font-size/font-family/color must never come back —
    it demonstrably does not render for a naked BAN view."""
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
    assert _cell_formats(worksheet) == []
    # The table-level <style> still carries the title + transparency rules
    # (both proven-working) — only the dead cell rule is gone.
    style = worksheet.find("table/style")
    assert style is not None
    elements = {r.get("element") for r in style.findall("style-rule")}
    assert elements == {"title", "table"}


# ---------------------------------------------------------------------------
# D4-4  Compact format per classification (currency/number/percent)
#
# Live-probe #2 hotfix: the compact format lives on THIS worksheet's own
# ``<datasource-dependencies><column default-format=...>`` declaration (a
# plain WORKSHEET-LOCAL default-format), NOT the ``element='cell'
# text-format`` rule (D4-3's cell rule is cosmetic-only now — see its
# section header). Root cause: verified against WB-118's real, published
# "Sales KPI (BAN) New" worksheet (WB-118.twbx)
# — its primary BAN measure carries the compact pattern as a plain
# default-format on its OWN worksheet-local <column>, not a cell override.
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

    # Independent cross-check: decode the raw field name from the worksheet's
    # OWN <text> encoding (not from the test fixture) and confirm THAT exact
    # name is what carries the local default-format.
    encoded_columns = _text_encoding_columns(worksheet)
    assert _field("Sales") in encoded_columns
    raw_primary = _raw_field_from_instance(_field("Sales"))
    assert raw_primary == "Sales"

    local_formats = _local_default_formats(worksheet)
    assert local_formats[f"[{raw_primary}]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


def test_compact_number_format_on_number_classified_primary() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Quantity]"] == "n#,##0,.0K;-#,##0,.0K"


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
    local_formats = _local_default_formats(worksheet)
    expected = twb_builder.classify_measure_format("Discount", BRAND["formats"])
    assert local_formats["[Discount]"] == expected == "0.0%"


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
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Sales]"] == 'c"€"#,##0,.0K;-"€"#,##0,.0K'


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
    # No kpi_tile block -> no LOCAL default-format override is stamped either
    # (same as any ordinary, non-kpi_tile sheet); rendering falls back to the
    # SHARED datasource's brand-driven default-format ("$#,##0").
    local_formats = _local_default_formats(worksheet)
    assert local_formats.get("[Sales]") is None
    shared_formats = _shared_default_formats(root)
    assert shared_formats["[Sales]"] == "$#,##0"


def test_local_default_format_does_not_disturb_shared_datasource_default_format() -> None:
    """The critical non-regression guard: the KPI tile's LOCAL compact
    override must NEVER touch the SHARED/global datasource <column> that
    every OTHER worksheet referencing the same raw field relies on
    (Slice D3/E1's classify_measure_format via brand.formats)."""
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
    shared_formats = _shared_default_formats(root)
    assert shared_formats["[Sales]"] == "$#,##0", (
        "Shared datasource default-format must stay the brand's NORMAL "
        "(uncompacted) format — the compact pattern lives only on the KPI "
        "tile worksheet's own LOCAL dependency column"
    )

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    assert local_formats["[Sales]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert local_formats["[Sales]"] != shared_formats["[Sales]"]

    # The chart worksheet also references "Sales" but is NOT a kpi_tile — it
    # gets NO local default-format override at all (unaffected, same as
    # before this slice); it renders via the SHARED datasource's format above.
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    chart_local_formats = _local_default_formats(chart_ws)
    assert chart_local_formats.get("[Sales]") is None


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
#
# Same live-probe #2 relocation as D4-4: the arrow-direction pattern lives
# on the delta measure's own WORKSHEET-LOCAL default-format, not the
# (ineffective, for naked BAN views) cell-level text-format rule.
# ---------------------------------------------------------------------------


def test_delta_arrow_format_when_semantic_delta_colors_true() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None

    # Independent cross-check against the worksheet's OWN <text> encoding.
    encoded_columns = _text_encoding_columns(worksheet)
    assert _field("Sales Delta") in encoded_columns
    raw_delta = _raw_field_from_instance(_field("Sales Delta"))
    assert raw_delta == "Sales Delta"

    local_formats = _local_default_formats(worksheet)
    assert local_formats[f"[{raw_delta}]"] == "*▲ #,##;▼ #,##"
    # No table-level cell rule exists at all any more (see
    # test_table_level_cell_rule_never_emitted_dead_mechanism_stays_removed);
    # the delta's compact/arrow format is made VISIBLE by a
    # <customized-label> placeholder run instead — see
    # test_twb_kpi_styling_customized_label.py.
    assert _cell_formats(worksheet) == []


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
    local_formats = _local_default_formats(worksheet)
    assert local_formats.get("[Sales Delta]") is None
    # Primary measure's own compact format is unaffected by the flag (no
    # brand passed here -> default "$" symbol, still the compact pattern).
    assert local_formats["[Sales]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


def test_delta_arrow_format_absent_when_no_delta_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    local_formats = _local_default_formats(worksheet)
    # Only the primary measure's own local override is present — no delta
    # field exists on this sheet's kpi spec at all.
    assert local_formats == {"[Quantity]": "n#,##0,.0K;-#,##0,.0K"}
    # No table-level cell rule (dead mechanism, removed); BAN
    # typography/cosmetics now live on <customized-label> — see
    # test_twb_kpi_styling_customized_label.py for that coverage, and
    # confirm here only that no delta run exists when there's no delta
    # measure at all (<customized-label> has exactly one value run).
    assert _cell_formats(worksheet) == []
    label_runs = worksheet.findall(".//panes/pane/customized-label//run")
    assert len(label_runs) == 1


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

