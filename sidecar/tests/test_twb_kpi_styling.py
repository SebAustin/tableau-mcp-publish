"""Design Excellence, Slice D4 — styled KPI tiles.

Fixes the three live-probe #1 findings on the KPI band (workbook 2527341):
(a) KPI numbers showing ``###`` (numeric overflow in ~250px tiles) — fixed by
    emitting a COMPACT number/currency format;
(b) KPI worksheet title labels dark-on-navy (illegible) — originally fixed by
    a per-worksheet ``element='title'`` style-rule; SUPERSEDED (see below) by
    an in-label caption + dashboard zone-level title suppression;
(c) KPI tiles carrying no zone-style at all (correct for D2, which
    deliberately deferred KPI-tile styling to this slice) — fixed by a themed
    ``<zone-style>`` on both the tile zones and the KPI band container.

FINAL SHAPE (live-probe #3's bisect ladder, V0-V12)
----------------------------------------------------------------------------
Three live-probe rounds after the original D4 land, Tableau Cloud fresh
renders still ignored the BAN typography/compact-formats this slice
published — even though the XML was independently verified correct. An
offline bisect ladder of a dozen ``.twbx`` variants (kept in scratchpad,
never committed) isolated the EXACT working shape by starting from a
verbatim graft of WB-118's real, published "Sales KPI (BAN) New"
worksheet (WB-118) and making the smallest
possible edits, rather than continuing to morph this builder's own
(independently-plausible but non-rendering) shape:

1. COMPACT/ARROW FORMAT lives on a worksheet-local CALCULATED column's OWN
   ``default-format`` (:func:`twb_builder._append_kpi_ban_calc_column`) —
   NOT a raw field's local ``<column>`` (the earlier hotfix's mechanism,
   :func:`twb_builder._kpi_tile_local_default_formats`, now REMOVED — bisect
   variant V7 proved Cloud silently ignores a ``default-format`` on a raw
   field's local dependency column for a naked BAN view).
2. The ``<customized-label>`` (:func:`twb_builder._kpi_tile_customized_label`)
   must reproduce the graft's OWN run idiom byte-for-byte: a letter-spaced
   UPPERCASE caption run derived from the tile's title, literal
   glyph-prefixed ``"Æ\\n"`` newline runs (NOT a plain ``"\\n"`` — an earlier
   hotfix round's assumption), and CDATA (not escaped-text) placeholder
   runs. Partial adoption is WORSE than none — variants that added only some
   of these traits to this builder's shape rendered a BLANK mark, not a
   partial label.
3. A pane-level ``<style-rule element='mark'>`` (``mark-labels-show``/
   ``mark-labels-cull``, both ``'true'``) is REQUIRED alongside the existing
   ``text-align: center`` rule (:func:`twb_builder._kpi_tile_pane_style_rules`)
   — its absence silently drops the ``<customized-label>`` back to the
   plain default text-mark render.
4. Because the in-label caption now labels the tile, the per-worksheet
   title-color rule (:func:`twb_builder._kpi_tile_title_style_rule`) is
   SKIPPED, and the dashboard zone hosting the tile gets
   ``show-title='false'`` (mirroring WB-118's own mined zone attribute)
   so the title is never shown twice.

See ``test_twb_kpi_styling_customized_label.py`` for the customized-label
mechanism's full test coverage and ``test_twb_kpi_styling_hotfix.py`` for
the earlier (still-relevant) transparency/pane-style hotfix coverage plus
the comparison-measure format reversal.

Test groups
-----------
D4-1  Tile zone-style from ``kpi_tile`` (background/border/padding)
D4-2  KPI band container gets ``kpi_tile.background`` only (continuous band)
D4-3  Table-level cell rule dead-mechanism regression guard (removed, D4-3
      moved to ``test_twb_kpi_styling_customized_label.py``)
D4-4  Compact format per classification (currency/number/percent) on the
      PRIMARY's calc column (FINAL SHAPE: no longer the raw field)
D4-5  Title suppression: in-label caption + zone show-title='false' replace
      the per-worksheet title-color rule
D4-6  Delta calc column format (arrow vs compact, per use_semantic_delta_colors)
D4-7  No-theme byte-identical (both entry points)
D4-8  No ``kpi_tile`` block -> tiles remain fully unstyled

D4-9 (XSD validity) and D4-10 (FastAPI integration) live in the companion
file ``test_twb_kpi_styling_integration.py``.
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


def _calc_field_name(field: str, *, delta: bool = False) -> str:
    """The worksheet-local CALCULATED column's bracketed name for *field*
    (Slice D4 FINAL SHAPE — see ``twb_builder._append_kpi_ban_calc_column``)."""
    suffix = "_Delta" if delta else ""
    return f"[{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug(field)}{suffix}]"


def _calc_instance(field: str, *, delta: bool = False) -> str:
    """The calc column's fully-qualified ``<text>``/placeholder instance name."""
    suffix = "_Delta" if delta else ""
    name = f"{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug(field)}{suffix}"
    return f"[usr:{name}:qk]"


def _cell_formats(worksheet: ET.Element) -> list[dict[str, str | None]]:
    rule = worksheet.find("table/style/style-rule[@element='cell']")
    if rule is None:
        return []
    return [dict(f.attrib) for f in rule.findall("format")]


def _text_encoding_columns(worksheet: ET.Element) -> list[str]:
    """The worksheet's ACTUAL ``<text>`` encoding column values.

    Live-probe #2 tightening: tests cross-check style/format targets against
    THIS (an independent read of what the mark really encodes) rather than
    only recomputing the expected value via the same helpers the
    implementation itself uses.
    """
    return [t.get("column") for t in worksheet.findall(".//panes/pane/encodings/text")]


def _local_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    """``{bracketed field name: default-format}`` for THIS worksheet's own
    ``<datasource-dependencies><column>`` declarations — includes BOTH raw
    fields (never carry a ``default-format`` for a kpi_tile sheet, Slice D4
    FINAL SHAPE) and the CALCULATED BAN columns (always do, when present)."""
    return {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }


def _calc_default_formats(worksheet: ET.Element) -> dict[str, str | None]:
    """``{calc field name: default-format}`` — only the CALCULATED BAN
    columns (name starts with ``Calculation_BAN_``), filtering out the
    (never-formatted) raw dependency columns for a cleaner assertion."""
    prefix = f"[{twb_builder._KPI_BAN_CALC_PREFIX}"
    return {k: v for k, v in _local_default_formats(worksheet).items() if k.startswith(prefix)}


def _shared_default_formats(root: ET.Element) -> dict[str, str | None]:
    """``{raw bracketed field name: default-format}`` for the SHARED/global
    ``<datasources><datasource><column>`` declarations (brand-driven,
    Slice D3/E1's ``classify_measure_format`` — must stay UNCHANGED by the
    KPI-tile-local mechanism, proving "don't disturb brand's default-format
    behavior" for every other worksheet referencing the same raw field)."""
    ds = root.find(".//datasources/datasource")
    assert ds is not None
    return {c.get("name"): c.get("default-format") for c in ds.findall("column")}


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
    # Design Excellence, Slice D4 FINAL SHAPE: the table-level <style> now
    # carries ONLY the transparency rule — the title-color rule ALSO no
    # longer applies (see D4-5): the in-label caption + zone show-title
    # suppression replace it whenever the BAN mechanism is active.
    style = worksheet.find("table/style")
    assert style is not None
    elements = {r.get("element") for r in style.findall("style-rule")}
    assert elements == {"table"}


# ---------------------------------------------------------------------------
# D4-4  Compact format per classification (currency/number/percent)
#
# Design Excellence, Slice D4 FINAL SHAPE (live-probe #3 bisect ladder, V7):
# the compact format lives on a worksheet-local CALCULATED column's OWN
# ``default-format`` (twb_builder._append_kpi_ban_calc_column) — NOT the
# raw field's local column (the earlier hotfix's mechanism, proven inert by
# bisect variant V7: Cloud silently ignores a default-format on a RAW
# field's local dependency column for a naked BAN view).
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

    # Independent cross-check: the primary <text> encoding must reference
    # the CALCULATED column's instance, not the raw field's.
    encoded_columns = _text_encoding_columns(worksheet)
    assert f"{DS_REF}.{_calc_instance('Sales')}" in encoded_columns
    assert _field("Sales") not in encoded_columns

    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Sales")] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'

    # The raw field's own local column never gets a default-format at all
    # (that mechanism was removed as dead weight — see D4-3's docstring).
    local_formats = _local_default_formats(worksheet)
    assert local_formats.get("[Sales]") is None


def test_compact_number_format_on_number_classified_primary() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Quantity")] == "n#,##0,.0K;-#,##0,.0K"


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
    calc_formats = _calc_default_formats(worksheet)
    expected = twb_builder.classify_measure_format("Discount", BRAND["formats"])
    assert calc_formats[_calc_field_name("Discount")] == expected == "0.0%"


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
    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Sales")] == 'c"€"#,##0,.0K;-"€"#,##0,.0K'


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
    # No kpi_tile block -> no CALCULATED BAN column is emitted at all — the
    # raw field's own local column never gets one either; rendering falls
    # back to the SHARED datasource's brand-driven default-format ("$#,##0").
    assert _calc_default_formats(worksheet) == {}
    local_formats = _local_default_formats(worksheet)
    assert local_formats.get("[Sales]") is None
    shared_formats = _shared_default_formats(root)
    assert shared_formats["[Sales]"] == "$#,##0"


def test_local_default_format_does_not_disturb_shared_datasource_default_format() -> None:
    """The critical non-regression guard: the KPI tile's calc-column format
    must NEVER touch the SHARED/global datasource <column> that every OTHER
    worksheet referencing the same raw field relies on (Slice D3/E1's
    classify_measure_format via brand.formats)."""
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
        "tile worksheet's own CALCULATED dependency column"
    )

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Sales")] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert calc_formats[_calc_field_name("Sales")] != shared_formats["[Sales]"]

    # The chart worksheet also references "Sales" but is NOT a kpi_tile — it
    # gets NO calc column at all (unaffected, same as before this slice); it
    # renders via the SHARED datasource's format above.
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    assert _calc_default_formats(chart_ws) == {}


# ---------------------------------------------------------------------------
# D4-5  Title suppression: in-label caption + zone show-title='false'
#
# Design Excellence, Slice D4 FINAL SHAPE: since the customized-label now
# carries an in-label caption run (derived from the tile's title — see
# test_twb_kpi_styling_customized_label.py), the per-worksheet title-color
# rule this slice originally used is REDUNDANT whenever the BAN mechanism
# is active — kept only as a defensive fallback for the degenerate case
# where kpi_tile theming is present but the sheet's kpi spec has no
# primary_measure (so no label/suppression happens either).
# ---------------------------------------------------------------------------


def test_kpi_tile_title_color_rule_absent_when_ban_active() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='title']") is None


def test_kpi_tile_zone_show_title_false_when_kpi_tile_active() -> None:
    """Mirrors WB-118's own mined zone attribute
    (WB-118: ``<zone ...
    name='Sales KPI (BAN) New' show-title='false' ...>``)."""
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    assert tile_zone.get("show-title") == "false"


def test_chart_sheet_unaffected_by_kpi_tile_title_suppression() -> None:
    """Scoping proof: the title-color rule/show-title suppression land on
    the KPI worksheet/zone only — the chart worksheet/zone must be
    untouched (unlike D3's workbook-level chrome.title_color, which would
    be global)."""
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    chart_ws = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert chart_ws is not None
    assert chart_ws.find("table/style/style-rule[@element='title']") is None
    # No workbook-level <style> either (THEME_KPI has no `chrome` block).
    assert root.find("style") is None

    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    assert chart_zone.get("show-title") is None


def test_title_color_rule_present_as_fallback_when_no_primary_measure() -> None:
    """Defensive fallback: kpi_tile theming is present but this specific
    sheet's kpi spec has no primary_measure — no BAN label (or its zone
    suppression) happens, so the title-color rule stays as a safety net."""
    sheet = {**KPI_SHEET_SALES, "title": "KPI Empty", "kpi": {}}
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Empty"],
            "layout_grammar": {
                "kind": "kpi_band_over_charts",
                "kpi_tile_titles": ["KPI Empty"],
                "chart_titles": [],
            },
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", [sheet], dashboards=dashboard, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Empty']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='title']") is not None


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
    # ban_active is independent of ban_color (the label still emits, just
    # with the default value/caption color) -> the title rule stays absent.
    assert worksheet.find("table/style/style-rule[@element='title']") is None


# ---------------------------------------------------------------------------
# D4-6  Delta calc-column format (arrow vs compact, per
# use_semantic_delta_colors) — Design Excellence, Slice D4 FINAL SHAPE
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
    delta_col = f"{DS_REF}.{_calc_instance('Sales Delta', delta=True)}"
    assert delta_col in encoded_columns
    assert _field("Sales Delta") not in encoded_columns

    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats[_calc_field_name("Sales Delta", delta=True)] == "*▲ #,##;▼ #,##"
    # No table-level cell rule exists at all any more (see
    # test_table_level_cell_rule_never_emitted_dead_mechanism_stays_removed);
    # the delta's format is made VISIBLE by a <customized-label> placeholder
    # run instead — see test_twb_kpi_styling_customized_label.py.
    assert _cell_formats(worksheet) == []


def test_delta_gets_compact_not_arrow_format_when_semantic_delta_colors_false() -> None:
    """use_semantic_delta_colors only governs WHICH format the delta calc
    column gets — the calc column (and its label line) still exist."""
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
    calc_formats = _calc_default_formats(worksheet)
    delta_key = _calc_field_name("Sales Delta", delta=True)
    assert calc_formats[delta_key] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    # Primary measure's own compact format is unaffected by the flag (no
    # brand passed here -> default "$" symbol, still the compact pattern).
    assert calc_formats[_calc_field_name("Sales")] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


def test_no_delta_calc_column_when_no_delta_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Quantity']")
    assert worksheet is not None
    # Only the primary measure's own calc column is present — no delta
    # field exists on this sheet's kpi spec at all.
    calc_formats = _calc_default_formats(worksheet)
    assert calc_formats == {_calc_field_name("Quantity"): "n#,##0,.0K;-#,##0,.0K"}
    assert _cell_formats(worksheet) == []
    label_runs = worksheet.findall(".//panes/pane/customized-label//run")
    # Caption + separator + one value run + one trailing newline (no delta
    # pair) — see test_twb_kpi_styling_customized_label.py for the full
    # run-by-run coverage.
    assert len(label_runs) == 4


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
    assert tile_zone.get("show-title") is None

    band = root.find(".//dashboards/dashboard/zones//zone[@h='20000'][@param='horz']")
    assert band is not None
    assert band.find("zone-style") is None

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='cell']") is None
    assert worksheet.find("table/style/style-rule[@element='title']") is None
    assert worksheet.find(".//panes/pane/customized-label") is None
    assert worksheet.find(".//panes/pane/style/style-rule[@element='mark']") is None
    assert _calc_default_formats(worksheet) == {}
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    assert pane.get("selection-relaxation-option") is None
