"""Design Excellence, Slice D7 — dashboard actions (cross-filter / highlight).

Mirrors two mined action shapes from ``design/corpus/recipes/actions.yaml``
(see ``twb_builder._build_actions``'s docstring for the full derivation +
mined-XML citations):

- ``tsc:tsl-filter`` cross-filter actions, one per chart worksheet on a
  dashboard with >=2 chart sheets (WB-118's
  ``WB-118.twbx`` "State FA"/"Cat FA"/... family
  + WB-114's ``WB-114.twbx`` Action4 "Map to Scatter Plot"
  self-exclusion evidence).
- ``tsc:brush`` highlight actions, one per chart worksheet carrying a color
  encoding (WB-114's ``WB-114.twbx`` Action1 "Highlight 1
  (generated)").

Emitted as a single workbook-level ``<actions>`` element positioned AFTER
``<datasources>`` and BEFORE ``<worksheets>`` (XSD ``Workbook-Actions-G``) —
this is the highest-render-risk slice per PLAN.md, so the position + XSD
validity assertions carry the most weight here.

Test groups
-----------
D7-1  Actions element position (after datasources, before worksheets)
D7-2  One cross-filter action per chart source; KPI tiles excluded from
      sources AND present in every exclude list
D7-3  tsc:tsl-filter param shape matches the mined shape (deep attr compare)
D7-4  tsc:brush highlight shape (mirrors "Highlight 1 (generated)")
D7-5  Deterministic naming/ordering (two independent builds are identical)
D7-6  Byte-identical guard when interactions is absent/None/all-False
      (both build_twb_xml and build_embedded_twb_xml)
D7-7  XSD validity for both entry points
D7-8  FastAPI integration: camelCase `interactions` -> 200 + actions present
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient
from lxml import etree

import hyper_builder
import twb_builder
from server import InteractionsModel
from server import app as fastapi_app

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_twb_design_theme.py's pattern)
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


def _interactions(**overrides: object) -> dict[str, object]:
    """Build an ``InteractionsModel.model_dump()`` (snake_case) dict.

    Constructing through the real Pydantic model (rather than a hand-rolled
    dict) keeps this fixture honest to the D7 wire shape — same discipline as
    ``test_twb_design_theme.py``'s ``_theme()`` helper.
    """
    base: dict[str, object] = {"cross_filter": False, "highlight": False}
    base.update(overrides)
    return InteractionsModel(**base).model_dump()  # type: ignore[arg-type]


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "Region": ["East", "West"],
            "Revenue": [1.0, 2.0],
            "Quarter": ["Q1", "Q2"],
            "Profit": [1.0, 2.0],
            "Category": ["Furniture", "Technology"],
        }
    )
    out = tmp_path / "actions.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Fixtures — sheets / dashboards
# ---------------------------------------------------------------------------

KPI_SHEET = {
    "title": "KPI Revenue",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Revenue"],
    "kpi": {"primary_measure": "Revenue"},
}
CHART_A = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
    "color": {"field": "Category", "kind": "dimension"},
}
CHART_B = {
    "title": "Profit Trend",
    "mark_type": "line",
    "cols": ["Quarter"],
    "rows": [],
    "measures": ["Profit"],
}
CHART_C = {
    "title": "Revenue by Category",
    "mark_type": "bar",
    "cols": ["Category"],
    "rows": [],
    "measures": ["Revenue"],
}
CHART_NO_COLOR = {
    "title": "Single Chart",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}

SHEETS_MIXED = [KPI_SHEET, CHART_A, CHART_B, CHART_C]
SHEETS_TWO_CHARTS = [CHART_A, CHART_B]
SHEETS_ONE_CHART = [CHART_NO_COLOR]

DASHBOARD_MIXED = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Revenue", "Revenue by Region", "Profit Trend", "Revenue by Category"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_TWO_CHARTS = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region", "Profit Trend"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_ONE_CHART = [
    {
        "name": "Dashboard 1",
        "titles": ["Single Chart"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Revenue", "Revenue by Region", "Profit Trend", "Revenue by Category"],
        "title": "Executive Dashboard",
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Revenue"],
            "chart_titles": ["Revenue by Region", "Profit Trend", "Revenue by Category"],
        },
    }
]

INTERACTIONS_CROSS_FILTER = _interactions(cross_filter=True)
INTERACTIONS_HIGHLIGHT = _interactions(highlight=True)
INTERACTIONS_BOTH = _interactions(cross_filter=True, highlight=True)


def _actions(xml: str) -> ET.Element | None:
    root = ET.fromstring(xml)
    return root.find("actions")


# ---------------------------------------------------------------------------
# D7-1  Actions element position (after datasources, before worksheets)
# ---------------------------------------------------------------------------


def test_actions_positioned_after_datasources_before_worksheets() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    root = ET.fromstring(xml)
    tags = [c.tag for c in root]
    assert "actions" in tags, tags
    assert tags.index("datasources") < tags.index("actions") < tags.index("worksheets"), tags


def test_actions_positioned_after_workbook_style_when_chrome_themed() -> None:
    """With a chrome-driven workbook-level <style> present too: preferences (if
    any) -> style -> datasources -> actions -> worksheets, in that order."""
    theme = {"name": "executive_dark", "chrome": {"hide_gridlines": True}}
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        design_theme=theme,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    root = ET.fromstring(xml)
    tags = [c.tag for c in root]
    assert (
        tags.index("style")
        < tags.index("datasources")
        < tags.index("actions")
        < tags.index("worksheets")
    ), tags


def test_actions_position_embedded_entry_point() -> None:
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename="x.hyper",
        columns=[],
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    root = ET.fromstring(xml)
    tags = [c.tag for c in root]
    assert tags.index("datasources") < tags.index("actions") < tags.index("worksheets"), tags


# ---------------------------------------------------------------------------
# D7-2  One cross-filter action per chart source; KPI tiles excluded from
#       sources AND present in every exclude list
# ---------------------------------------------------------------------------


def test_one_cross_filter_action_per_chart_source() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    action_els = actions_el.findall("action")
    assert len(action_els) == 3, [a.get("caption") for a in action_els]

    sources = {a.find("source").get("worksheet") for a in action_els}
    assert sources == {"Revenue by Region", "Profit Trend", "Revenue by Category"}
    assert "KPI Revenue" not in sources, "KPI tile must never be a cross-filter source"


def test_kpi_tile_present_in_every_exclude_list_and_not_a_target() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    for action_el in actions_el.findall("action"):
        command_el = action_el.find("command")
        exclude_param = next(p for p in command_el.findall("param") if p.get("name") == "exclude")
        exclude_list = exclude_param.get("value").split(",")
        assert "KPI Revenue" in exclude_list, exclude_list


def test_no_actions_below_two_chart_sheet_threshold() -> None:
    """A dashboard with < 2 chart sheets gets zero cross-filter actions."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_ONE_CHART,
        dashboards=DASHBOARD_ONE_CHART,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    assert _actions(xml) is None


def test_cross_filter_respects_kpi_band_layout_grammar_split() -> None:
    """KPI/chart classification also works via layout_grammar.kpi_tile_titles/
    chart_titles (kpi_band_over_charts), not just sheet["kind"]."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    action_els = actions_el.findall("action")
    sources = {a.find("source").get("worksheet") for a in action_els}
    assert sources == {"Revenue by Region", "Profit Trend", "Revenue by Category"}
    for action_el in action_els:
        exclude_list = (
            next(
                p for p in action_el.find("command").findall("param") if p.get("name") == "exclude"
            )
            .get("value")
            .split(",")
        )
        assert "KPI Revenue" in exclude_list


# ---------------------------------------------------------------------------
# D7-3  tsc:tsl-filter param shape matches the mined shape (deep attr compare)
# ---------------------------------------------------------------------------


def test_cross_filter_action_shape_matches_mined_entries_verbatim() -> None:
    """Deep attribute compare against WB-118's "State FA"-family shape:
    <action caption name><activation auto-clear='true' type='on-select'/>
    <source dashboard type='sheet' worksheet/><command command='tsc:tsl-filter'>
    <param exclude/><param special-fields='all'/><param target=dashboard/>
    </command></action> — see design/corpus/recipes/actions.yaml.
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_CROSS_FILTER,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    action_el = next(
        a
        for a in actions_el.findall("action")
        if a.find("source").get("worksheet") == "Revenue by Region"
    )

    assert action_el.get("caption") == "Filter: Revenue by Region"
    assert action_el.get("name") == "[Action1]"
    assert [c.tag for c in action_el] == ["activation", "source", "command"]

    activation = action_el.find("activation")
    assert activation.attrib == {"auto-clear": "true", "type": "on-select"}

    source = action_el.find("source")
    assert source.attrib == {
        "dashboard": "Executive Dashboard",
        "type": "sheet",
        "worksheet": "Revenue by Region",
    }

    command = action_el.find("command")
    assert command.get("command") == "tsc:tsl-filter"
    params = {p.get("name"): p.get("value") for p in command.findall("param")}
    assert params == {
        "exclude": "KPI Revenue,Revenue by Region",
        "special-fields": "all",
        "target": "Executive Dashboard",
    }
    # Params appear in the same order as the mined exemplar: exclude,
    # special-fields, target.
    assert [p.get("name") for p in command.findall("param")] == [
        "exclude",
        "special-fields",
        "target",
    ]


# ---------------------------------------------------------------------------
# D7-4  tsc:brush highlight shape (mirrors "Highlight 1 (generated)")
# ---------------------------------------------------------------------------


def test_highlight_action_shape_matches_mined_entry_verbatim() -> None:
    """Deep attribute compare against WB-114's "Highlight 1 (generated)":
    tsc:brush, params field-captions + target only (no exclude), source with
    NO dashboard attribute, target = the worksheet's own name.
    """
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_HIGHLIGHT,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    action_els = actions_el.findall("action")
    # Only CHART_A ("Revenue by Region") carries a color block.
    assert len(action_els) == 1
    action_el = action_els[0]

    assert action_el.get("caption") == "Highlight: Revenue by Region"
    assert action_el.get("name") == "[Action1]"
    assert [c.tag for c in action_el] == ["activation", "source", "command"]

    activation = action_el.find("activation")
    assert activation.attrib == {"auto-clear": "true", "type": "on-select"}

    source = action_el.find("source")
    assert source.attrib == {"type": "sheet", "worksheet": "Revenue by Region"}
    assert "dashboard" not in source.attrib

    command = action_el.find("command")
    assert command.get("command") == "tsc:brush"
    params = {p.get("name"): p.get("value") for p in command.findall("param")}
    assert params == {"field-captions": "Category", "target": "Revenue by Region"}
    assert "exclude" not in params


def test_highlight_skips_chart_sheets_without_color_field() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_TWO_CHARTS,
        dashboards=DASHBOARD_TWO_CHARTS,
        interactions=INTERACTIONS_HIGHLIGHT,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    action_els = actions_el.findall("action")
    sources = {a.find("source").get("worksheet") for a in action_els}
    # Only CHART_A has a color block; CHART_B ("Profit Trend") is skipped.
    assert sources == {"Revenue by Region"}


def test_highlight_does_not_require_two_chart_sheets() -> None:
    """Highlight is per-sheet, self-contained — no >=2 chart gate (unlike
    cross-filter)."""
    single_chart_dashboard = [
        {
            "name": "Dashboard 1",
            "titles": ["Revenue by Region"],
            "title": None,
            "subtitle": None,
            "text_zones": [],
            "layout_grammar": None,
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [CHART_A],
        dashboards=single_chart_dashboard,
        interactions=INTERACTIONS_HIGHLIGHT,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    assert len(actions_el.findall("action")) == 1


# ---------------------------------------------------------------------------
# D7-5  Deterministic naming/ordering (two independent builds are identical)
# ---------------------------------------------------------------------------


def test_deterministic_two_builds_identical() -> None:
    xml_a = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_BOTH,
    )
    xml_b = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_BOTH,
    )
    assert xml_a == xml_b


def test_action_names_are_sequential_starting_at_one() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_BOTH,
    )
    actions_el = _actions(xml)
    assert actions_el is not None
    names = [a.get("name") for a in actions_el.findall("action")]
    assert names == [f"[Action{i}]" for i in range(1, len(names) + 1)]
    # Cross-filter actions (3, one per chart) precede highlight actions (1).
    assert len(names) == 4


# ---------------------------------------------------------------------------
# D7-6  Byte-identical guard when interactions is absent/None/all-False
# ---------------------------------------------------------------------------


def test_byte_identical_without_interactions_build_twb_xml() -> None:
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_TWO_CHARTS, dashboards=DASHBOARD_TWO_CHARTS
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_TWO_CHARTS, dashboards=DASHBOARD_TWO_CHARTS, interactions=None
    )
    xml_all_false = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_TWO_CHARTS,
        dashboards=DASHBOARD_TWO_CHARTS,
        interactions=_interactions(),
    )
    assert xml_omitted == xml_explicit_none == xml_all_false
    assert _actions(xml_omitted) is None


def test_byte_identical_without_interactions_build_embedded_twb_xml(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)

    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_TWO_CHARTS,
        dashboards=DASHBOARD_TWO_CHARTS,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_TWO_CHARTS,
        dashboards=DASHBOARD_TWO_CHARTS,
        interactions=None,
    )
    assert xml_omitted == xml_explicit_none
    assert _actions(xml_omitted) is None


# ---------------------------------------------------------------------------
# D7-7  XSD validity for both entry points
# ---------------------------------------------------------------------------


def test_xsd_valid_build_twb_xml_with_actions() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_BOTH,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_build_embedded_twb_xml_with_actions(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_MIXED,
        interactions=INTERACTIONS_BOTH,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_kpi_band_layout_with_actions() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        interactions=INTERACTIONS_BOTH,
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# D7-8  FastAPI integration: camelCase `interactions` -> 200 + actions present
# ---------------------------------------------------------------------------


def test_post_workbook_dashboard_with_interactions_returns_200_and_actions(
    tmp_path: Path,
) -> None:
    hyper_file = _build_hyper(tmp_path)

    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "Revenue by Region",
                "markType": "bar",
                "cols": ["Region"],
                "rows": [],
                "measures": ["Revenue"],
                "color": {"field": "Category", "kind": "dimension"},
            },
            {
                "title": "Profit Trend",
                "markType": "line",
                "cols": ["Quarter"],
                "rows": [],
                "measures": ["Profit"],
            },
        ],
        "interactions": {"crossFilter": True, "highlight": True},
    }

    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert (
        response.status_code == 200
    ), f"Expected 200 but got {response.status_code}. Response body: {response.text}"
    body = response.json()

    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")

    _assert_xsd_valid(twb_xml)
    actions_el = _actions(twb_xml)
    assert actions_el is not None
    action_els = actions_el.findall("action")
    # 2 cross-filter (one per chart) + 1 highlight (only Revenue by Region has color).
    assert len(action_els) == 3
    commands = {a.find("command").get("command") for a in action_els}
    assert commands == {"tsc:tsl-filter", "tsc:brush"}


def test_post_workbook_dashboard_without_interactions_returns_200_without_actions(
    tmp_path: Path,
) -> None:
    hyper_file = _build_hyper(tmp_path)
    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "Revenue by Region",
                "markType": "bar",
                "cols": ["Region"],
                "rows": [],
                "measures": ["Revenue"],
            },
            {
                "title": "Profit Trend",
                "markType": "line",
                "cols": ["Quarter"],
                "rows": [],
                "measures": ["Profit"],
            },
        ],
    }
    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200
    body = response.json()
    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")
    assert _actions(twb_xml) is None
