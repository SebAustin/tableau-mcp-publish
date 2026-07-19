"""Dashboard XML tests: zone geometry, canvas size, and backward-compat regression (M3)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import hyper_builder
import twb_builder

SHEETS_2 = [
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

DASHBOARDS_BASIC = [{"name": "Dashboard 1", "titles": ["Revenue by Region", "Top 10 Customers"]}]


# ---------------------------------------------------------------------------
# DB-1  one worksheet zone (identified by @name, NO @type) per sheet
# A worksheet zone carries `name` and no type/type-v2 (verified vs wb1/wb7);
# the layout container is the only typed zone (@type-v2="layout-basic").
# ---------------------------------------------------------------------------


def test_dashboard_zone_count_matches_sheets() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    dashboards_el = root.find("dashboards")
    assert dashboards_el is not None, "<dashboards> element is missing"
    worksheet_zones = dashboards_el.findall(".//zone[@name]")
    assert len(worksheet_zones) == len(SHEETS_2)
    # A worksheet zone must NOT carry a type/type-v2 attribute, else Tableau
    # rejects the dashboard with 400011 "sheet has no visual representation".
    for z in worksheet_zones:
        assert z.get("type") is None and z.get("type-v2") is None


def test_dashboard_zone_names_match_titles() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    names = {z.get("name") for z in root.findall(".//zone[@name]")}
    assert names == {"Revenue by Region", "Top 10 Customers"}


# ---------------------------------------------------------------------------
# DB-2  zone geometry invariants
# ---------------------------------------------------------------------------


def _get_quads(layout: str, n: int) -> list[dict[str, int]]:
    titles = [f"Sheet {i}" for i in range(n)]
    return twb_builder._tile_zones(titles, layout)


def test_tile_zones_vertical_sum_equals_100000() -> None:
    for n in range(1, 9):
        quads = _get_quads("tiled_vertical", n)
        assert sum(q["h"] for q in quads) == 100000, f"Σh != 100000 for n={n}"


def test_tile_zones_horizontal_sum_equals_100000() -> None:
    for n in range(1, 9):
        quads = _get_quads("tiled_horizontal", n)
        assert sum(q["w"] for q in quads) == 100000, f"Σw != 100000 for n={n}"


def test_tile_zones_vertical_equal_x_zero_and_w() -> None:
    quads = _get_quads("tiled_vertical", 3)
    for q in quads:
        assert q["x"] == 0
        assert q["w"] == 100000


def test_tile_zones_horizontal_equal_y_zero_and_h() -> None:
    quads = _get_quads("tiled_horizontal", 3)
    for q in quads:
        assert q["y"] == 0
        assert q["h"] == 100000


def test_tile_zones_vertical_distinct_y() -> None:
    quads = _get_quads("tiled_vertical", 4)
    ys = [q["y"] for q in quads]
    assert len(set(ys)) == len(ys), "y values are not distinct"


def test_tile_zones_horizontal_distinct_x() -> None:
    quads = _get_quads("tiled_horizontal", 4)
    xs = [q["x"] for q in quads]
    assert len(set(xs)) == len(xs), "x values are not distinct"


def test_tile_zones_no_overlap_vertical() -> None:
    quads = _get_quads("tiled_vertical", 3)
    for i, a in enumerate(quads):
        for j, b in enumerate(quads):
            if i >= j:
                continue
            # Overlap on y-axis if intervals intersect
            a_end = a["y"] + a["h"]
            b_end = b["y"] + b["h"]
            overlap = min(a_end, b_end) - max(a["y"], b["y"])
            assert overlap <= 0, f"Zones {i} and {j} overlap vertically by {overlap}"


def test_tile_zones_no_overlap_horizontal() -> None:
    quads = _get_quads("tiled_horizontal", 3)
    for i, a in enumerate(quads):
        for j, b in enumerate(quads):
            if i >= j:
                continue
            a_end = a["x"] + a["w"]
            b_end = b["x"] + b["w"]
            overlap = min(a_end, b_end) - max(a["x"], b["x"])
            assert overlap <= 0, f"Zones {i} and {j} overlap horizontally by {overlap}"


# ---------------------------------------------------------------------------
# Canvas-size tests — <size> must match audience table (BI_DESIGN §4.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "canvas_w,canvas_h",
    [
        (1000, 800),   # exec
        (1200, 900),   # analyst
        (800, 1200),   # operational
        (1000, 900),   # mixed
    ],
)
def test_dashboard_size_element_matches_canvas(canvas_w: int, canvas_h: int) -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
        canvas_width=canvas_w,
        canvas_height=canvas_h,
    )
    root = ET.fromstring(xml)
    size_el = root.find(".//dashboard/size")
    assert size_el is not None
    assert int(size_el.get("maxwidth", "0")) == canvas_w
    assert int(size_el.get("maxheight", "0")) == canvas_h
    assert int(size_el.get("minwidth", "0")) == canvas_w
    assert int(size_el.get("minheight", "0")) == canvas_h


# ---------------------------------------------------------------------------
# Design Excellence, Slice D4 FINAL SHAPE hotfix — sizing-mode='fixed'
#
# Live-probe #2f's bisect ladder (V13-V23, design/corpus/SCHEMA.md
# constraint #5) proved a KPI-tile Text mark's compact-formatted value
# overflows to "###" inside ANY dashboard with 2+ zones, regardless of
# fontsize/mark-labels-cull/delta-line/band-height/zone-width/fixed-size
# zones — pointing at a dashboard-level (not zone-level) attribute. Our
# emitted <size> carried equal min/max but NO sizing-mode, unlike every
# mined exemplar dashboard (WB-118/WB-117 both carry
# sizing-mode='fixed'). Without it, Tableau's server-side image renderer
# does not treat an equal-min/max <size> as truly fixed — it falls back to
# range/automatic sizing, which compresses zone content. This regression
# guard is unconditional (sizing-mode is emitted on EVERY dashboard, not
# gated by design_theme) — a deliberate, mined-evidence-backed baseline
# change, not a design_theme-driven feature.
# ---------------------------------------------------------------------------


def test_dashboard_size_has_sizing_mode_fixed_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC
    )
    root = ET.fromstring(xml)
    size_el = root.find(".//dashboard/size")
    assert size_el is not None
    assert size_el.get("sizing-mode") == "fixed"


def test_dashboard_size_has_sizing_mode_fixed_embedded(tmp_path: Path) -> None:
    df = pd.DataFrame({"Revenue": [1.0, 2.0], "Region": ["East", "West"]})
    hyper_file = tmp_path / "sizing_mode.hyper"
    hyper_builder.dataframe_to_hyper(df, hyper_file)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    size_el = root.find(".//dashboard/size")
    assert size_el is not None
    assert size_el.get("sizing-mode") == "fixed"


# ---------------------------------------------------------------------------
# Default-None regression — output must be byte-identical to pre-feature version
# ---------------------------------------------------------------------------

SHEETS_REGRESSION = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    }
]

# NOTE: _build_baseline_xml() was removed — it was never called and its presence
# undermined the "no golden snapshot" claim for test_default_none_regression_byte_identical.
# The byte-identical guard is a self-comparison (two live calls vs each other), not
# a comparison against a stored snapshot.  See ADAPTATION_PLAN §A2.5.


def test_default_none_dashboards_no_dashboards_element() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_REGRESSION)
    root = ET.fromstring(xml)
    assert root.find("dashboards") is None, "<dashboards> should not appear when dashboards=None"


def test_default_none_regression_byte_identical() -> None:
    """build_twb_xml with no dashboards= kwarg must produce the same XML as
    an explicit dashboards=None call (which mirrors the original signature)."""
    xml_default = twb_builder.build_twb_xml(
        "Top Customers", "TopCustomers", "mysite", SHEETS_REGRESSION,
        server_url="https://x.online.tableau.com",
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "Top Customers", "TopCustomers", "mysite", SHEETS_REGRESSION,
        server_url="https://x.online.tableau.com",
        dashboards=None,
    )
    assert xml_default == xml_explicit_none


# ---------------------------------------------------------------------------
# A2 structural pins — dashboard-specific schema-valid output shape
# re-baselined for schema-valid output (XSD A2); see ADAPTATION_PLAN §A2
# ---------------------------------------------------------------------------


def test_dashboards_element_precedes_windows() -> None:
    """<dashboards> must appear before <windows> in the workbook (XSD sequence)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    children = [child.tag for child in root]
    assert "dashboards" in children, "<dashboards> element is missing"
    assert "windows" in children, "<windows> element is missing"
    dashboards_idx = children.index("dashboards")
    windows_idx = children.index("windows")
    assert dashboards_idx < windows_idx, (
        f"<dashboards> (index {dashboards_idx}) must come before "
        f"<windows> (index {windows_idx})"
    )


def test_dashboard_has_simple_id() -> None:
    """Each <dashboard> must have a <simple-id> child (required by XSD)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    for db in root.findall(".//dashboards/dashboard"):
        sid = db.find("simple-id")
        assert sid is not None, f"dashboard '{db.get('name')}' missing <simple-id>"


def test_dashboard_window_has_viewpoints_and_active() -> None:
    """Dashboard <window> must have <viewpoints/> and <active id='-1'/> (XSD required).

    id='-1' is the Tableau standard sentinel for "no sheet currently active".
    All reference workbooks (wb3–wb7) use -1; id='1' was incorrectly pointing
    at the container zone rather than a sheet.
    """
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    for win in root.findall(".//windows/window[@class='dashboard']"):
        assert win.find("viewpoints") is not None, (
            f"dashboard window '{win.get('name')}' missing <viewpoints/>"
        )
        active = win.find("active")
        assert active is not None, (
            f"dashboard window '{win.get('name')}' missing <active>"
        )
        assert active.get("id") == "-1", (
            f"dashboard window '{win.get('name')}' active id should be '-1' "
            f"(no-selection sentinel), got '{active.get('id')}'"
        )


def test_build_starter_twbx_with_dashboard(tmp_path: Path) -> None:
    out = twb_builder.build_starter_twbx(
        "DS", "ds", "site", SHEETS_2, tmp_path / "wb.twbx",
        dashboards=DASHBOARDS_BASIC,
    )
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        assert len(twb_files) == 1
        root = ET.fromstring(archive.read(twb_files[0]))
        assert root.find("dashboards") is not None


def test_build_starter_twbx_without_dashboard_is_unchanged(tmp_path: Path) -> None:
    """No dashboards kwarg → zip must not contain a <dashboards> element."""
    out = twb_builder.build_starter_twbx(
        "DS", "ds", "site", SHEETS_REGRESSION, tmp_path / "wb.twbx",
    )
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        root = ET.fromstring(archive.read(twb_files[0]))
        assert root.find("dashboards") is None
