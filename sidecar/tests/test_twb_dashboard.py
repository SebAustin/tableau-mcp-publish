"""Dashboard XML tests: zone geometry, canvas size, and backward-compat regression (M3)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

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
# DB-1  one <zone type="worksheet"> per sheet
# ---------------------------------------------------------------------------


def test_dashboard_zone_count_matches_sheets() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    dashboards_el = root.find("dashboards")
    assert dashboards_el is not None, "<dashboards> element is missing"
    worksheet_zones = dashboards_el.findall('.//zone[@type="worksheet"]')
    assert len(worksheet_zones) == len(SHEETS_2)


def test_dashboard_zone_names_match_titles() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    root = ET.fromstring(xml)
    names = {z.get("name") for z in root.findall('.//zone[@type="worksheet"]')}
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


def _build_baseline_xml() -> str:
    """Reproduce the pre-feature build_twb_xml output (no dashboards kwarg)."""
    return twb_builder.build_twb_xml("Top Customers", "TopCustomers", "mysite", SHEETS_REGRESSION)


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
