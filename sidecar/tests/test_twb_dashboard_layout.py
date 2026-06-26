"""Dashboard composition tests: title/text zones + KPI-band-over-charts 2-zone layout.

Slice 3B: mirrors wb6 (Threshold Analysis Two Way) and wb7 (Quota Attainment)
dashboard structure.

Test groups
-----------
DL-1  Title / subtitle text zones (wb6/wb7 structure)
DL-2  Extra textZones (header + footer positions)
DL-3  kpi_band_over_charts 2-zone layout
DL-4  Default-path invariants (no title / no grammar → output unchanged)
DL-5  XSD validity for every new variant
DL-6  Viewpoints completeness guard (400011 regression)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from lxml import etree

import twb_builder

# ---------------------------------------------------------------------------
# Shared helpers
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


# Sheets shared across tests
KPI_SHEET = {
    "title": "KPI Revenue",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Revenue"],
    "kpi": {"primaryMeasure": "Revenue"},
}
KPI_SHEET_2 = {
    "title": "KPI Profit",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Profit"],
    "kpi": {"primaryMeasure": "Profit"},
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}
CHART_SHEET_2 = {
    "title": "Profit Trend",
    "mark_type": "line",
    "cols": ["Quarter"],
    "rows": [],
    "measures": ["Profit"],
}

SHEETS_MIXED = [KPI_SHEET, KPI_SHEET_2, CHART_SHEET, CHART_SHEET_2]

DASHBOARD_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Revenue", "KPI Profit", "Revenue by Region", "Profit Trend"],
        "title": "Executive Dashboard",
        "subtitle": "Q4 2024 Performance",
        "text_zones": [],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Revenue", "KPI Profit"],
            "chart_titles": ["Revenue by Region", "Profit Trend"],
        },
    }
]

DASHBOARD_TITLE_ONLY = [
    {
        "name": "Dashboard 1",
        "titles": ["KPI Revenue", "Revenue by Region"],
        "title": "My Dashboard Title",
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_WITH_TEXT_ZONES = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region"],
        "title": None,
        "subtitle": None,
        "text_zones": [
            {"text": "Header annotation", "position": "header"},
            {"text": "Footer source note", "position": "footer"},
        ],
        "layout_grammar": None,
    }
]


# ---------------------------------------------------------------------------
# DL-1  Title / subtitle text zones (mirror wb6/wb7)
# ---------------------------------------------------------------------------


def test_title_zone_emitted_when_title_present() -> None:
    """When dashboardTitle is set, a type-v2='text' zone must appear in the dashboard."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    assert len(text_zones) >= 1, "Expected at least one text zone for title"


def test_title_zone_contains_formatted_text() -> None:
    """The title text zone must carry <formatted-text><run ...>title</run></formatted-text>.

    Mirrors wb6 line 1664 and wb7 line 4476 exactly.
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    # Find the zone that contains the title text
    title_text = "My Dashboard Title"
    found = False
    for zone in text_zones:
        ft = zone.find("formatted-text")
        if ft is not None:
            for run in ft.findall("run"):
                if run.text and title_text in run.text:
                    found = True
    assert found, (
        f"Title text '{title_text}' not found inside any <run> within a text zone "
        "(must mirror wb6 <run bold='true' fontsize='20'>title</run>)"
    )


def test_title_zone_run_has_bold_attribute() -> None:
    """The title <run> element must have bold='true' (mirrors wb6 ~1664)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    title_text = "My Dashboard Title"
    found_bold = False
    for zone in text_zones:
        ft = zone.find("formatted-text")
        if ft is not None:
            for run in ft.findall("run"):
                if run.text and title_text in run.text:
                    # bold must be 'true' (mirroring wb6 which uses bold="true")
                    assert run.get("bold") == "true", (
                        f"Title run must have bold='true', got bold={run.get('bold')!r}"
                    )
                    found_bold = True
    assert found_bold, "Title run with bold not found"


def test_subtitle_zone_emitted_when_subtitle_present() -> None:
    """When dashboardSubtitle is set, a second text zone must appear."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    subtitle_text = "Q4 2024 Performance"
    found = any(
        (ft := zone.find("formatted-text")) is not None
        and any(
            run.text and subtitle_text in run.text
            for run in ft.findall("run")
        )
        for zone in text_zones
    )
    assert found, f"Subtitle text '{subtitle_text}' not found in any text zone"


def test_title_zone_placed_before_chart_flow() -> None:
    """Title zone must appear before the chart worksheet zones in the zone tree.

    In wb6/wb7, text zones sit at the top of the layout-flow container.
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    root = ET.fromstring(xml)
    # Find all direct children of the outermost layout-flow (or layout-basic)
    outer_flow = root.find(".//dashboards//zone[@type-v2='layout-flow']")
    assert outer_flow is not None, "Expected a layout-flow container zone"
    children = list(outer_flow)
    zone_types = [z.get("type-v2", "") for z in children if z.tag == "zone"]
    # Text zone must appear before any worksheet zone (no type-v2)
    text_indices = [i for i, t in enumerate(zone_types) if t == "text"]
    ws_indices = [i for i, t in enumerate(zone_types) if t == ""]
    if text_indices and ws_indices:
        assert min(text_indices) < min(ws_indices), (
            "Title text zone must precede worksheet zones in the container"
        )


# ---------------------------------------------------------------------------
# DL-2  Extra textZones (header + footer positions)
# ---------------------------------------------------------------------------


def test_header_text_zone_emitted() -> None:
    """A textZone with position='header' must produce a type-v2='text' zone."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [CHART_SHEET],
        dashboards=DASHBOARD_WITH_TEXT_ZONES,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    found = any(
        (ft := z.find("formatted-text")) is not None
        and any(
            run.text and "Header annotation" in run.text
            for run in ft.findall("run")
        )
        for z in text_zones
    )
    assert found, "Header textZone content not found in any text zone"


def test_footer_text_zone_emitted() -> None:
    """A textZone with position='footer' must produce a type-v2='text' zone."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [CHART_SHEET],
        dashboards=DASHBOARD_WITH_TEXT_ZONES,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    found = any(
        (ft := z.find("formatted-text")) is not None
        and any(
            run.text and "Footer source note" in run.text
            for run in ft.findall("run")
        )
        for z in text_zones
    )
    assert found, "Footer textZone content not found in any text zone"


# ---------------------------------------------------------------------------
# DL-3  kpi_band_over_charts 2-zone layout
# ---------------------------------------------------------------------------


def test_kpi_band_layout_has_two_flow_children() -> None:
    """kpi_band_over_charts must produce two layout-flow sub-zones (band + charts).

    Structure (mirrors wb7):
      outer layout-flow param='vert'
        [text zones]
        layout-flow param='horz'   ← KPI band
          kpi tile ws zones
        layout-flow param='horz'   ← charts
          chart ws zones
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    # There should be at least 2 layout-flow zones with param='horz' (band + charts)
    horz_flows = root.findall(".//dashboards//zone[@type-v2='layout-flow'][@param='horz']")
    assert len(horz_flows) >= 2, (
        f"Expected at least 2 param='horz' layout-flow zones for KPI band + charts, "
        f"got {len(horz_flows)}"
    )


def test_kpi_band_tiles_in_horz_flow() -> None:
    """KPI tile worksheets must reside in a param='horz' flow (the KPI band)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    # Find the horz flows and check which one contains the KPI tile zones
    kpi_tile_titles = {"KPI Revenue", "KPI Profit"}
    horz_flows = root.findall(".//dashboards//zone[@type-v2='layout-flow'][@param='horz']")
    found_band = False
    for flow in horz_flows:
        child_names = {z.get("name") for z in flow.findall("zone") if z.get("name")}
        if kpi_tile_titles.issubset(child_names):
            found_band = True
    assert found_band, (
        f"KPI tile titles {kpi_tile_titles} must all reside in a single "
        "param='horz' layout-flow (the KPI band)"
    )


def test_chart_tiles_in_separate_flow_from_kpi_band() -> None:
    """Chart worksheets must reside in a flow that does NOT contain KPI tile zones."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    kpi_tile_titles = {"KPI Revenue", "KPI Profit"}
    chart_titles = {"Revenue by Region", "Profit Trend"}
    horz_flows = root.findall(".//dashboards//zone[@type-v2='layout-flow'][@param='horz']")
    # Find the flow that contains chart zones
    charts_flow_found = False
    for flow in horz_flows:
        child_names = {z.get("name") for z in flow.findall("zone") if z.get("name")}
        if chart_titles.issubset(child_names) and not kpi_tile_titles.intersection(child_names):
            charts_flow_found = True
    assert charts_flow_found, (
        f"Chart titles {chart_titles} must reside in a layout-flow separate from KPI tiles"
    )


def test_kpi_band_outer_flow_is_vert() -> None:
    """The outer container for kpi_band_over_charts must have param='vert'.

    Mirrors wb7's layout-flow id=76 param='vert' that wraps the entire dashboard.
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    # The layout-basic canvas must contain a param='vert' flow
    vert_flows = root.findall(".//dashboards//zone[@type-v2='layout-flow'][@param='vert']")
    assert len(vert_flows) >= 1, "Expected at least one param='vert' layout-flow for kpi_band"


def test_kpi_band_all_worksheets_have_zones() -> None:
    """Every sheet title in SHEETS_MIXED must have a zone in the dashboard."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    zone_names = {z.get("name") for z in root.findall(".//dashboards//zone[@name]")}
    for sheet in SHEETS_MIXED:
        assert sheet["title"] in zone_names, (
            f"Sheet '{sheet['title']}' has no zone in the dashboard"
        )


def test_kpi_band_worksheet_zones_have_no_type_v2() -> None:
    """Worksheet zones must carry name but NO type/type-v2 (DB-1 invariant)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    # A worksheet zone has @name and no @type-v2
    for title in ["KPI Revenue", "KPI Profit", "Revenue by Region", "Profit Trend"]:
        zones_with_name = root.findall(f".//dashboards//zone[@name='{title}']")
        assert len(zones_with_name) == 1, (
            f"Expected exactly 1 zone named '{title}', got {len(zones_with_name)}"
        )
        z = zones_with_name[0]
        assert z.get("type-v2") is None, (
            f"Worksheet zone '{title}' must not have type-v2 (got {z.get('type-v2')!r})"
        )


def test_kpi_band_worksheet_zones_have_layout_cache() -> None:
    """Each worksheet zone must carry a <layout-cache> child."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    for title in ["KPI Revenue", "KPI Profit", "Revenue by Region", "Profit Trend"]:
        zones_with_name = root.findall(f".//dashboards//zone[@name='{title}']")
        assert len(zones_with_name) == 1
        z = zones_with_name[0]
        lc = z.find("layout-cache")
        assert lc is not None, f"Worksheet zone '{title}' must have a <layout-cache> child"


# ---------------------------------------------------------------------------
# DL-4  Default-path invariants: no title / no grammar → output unchanged
# ---------------------------------------------------------------------------

SHEETS_BASIC = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
    {
        "title": "Top 10 Customers",
        "mark_type": "text",
        "cols": [],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARD_NO_EXTRAS = [
    {"name": "Dashboard 1", "titles": ["Revenue by Region", "Top 10 Customers"]}
]

DASHBOARD_EXPLICIT_NONE = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region", "Top 10 Customers"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]


def test_default_path_byte_identical_with_and_without_extra_keys() -> None:
    """A dashboard dict with no title/grammar keys must produce the same XML as
    one with explicit None values for those keys.

    This ensures the new optional fields do not change the default code path.
    """
    xml_old = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_NO_EXTRAS
    )
    xml_new = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_EXPLICIT_NONE
    )
    assert xml_old == xml_new, (
        "Adding explicit None title/grammar fields must not change the output XML"
    )


def test_default_path_no_text_zones() -> None:
    """Without a title, there must be no type-v2='text' zones."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_NO_EXTRAS
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    assert len(text_zones) == 0, (
        f"Expected 0 text zones when no title is set, got {len(text_zones)}"
    )


def test_default_path_single_layout_flow() -> None:
    """Without a layout grammar, there must be exactly one layout-flow container
    (the existing tiled_vertical/horizontal single-flow behavior)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_NO_EXTRAS
    )
    root = ET.fromstring(xml)
    flows = root.findall(".//dashboards//zone[@type-v2='layout-flow']")
    assert len(flows) == 1, (
        f"Default path must have exactly 1 layout-flow, got {len(flows)}"
    )


# ---------------------------------------------------------------------------
# DL-5  XSD validity for every new variant
# ---------------------------------------------------------------------------


def test_xsd_valid_title_only() -> None:
    """Dashboard with title only must be XSD-valid."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_title_and_subtitle() -> None:
    """Dashboard with title + subtitle must be XSD-valid."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_header_footer_text_zones() -> None:
    """Dashboard with header + footer textZones must be XSD-valid."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [CHART_SHEET],
        dashboards=DASHBOARD_WITH_TEXT_ZONES,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_kpi_band_over_charts() -> None:
    """kpi_band_over_charts layout must be XSD-valid."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_embedded_with_title() -> None:
    """build_embedded_twb_xml with dashboard title must be XSD-valid."""
    import tempfile

    import pandas as pd

    import hyper_builder

    with tempfile.TemporaryDirectory() as tmp:
        df = pd.DataFrame(
            {"Region": ["West", "East"], "Revenue": [100.0, 200.0], "Profit": [10.0, 20.0]}
        )
        hyper_path = Path(tmp) / "test.hyper"
        hyper_builder.dataframe_to_hyper(df, hyper_path)
        columns = hyper_builder.read_hyper_columns(hyper_path)
        xml = twb_builder.build_embedded_twb_xml(
            "Sales DS",
            hyper_path.name,
            columns,
            SHEETS_MIXED,
            dashboards=DASHBOARD_KPI_BAND,
        )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# DL-6  Viewpoints completeness guard (400011 regression)
# ---------------------------------------------------------------------------


def test_viewpoints_complete_with_title_dashboard() -> None:
    """Dashboard window viewpoints must list every sheet title even when
    title text zones are added (the 400011 regression guard)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [KPI_SHEET, CHART_SHEET],
        dashboards=DASHBOARD_TITLE_ONLY,
    )
    root = ET.fromstring(xml)
    sheet_titles = {"KPI Revenue", "Revenue by Region"}
    for win in root.findall(".//windows/window[@class='dashboard']"):
        viewpoints_el = win.find("viewpoints")
        assert viewpoints_el is not None, "Dashboard window missing <viewpoints>"
        vp_names = {vp.get("name") for vp in viewpoints_el.findall("viewpoint")}
        assert sheet_titles == vp_names, (
            f"Viewpoints {vp_names} must match sheet titles {sheet_titles}"
        )


def test_viewpoints_complete_with_kpi_band_layout() -> None:
    """kpi_band_over_charts layout must still list every worksheet in <viewpoints>."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
    )
    root = ET.fromstring(xml)
    sheet_titles = {"KPI Revenue", "KPI Profit", "Revenue by Region", "Profit Trend"}
    for win in root.findall(".//windows/window[@class='dashboard']"):
        viewpoints_el = win.find("viewpoints")
        assert viewpoints_el is not None, "Dashboard window missing <viewpoints>"
        vp_names = {vp.get("name") for vp in viewpoints_el.findall("viewpoint")}
        assert sheet_titles == vp_names, (
            f"Viewpoints {vp_names} must match all sheet titles {sheet_titles}"
        )


def test_viewpoints_not_polluted_by_text_zone_names() -> None:
    """Text zones are NOT worksheets — they must not appear in <viewpoints>."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [CHART_SHEET],
        dashboards=DASHBOARD_WITH_TEXT_ZONES,
    )
    root = ET.fromstring(xml)
    for win in root.findall(".//windows/window[@class='dashboard']"):
        viewpoints_el = win.find("viewpoints")
        if viewpoints_el is not None:
            vp_names = {vp.get("name") for vp in viewpoints_el.findall("viewpoint")}
            assert "Header annotation" not in vp_names
            assert "Footer source note" not in vp_names
