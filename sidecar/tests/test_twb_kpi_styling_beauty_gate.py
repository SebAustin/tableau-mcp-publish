"""Design Excellence, Slice D4 — BEAUTY-GATE hotfix (live-probe #5).

A user reported "I can't see the numbers" after opening a themed dashboard
INTERACTIVELY in a browser — the KPI-value invisibility was not merely a
static-image-render artifact (as SCHEMA.md constraint #5 previously
concluded); it reproduced live on Tableau Cloud too.

Decisive experiment: the UNTOUCHED WB-118 exemplar workbook
(``design/references/WB-118.twbx``) was
published as-is to our own dev site and its "Superstore Dashboard" view
(which mixes BAN tiles with charts) rendered its numbers PERFECTLY. This
ruled out a renderer/site-level limitation and proved the failure was in
OUR dashboard's zone XML specifically.

An exhaustive zone-tree diff plus a live-probe bisect ladder (V13-V25,
scratchpad-only, never committed) isolated the fix: the exemplar wraps
EVERY KPI-tile worksheet zone in a CASCADED ``is-fixed='true'`` pair —
an intermediate ``fixed-size='210'`` flow container AROUND a leaf
worksheet zone that is ITSELF ALSO ``is-fixed='true' fixed-size='150'``.
Neither alone is sufficient: an earlier round's V20 tried ``is-fixed``
on the leaf only and it did not fix the render. V24 tried removing the
``layout-basic`` canvas wrapper instead and it also did not help. V25
(the cascade, isolated in a genuine multi-zone dashboard alongside a real
chart sibling) is the first variant whose fresh Tableau Cloud render
showed a readable KPI value ("$2,297.4K").

:func:`twb_builder._append_worksheet_zones`'s ``wrap_fixed_size``/
``wrapper_id_start`` parameters (and the KPI-band call site in
:func:`twb_builder._build_dashboard`) now reproduce this cascade for
every KPI tile. Chart-band zones are intentionally left unwrapped — the
exemplar renders those without this treatment and every prior probe
round already showed our own chart zones render fine unmodified.

ROUND 2 (multi-tile): applying the wrapper+leaf cascade to EVERY KPI tile
in a real 4-tile band still rendered every value as a static '####'
placeholder, live, byte-for-byte IDENTICAL regardless of BAN font size
(10/17/36px) or workbook identity — ruling out a font-fit or render-cache
explanation. The mined exemplar's own 4-quadrant KPI row leaves exactly
ONE sibling unwrapped (non-fixed) as the flow's flexible anchor, so
:func:`twb_builder._kpi_wrapped_indices` wraps every tile EXCEPT THE
LAST.

ROUNDS 3-5 (still broken after round 2): live probes isolating tile
COUNT (a single real tile was still broken), the NULL "Sales Difference"
delta field (stripped entirely, still broken), and render caching
(maxAge=0, no-cache response headers, brand-new workbook identity — all
ruled out) failed to explain the remaining '####'.

ROUND 6 (the fix): re-examining the exemplar's OWN KPI row CONTAINER
(not just each tile's wrapper+leaf) showed it is ALSO
``is-fixed='true' fixed-size='96' layout-strategy-id='distribute-evenly'``
— a THIRD cascade level this builder never emitted. V25's isolated proof
only cascaded 2 levels (wrapper+leaf) because its wrapper was a DIRECT
child of the dashboard's outer vert flow; the real pipeline nests an
EXTRA dedicated KPI-band container between the outer flow and each
tile's wrapper, and that container needs the SAME is-fixed/fixed-size
treatment, plus ``layout-strategy-id="distribute-evenly"`` (an XSD-valid
``ZoneLayoutType-ST`` enum value neither this builder nor any prior
probe round had ever emitted). A live-probe render of the REAL
exec-audience full pipeline (4 KPI tiles, executive_dark theme, real
brand, real 9994-row Superstore data) with all three cascade levels
applied showed all four values clearly: "$2,297.4K" / "$286.3K" /
"37.9K" / "1,561". The BAN delta line remains a separate, deferred issue
(the "Sales Difference" field this specific dataset's planner selects as
delta_measure is 100% NULL for every row — an upstream field-selection
concern, not a zone-XML mechanism issue).

:func:`twb_builder._build_dashboard`'s KPI-band container zone now
carries ``is-fixed='true' fixed-size='140'
layout-strategy-id='distribute-evenly'`` unconditionally whenever
``effective_kpi`` is non-empty (mirrors ``sizing-mode='fixed'``'s
earlier unconditional treatment — a structural rendering fix, not a
cosmetic theme feature).

See ``design/corpus/SCHEMA.md`` constraint #5 for the (now superseded)
prior conclusion and its revision, and ``test_twb_kpi_styling_hotfix.py``
for the earlier (still valid, orthogonal) live-probe #2b findings.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

import twb_builder
from server import BrandModel, DesignThemeModel

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"

# ---------------------------------------------------------------------------
# Fixtures (mirrors test_twb_kpi_styling_hotfix.py's shapes)
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
KPI_SHEET_PROFIT = {
    "title": "KPI Profit",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Profit"],
    "kpi": {"primary_measure": "Profit"},
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Sales"],
}
SHEETS_BASIC = [KPI_SHEET_SALES, CHART_SHEET]
SHEETS_TWO_KPI = [KPI_SHEET_SALES, KPI_SHEET_PROFIT, CHART_SHEET]

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

DASHBOARD_TWO_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Sales", "KPI Profit", "Revenue by Region"],
        "title": "Executive Dashboard",
        "subtitle": None,
        "text_zones": [
            {"text": "Q4 Summary", "position": "header"},
            {"text": "Confidential", "position": "footer"},
        ],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Sales", "KPI Profit"],
            "chart_titles": ["Revenue by Region"],
        },
    }
]

THEME_KPI = DesignThemeModel(
    name="executive_dark",
    kpi_tile={
        "background": "#2f2e41",
        "border": {"color": "#000000", "style": "none", "width": 0},
        "padding": 4,
        "ban_color": "#ffffff",
        "use_semantic_delta_colors": True,
    },
).model_dump()  # type: ignore[arg-type]

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


def _dashboard_zones(xml: str) -> ET.Element:
    root = ET.fromstring(xml)
    zones = root.find(".//dashboards/dashboard/zones")
    assert zones is not None
    return zones


# ---------------------------------------------------------------------------
# Core cascade shape
# ---------------------------------------------------------------------------


def test_kpi_tile_zone_wrapped_in_cascaded_fixed_size() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    leaf = zones.find(".//zone[@name='KPI Sales']")
    assert leaf is not None
    assert leaf.get("is-fixed") == "true"
    assert leaf.get("fixed-size") == "150"
    assert leaf.get("w") == "100000"
    assert leaf.get("x") == "0"
    assert leaf.get("y") == "0"

    # xml.etree has no getparent(); locate the wrapper via the flow container.
    kpi_flow = zones.find(".//zone[@param='horz'][@h='20000']")
    assert kpi_flow is not None
    inner_wrapper = kpi_flow.find("zone[@type-v2='layout-flow']")
    assert inner_wrapper is not None
    assert inner_wrapper.get("is-fixed") == "true"
    assert inner_wrapper.get("fixed-size") == "210"
    assert inner_wrapper.get("param") == "horz"
    assert inner_wrapper.find("zone[@name='KPI Sales']") is not None


def test_kpi_tile_wrapper_carries_the_tiles_real_position() -> None:
    """The wrapper (not the leaf) carries the tile's real x/w allocation
    within the KPI band -- a single KPI tile spans the whole band."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    kpi_flow = zones.find(".//zone[@param='horz'][@h='20000']")
    assert kpi_flow is not None
    wrapper = kpi_flow.find("zone[@type-v2='layout-flow']")
    assert wrapper is not None
    assert wrapper.get("w") == "100000"
    assert wrapper.get("x") == "0"
    assert wrapper.get("h") == "100000"
    assert wrapper.get("y") == "0"


def test_two_kpi_tiles_only_the_non_last_tile_gets_wrapped() -> None:
    """BEAUTY-GATE hotfix, round 2: a real multi-tile band with EVERY tile
    wrapped rendered a static '####' placeholder for every value (verified
    live -- byte-identical across three BAN font sizes and a brand-new
    workbook identity, ruling out a font-fit or render-cache explanation).
    The mined exemplar's own 4-quadrant KPI row leaves exactly one sibling
    UNWRAPPED (non-fixed) -- mirrored here: with 2 tiles, only the FIRST
    (index 0) gets the cascade; the LAST tile (index 1) stays a plain,
    unwrapped, non-fixed zone."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_TWO_KPI,
        dashboards=DASHBOARD_TWO_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    kpi_flow = zones.find(".//zone[@param='horz'][@h='20000']")
    assert kpi_flow is not None
    wrappers = kpi_flow.findall("zone[@type-v2='layout-flow']")
    assert len(wrappers) == 1
    wrapper = wrappers[0]
    assert wrapper.get("is-fixed") == "true"
    assert wrapper.get("fixed-size") == "210"
    assert wrapper.get("x") == "0"
    wrapped_leaf = wrapper.find("zone[@is-fixed='true'][@fixed-size='150']")
    assert wrapped_leaf is not None
    assert wrapped_leaf.get("name") == "KPI Sales"

    # The last tile (KPI Profit) is a DIRECT child of kpi_flow, unwrapped.
    unwrapped_leaf = kpi_flow.find("zone[@name='KPI Profit']")
    assert unwrapped_leaf is not None
    assert unwrapped_leaf.get("is-fixed") is None
    assert unwrapped_leaf.get("fixed-size") is None
    assert unwrapped_leaf.get("x") == "50000"


def test_chart_zone_not_wrapped_fixed_size() -> None:
    """Scoping proof: only the KPI band gets the cascade -- the chart band
    (which already renders fine) is left structurally unchanged."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    chart_leaf = zones.find(".//zone[@name='Revenue by Region']")
    assert chart_leaf is not None
    assert chart_leaf.get("is-fixed") is None
    assert chart_leaf.get("fixed-size") is None
    chart_flow = zones.find(".//zone[@param='horz'][@h='80000']")
    assert chart_flow is not None
    assert chart_flow.find("zone[@type-v2='layout-flow']") is None


# ---------------------------------------------------------------------------
# Round 6: the KPI band CONTAINER itself is also cascaded (3rd level) --
# the fix that actually resolved the real multi-tile pipeline's '####'.
# ---------------------------------------------------------------------------


def test_kpi_band_container_itself_is_fixed_with_distribute_evenly() -> None:
    """The KPI band container (not just each tile's wrapper/leaf) must be
    is-fixed/fixed-size AND carry layout-strategy-id='distribute-evenly' --
    mined verbatim from the exemplar's own KPI row zone (id 9). This is the
    round-6 fix: rounds 1-5's wrapper+leaf-only cascade left the real
    4-tile pipeline showing '####' for every value; adding this THIRD
    level (confirmed live) is what actually fixed it."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    kpi_flow = zones.find(".//zone[@param='horz'][@h='20000']")
    assert kpi_flow is not None
    assert kpi_flow.get("is-fixed") == "true"
    assert kpi_flow.get("fixed-size") == "140"
    assert kpi_flow.get("layout-strategy-id") == "distribute-evenly"
    assert kpi_flow.get("type-v2") == "layout-flow"


def test_kpi_band_container_cascade_applies_with_multiple_tiles_too() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_TWO_KPI,
        dashboards=DASHBOARD_TWO_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    kpi_flow = zones.find(".//zone[@param='horz'][@h='20000']")
    assert kpi_flow is not None
    assert kpi_flow.get("is-fixed") == "true"
    assert kpi_flow.get("fixed-size") == "140"
    assert kpi_flow.get("layout-strategy-id") == "distribute-evenly"


def test_chart_band_container_not_cascaded() -> None:
    """Scoping proof: the round-6 band-container cascade is KPI-band-only
    -- the chart band container is left structurally unchanged (chart
    zones already render fine without any of this)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    chart_flow = zones.find(".//zone[@param='horz'][@h='80000']")
    assert chart_flow is not None
    assert chart_flow.get("is-fixed") is None
    assert chart_flow.get("fixed-size") is None
    assert chart_flow.get("layout-strategy-id") is None


# ---------------------------------------------------------------------------
# Id-collision safety
# ---------------------------------------------------------------------------


def test_all_zone_ids_unique_across_a_two_tile_dashboard_with_text_zones() -> None:
    """The wrapper ids are sourced from the SAME counter as every other new
    zone in the dashboard (header/footer text zones, sub-flow containers,
    worksheet zones) -- no id may repeat anywhere in <zones>."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_TWO_KPI,
        dashboards=DASHBOARD_TWO_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    zones = _dashboard_zones(xml)
    all_ids = [z.get("id") for z in zones.iter("zone")]
    assert len(all_ids) == len(set(all_ids)), f"duplicate zone ids found: {all_ids}"


# ---------------------------------------------------------------------------
# _append_worksheet_zones unit-level guard
# ---------------------------------------------------------------------------


def test_append_worksheet_zones_wrap_fixed_size_requires_wrapper_id_start() -> None:
    parent = ET.Element("zone")
    with pytest.raises(ValueError, match="wrapper_id_start"):
        twb_builder._append_worksheet_zones(parent, ["Sales"], id_start=3, wrap_fixed_size=True)


def test_append_worksheet_zones_wrap_fixed_size_false_ignores_wrapper_id_start() -> None:
    """Non-regression: wrapper_id_start is a no-op when wrap_fixed_size is
    False (default), so existing callers that never pass it keep working."""
    parent = ET.Element("zone")
    twb_builder._append_worksheet_zones(parent, ["Sales"], id_start=3)
    leaf = parent.find("zone[@name='Sales']")
    assert leaf is not None
    assert leaf.get("is-fixed") is None
    assert leaf.get("fixed-size") is None


# ---------------------------------------------------------------------------
# Cross-cutting: XSD validity + no-theme byte-identical guard
# ---------------------------------------------------------------------------


def test_kpi_band_with_cascade_is_xsd_valid_single_tile() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


def test_kpi_band_with_cascade_is_xsd_valid_two_tiles_with_text_zones() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_TWO_KPI,
        dashboards=DASHBOARD_TWO_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


def test_kpi_band_cascade_applies_regardless_of_theme_presence() -> None:
    """The cascade is a structural rendering fix, not a cosmetic theme
    feature -- it must be unconditional (like sizing-mode), so an
    UNTHEMED kpi_band_over_charts dashboard still gets it."""
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND
    )
    zones = _dashboard_zones(xml)
    leaf = zones.find(".//zone[@name='KPI Sales']")
    assert leaf is not None
    assert leaf.get("is-fixed") == "true"
    assert leaf.get("fixed-size") == "150"
    _assert_xsd_valid(xml)
