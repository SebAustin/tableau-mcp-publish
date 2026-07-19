"""Story (storyboard) XML tests — Phase E4, Pillar E.

Verifies the ``<dashboard type='storyboard'>`` structure inside the SHARED
``<dashboards>`` container (peer of regular dashboards), the flipboard/nav
zone pairing, story-point captured-sheet validation (fail loud), the story
window entry, and XSD-validity for every variant. Also guards that the
no-story path stays byte-identical to before this slice — the refactor that
merged per-dashboard ``<dashboards>`` wrappers into one shared container must
not change output when there is exactly one regular dashboard and no story.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest
from lxml import etree

import hyper_builder
import twb_builder

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"

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

STORY_BASIC = [
    {
        "name": "Story: Q4 Review",
        "nav_type": "caption",
        "points": [
            {"caption": "Here's the headline.", "captured_sheet": "Revenue by Region"},
            {"caption": "And the detail.", "captured_sheet": "Top 10 Customers"},
        ],
    }
]


# ---------------------------------------------------------------------------
# XSD harness (mirrors test_twb_schema_validation.py)
# ---------------------------------------------------------------------------


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )


def _load_schema() -> etree.XMLSchema:
    parser = _safe_parser()
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str) -> None:
    schema = _load_schema()
    parser = _safe_parser()
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"TWB XSD validation FAILED:\n{errors}"


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_story_dashboard_is_type_storyboard_inside_shared_dashboards() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    dashboards_els = root.findall("dashboards")
    assert len(dashboards_els) == 1, (
        "story must live in the SAME <dashboards> as regular dashboards"
    )
    story_dashboard = dashboards_els[0].find("dashboard[@type='storyboard']")
    assert story_dashboard is not None
    assert story_dashboard.get("name") == "Story: Q4 Review"
    # Regular dashboard must still be present alongside it.
    regular = dashboards_els[0].find("dashboard[@name='Dashboard 1']")
    assert regular is not None


def test_story_zones_layout_basic_then_flow_then_title_nav_flipboard() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    story = root.find(".//dashboard[@type='storyboard']")
    assert story is not None
    canvas = story.find("zones/zone[@type-v2='layout-basic']")
    assert canvas is not None
    flow = canvas.find("zone[@type-v2='layout-flow'][@param='vert']")
    assert flow is not None
    children = list(flow)
    assert [z.get("type") for z in children] == ["title", "flipboard-nav", "flipboard"]
    # Confirm these three zones use bare `type=`, NOT `type-v2` (the reference
    # spelling for story zones — distinct from every other zone kind).
    for z in children:
        assert z.get("type-v2") is None


def test_story_dashboard_size_has_sizing_mode_fixed() -> None:
    """Design Excellence, Slice D4 FINAL SHAPE hotfix: a storyboard is
    still a <dashboard> element with its own <size> — same
    sizing-mode='fixed' mined-evidence fix as _build_dashboard's <size>
    (see test_twb_dashboard.py's sizing-mode test group and
    design/corpus/SCHEMA.md constraint #5)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    story = root.find(".//dashboard[@type='storyboard']")
    assert story is not None
    size_el = story.find("size")
    assert size_el is not None
    assert size_el.get("sizing-mode") == "fixed"


def test_flipboard_nav_and_flipboard_zones_are_paired() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    nav_zone = root.find(".//zone[@type='flipboard-nav']")
    flip_zone = root.find(".//zone[@type='flipboard']")
    assert nav_zone is not None and flip_zone is not None
    assert nav_zone.get("is-fixed") == "true"
    assert nav_zone.get("paired-zone-id") == flip_zone.get("id")
    assert flip_zone.get("paired-zone-id") == nav_zone.get("id")


def test_flipboard_element_has_required_attrs() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    flipboard = root.find(".//zone[@type='flipboard']/flipboard")
    assert flipboard is not None
    assert flipboard.get("active-id") == "1"
    assert flipboard.get("nav-type") == "caption"
    assert flipboard.get("show-nav-arrows") == "true"


def test_flipboard_nav_type_defaults_to_caption_when_omitted() -> None:
    story_no_nav_type = [
        {
            "name": "Story: Default Nav",
            "points": [{"caption": "x", "captured_sheet": "Revenue by Region"}],
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=story_no_nav_type
    )
    root = ET.fromstring(xml)
    flipboard = root.find(".//zone[@type='flipboard']/flipboard")
    assert flipboard.get("nav-type") == "caption"


def test_story_points_carry_caption_captured_sheet_and_sequential_id() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    points = root.findall(".//flipboard/story-points/story-point")
    assert len(points) == 2
    assert points[0].get("caption") == "Here's the headline."
    assert points[0].get("captured-sheet") == "Revenue by Region"
    assert points[0].get("id") == "1"
    assert points[1].get("captured-sheet") == "Top 10 Customers"
    assert points[1].get("id") == "2"


def test_story_dashboard_has_simple_id() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    story = root.find(".//dashboard[@type='storyboard']")
    assert story is not None
    assert story.find("simple-id") is not None


def test_story_window_entry_lists_captured_sheets_and_active_minus_one() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    win = root.find(".//windows/window[@name='Story: Q4 Review']")
    assert win is not None
    assert win.get("class") == "dashboard"
    vp_names = {vp.get("name") for vp in win.findall("viewpoints/viewpoint")}
    assert vp_names == {"Revenue by Region", "Top 10 Customers"}
    for vp in win.findall("viewpoints/viewpoint"):
        zoom = vp.find("zoom")
        assert zoom is not None
        assert zoom.get("type") == "entire-view"
    active = win.find("active")
    assert active is not None
    assert active.get("id") == "-1"
    assert win.find("simple-id") is not None


def test_story_window_viewpoints_deduplicate_repeated_captured_sheets() -> None:
    story_repeated = [
        {
            "name": "Story: Repeat",
            "points": [
                {"caption": "a", "captured_sheet": "Revenue by Region"},
                {"caption": "b", "captured_sheet": "Revenue by Region"},
            ],
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=story_repeated
    )
    root = ET.fromstring(xml)
    win = root.find(".//windows/window[@name='Story: Repeat']")
    assert win is not None
    vps = win.findall("viewpoints/viewpoint")
    assert len(vps) == 1
    assert vps[0].get("name") == "Revenue by Region"


def test_story_dashboard_and_window_simple_ids_are_globally_unique() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    root = ET.fromstring(xml)
    uuids = [el.get("uuid") for el in root.findall(".//simple-id")]
    assert len(uuids) == len(set(uuids)), f"duplicate simple-id uuid(s) found: {uuids}"


# ---------------------------------------------------------------------------
# Captured-sheet validation (fail loud)
# ---------------------------------------------------------------------------


def test_unknown_captured_sheet_raises_value_error_listing_valid_names() -> None:
    bad_story = [
        {
            "name": "Story: Bad",
            "points": [{"caption": "Oops", "captured_sheet": "Nonexistent Sheet"}],
        }
    ]
    with pytest.raises(ValueError) as excinfo:
        twb_builder.build_twb_xml(
            "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=bad_story
        )
    message = str(excinfo.value)
    assert "Nonexistent Sheet" in message
    assert "Revenue by Region" in message
    assert "Top 10 Customers" in message
    assert "Dashboard 1" in message


def test_captured_sheet_may_reference_a_regular_dashboard_name() -> None:
    """captured-sheet can point at a <dashboard> name, not only a worksheet."""
    story_on_dashboard = [
        {
            "name": "Story: Overview",
            "points": [{"caption": "The whole picture.", "captured_sheet": "Dashboard 1"}],
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=story_on_dashboard
    )
    root = ET.fromstring(xml)
    point = root.find(".//story-point")
    assert point is not None
    assert point.get("captured-sheet") == "Dashboard 1"


def test_story_with_no_points_raises_value_error() -> None:
    empty_story = [{"name": "Story: Empty", "points": []}]
    with pytest.raises(ValueError, match="at least one story point"):
        twb_builder.build_twb_xml(
            "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=empty_story
        )


# ---------------------------------------------------------------------------
# XSD validity across variants
# ---------------------------------------------------------------------------


def test_xsd_valid_dashboard_plus_story() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=STORY_BASIC
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_story_only_no_regular_dashboard() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_2, stories=STORY_BASIC)
    _assert_xsd_valid(xml)


def test_xsd_valid_story_referencing_dashboard_name() -> None:
    story_on_dashboard = [
        {
            "name": "Story: Overview",
            "points": [
                {"caption": "The whole picture.", "captured_sheet": "Dashboard 1"},
                {"caption": "Zoom in.", "captured_sheet": "Revenue by Region"},
            ],
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=story_on_dashboard
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_multiple_stories() -> None:
    two_stories = [
        *STORY_BASIC,
        {
            "name": "Story: Second",
            "points": [{"caption": "Another angle.", "captured_sheet": "Top 10 Customers"}],
        },
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=two_stories
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_embedded_variant(tmp_path: Path) -> None:
    """The embedded (federated/hyper) builder must also validate with a story."""
    hyper_path = tmp_path / "story_test.hyper"
    df = pd.DataFrame({"Region": ["West", "East"], "Revenue": [100.0, 200.0]})
    hyper_builder.dataframe_to_hyper(df, hyper_path)
    columns = hyper_builder.read_hyper_columns(hyper_path)
    sheets = [
        {
            "title": "Revenue by Region",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Revenue"],
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_path.name,
        columns=columns,
        sheets=sheets,
        dashboards=[{"name": "Dashboard 1", "titles": ["Revenue by Region"]}],
        stories=[
            {
                "name": "Story: Embedded",
                "points": [{"caption": "Headline.", "captured_sheet": "Revenue by Region"}],
            }
        ],
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_embedded_story_only_no_dashboard(tmp_path: Path) -> None:
    hyper_path = tmp_path / "story_only.hyper"
    df = pd.DataFrame({"Region": ["West", "East"], "Revenue": [100.0, 200.0]})
    hyper_builder.dataframe_to_hyper(df, hyper_path)
    columns = hyper_builder.read_hyper_columns(hyper_path)
    sheets = [
        {
            "title": "Revenue by Region",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Revenue"],
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_path.name,
        columns=columns,
        sheets=sheets,
        stories=[
            {
                "name": "Story: Solo",
                "points": [{"caption": "Headline.", "captured_sheet": "Revenue by Region"}],
            }
        ],
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# No-story regression: byte-identical determinism guard
# ---------------------------------------------------------------------------


def test_no_story_path_byte_identical_kwarg_omitted_vs_explicit_none() -> None:
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC, stories=None
    )
    assert xml_omitted == xml_explicit_none


def test_no_story_single_dashboard_still_single_dashboards_wrapper() -> None:
    """Regression guard for the _build_dashboard refactor (now returns
    <dashboard>, not <dashboards>): a single regular dashboard with no story
    must still produce exactly one <dashboards> element wrapping exactly one
    <dashboard>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_2, dashboards=DASHBOARDS_BASIC)
    root = ET.fromstring(xml)
    dashboards_els = root.findall("dashboards")
    assert len(dashboards_els) == 1
    assert len(dashboards_els[0].findall("dashboard")) == 1


def test_no_dashboards_no_stories_omits_dashboards_element_entirely() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_2)
    root = ET.fromstring(xml)
    assert root.find("dashboards") is None


def test_embedded_no_story_path_byte_identical_kwarg_omitted_vs_explicit_none(
    tmp_path: Path,
) -> None:
    hyper_path = tmp_path / "regress.hyper"
    df = pd.DataFrame({"Region": ["West"], "Revenue": [1.0]})
    hyper_builder.dataframe_to_hyper(df, hyper_path)
    columns = hyper_builder.read_hyper_columns(hyper_path)
    sheets = [
        {
            "title": "Revenue by Region",
            "mark_type": "bar",
            "cols": ["Region"],
            "rows": [],
            "measures": ["Revenue"],
        }
    ]
    dashboards = [{"name": "Dashboard 1", "titles": ["Revenue by Region"]}]

    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_path.name,
        columns=columns,
        sheets=sheets,
        dashboards=dashboards,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_path.name,
        columns=columns,
        sheets=sheets,
        dashboards=dashboards,
        stories=None,
    )
    assert xml_omitted == xml_explicit_none
