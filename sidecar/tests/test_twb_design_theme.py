"""Design Excellence, Slice D2 — zone-style box model + spacer zones.

Mirrors the mined vocabulary in ``design/corpus/recipes/zone_styles.yaml``:
``<zone-style>`` (LAST child of a ``<zone>``) containing sorted
``<format attr='...' value='...'/>`` children built from
``background-color``/``border-color``/``border-style``/``border-width``/
``margin``/``padding``/``corner-radius`` — the mined attribute vocabulary
only, never the XSD-illegal ``_.fcp.DashboardRoundedCorners...`` element form.

Test groups
-----------
D2-1  No-theme byte-identical guards (``build_twb_xml`` / ``build_embedded_twb_xml``)
D2-2  Canvas zone background-color from ``design_theme.dashboard_background``
D2-3  Chart/worksheet zone-style formats mirror ``chart_card``
D2-4  ``<zone-style>`` is the LAST child of its zone
D2-5  KPI tiles are NOT styled in D2 (chart zones ARE)
D2-6  Gutter margin vs. explicit ``chart_card.margin`` precedence
D2-7  Deterministic (sorted) ``<format>`` ordering
D2-8  XSD validity for themed dashboards (sqlproxy + embedded)
D2-9  Integration: POST /workbook/dashboard with a camelCase ``designTheme`` block
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from lxml import etree

import hyper_builder
import twb_builder
from server import DesignThemeModel
from server import app as fastapi_app

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_twb_dashboard_layout.py's pattern)
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


# ---------------------------------------------------------------------------
# Fixtures — sheets / dashboards / theme dicts
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
KPI_SHEET_2 = {
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
    "measures": ["Revenue"],
}
CHART_SHEET_2 = {
    "title": "Profit Trend",
    "mark_type": "line",
    "cols": ["Quarter"],
    "rows": [],
    "measures": ["Profit"],
}
SHEETS_BASIC = [CHART_SHEET, CHART_SHEET_2]
SHEETS_MIXED = [KPI_SHEET, KPI_SHEET_2, CHART_SHEET, CHART_SHEET_2]

DASHBOARD_PLAIN = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region", "Profit Trend"],
        "title": None,
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

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


def _theme(**overrides: object) -> dict[str, object]:
    """Build a ``DesignThemeModel.model_dump()`` (snake_case) dict.

    Constructing through the real Pydantic model (rather than a hand-rolled
    dict) keeps this fixture honest to the D1 wire shape — same discipline as
    ``test_server_rich_dashboard.py``'s MODEL_DUMP LESSON.
    """
    base: dict[str, object] = {
        "name": "executive_dark",
        "dashboard_background": "#2f2e41",
        "spacing": {"outer_margin": 25, "gutter": 30},
        "chart_card": {
            "background": "#f5f3f4",
            "border": {"color": "#000000", "style": "none", "width": 0},
            "padding": 25,
            "margin": 0,
            "corner_radius": None,
        },
    }
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_FULL = _theme()


# ---------------------------------------------------------------------------
# D2-1  No-theme byte-identical guards
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_build_twb_xml() -> None:
    """Omitting design_theme, passing it explicitly as None, and the pre-D2 call
    signature must all produce byte-identical XML from build_twb_xml."""
    xml_pre_d2 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN
    )
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=None
    )
    assert xml_pre_d2 == xml_omitted == xml_explicit_none


def test_no_theme_byte_identical_build_embedded_twb_xml(tmp_path: Path) -> None:
    """Same byte-identical guard for build_embedded_twb_xml."""
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)

    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "Region": ["East", "West"],
            "Revenue": [1.0, 2.0],
            "Quarter": ["Q1", "Q2"],
            "Profit": [1.0, 2.0],
        }
    )
    out = tmp_path / "design_theme.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# D2-2  Canvas zone background-color from dashboard_background
# ---------------------------------------------------------------------------


def test_canvas_zone_background_from_theme() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)
    canvas = root.find(".//dashboards/dashboard/zones/zone[@type-v2='layout-basic']")
    assert canvas is not None
    zone_style = canvas.find("zone-style")
    assert zone_style is not None, "Canvas zone must carry a <zone-style> when themed"
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats == {"background-color": "#2f2e41"}


# ---------------------------------------------------------------------------
# D2-3  Chart-zone formats mirror chart_card
# ---------------------------------------------------------------------------


def test_chart_zone_style_formats_match_chart_card() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)
    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    zone_style = chart_zone.find("zone-style")
    assert zone_style is not None
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    # chart_card.margin=0 is explicitly set → wins over spacing.gutter=30.
    assert formats == {
        "background-color": "#f5f3f4",
        "border-color": "#000000",
        "border-style": "none",
        "border-width": "0",
        "padding": "25",
        "margin": "0",
    }
    # corner_radius was None in THEME_FULL → must be absent entirely (never a
    # placeholder zero/empty value).
    assert "corner-radius" not in formats


def test_chart_zone_style_formats_include_corner_radius_when_set() -> None:
    theme = _theme(chart_card={
        "background": "#ffffff",
        "border": None,
        "padding": None,
        "margin": None,
        "corner_radius": 8,
    })
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    zone_style = chart_zone.find("zone-style")
    assert zone_style is not None
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats["corner-radius"] == "8"
    assert formats["background-color"] == "#ffffff"
    assert "border-color" not in formats
    assert "padding" not in formats
    # No chart_card.margin set → falls back to spacing.gutter (30, from _theme's base).
    assert formats["margin"] == "30"


# ---------------------------------------------------------------------------
# D2-4  <zone-style> is the LAST child of its zone
# ---------------------------------------------------------------------------


def test_zone_style_is_last_child_of_zone() -> None:
    """A themed worksheet zone carries <layout-cache> AND <zone-style>; the
    zone-style must be the last child (XSD Zone-ZoneStyle-G group ordering)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)
    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    children_tags = [c.tag for c in chart_zone]
    assert children_tags == ["layout-cache", "zone-style"], children_tags


def test_zone_style_is_last_child_of_canvas_zone() -> None:
    """The canvas (layout-basic) zone's zone-style must also be last, after
    its layout-flow child."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)
    canvas = root.find(".//dashboards/dashboard/zones/zone[@type-v2='layout-basic']")
    assert canvas is not None
    assert canvas[-1].tag == "zone-style"


# ---------------------------------------------------------------------------
# D2-5  KPI tiles are NOT styled in D2; chart zones ARE
#
# Slice D4 (Design Excellence) later CONSUMES ``design_theme["kpi_tile"]`` to
# style KPI tiles too (see ``test_twb_kpi_styling.py``) — but that is
# conditioned on a ``kpi_tile`` block actually being present on the theme.
# ``THEME_FULL`` here has NO ``kpi_tile`` block (see ``_theme()``'s base
# dict above), so the "KPI tiles stay unstyled" assertion below remains true
# from D2 onward AS A "NO KPI_TILE BLOCK" GUARD, not a "D2 in general" fact —
# renamed accordingly per the D4 plan's instruction to adjust this test
# honestly rather than delete it silently.
# ---------------------------------------------------------------------------


def test_kpi_tiles_unstyled_without_kpi_tile_block() -> None:
    """Chart zones ARE styled by ``chart_card``; KPI tile zones stay fully
    unstyled when the theme carries no ``kpi_tile`` block at all (D2's
    original behavior, still correct post-D4 — see ``test_twb_kpi_styling.py``
    for the "kpi_tile block present -> tiles ARE styled" positive case)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)

    kpi_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Revenue']")
    assert kpi_zone is not None
    assert kpi_zone.find("zone-style") is None, (
        "KPI tiles must NOT be styled when design_theme has no kpi_tile block"
    )

    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    assert chart_zone.find("zone-style") is not None, "Chart zones MUST be styled in Slice D2"


# ---------------------------------------------------------------------------
# D2-6  Gutter margin vs. explicit chart_card.margin precedence
# ---------------------------------------------------------------------------


def test_gutter_margin_applied_and_chart_card_margin_wins() -> None:
    # (a) chart_card has no margin → gutter (30) is used.
    theme_no_margin = _theme(
        chart_card={
            "background": "#f5f3f4",
            "border": None,
            "padding": None,
            "margin": None,
            "corner_radius": None,
        }
    )
    xml_a = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme_no_margin
    )
    root_a = ET.fromstring(xml_a)
    zone_a = root_a.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert zone_a is not None
    formats_a = {f.get("attr"): f.get("value") for f in zone_a.find("zone-style").findall("format")}
    assert formats_a["margin"] == "30"

    # (b) chart_card.margin explicitly set (5) → wins over gutter (30).
    theme_with_margin = _theme(
        chart_card={
            "background": "#f5f3f4",
            "border": None,
            "padding": None,
            "margin": 5,
            "corner_radius": None,
        }
    )
    xml_b = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme_with_margin
    )
    root_b = ET.fromstring(xml_b)
    zone_b = root_b.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert zone_b is not None
    formats_b = {f.get("attr"): f.get("value") for f in zone_b.find("zone-style").findall("format")}
    assert formats_b["margin"] == "5"


def test_outer_margin_applied_to_outer_layout_flow_zone() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    root = ET.fromstring(xml)
    outer_flow = root.find(
        ".//dashboards/dashboard/zones"
        "/zone[@type-v2='layout-basic']/zone[@type-v2='layout-flow']"
    )
    assert outer_flow is not None
    zone_style = outer_flow.find("zone-style")
    assert zone_style is not None
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats == {"margin": "25"}


# ---------------------------------------------------------------------------
# D2-7  Deterministic (sorted) <format> ordering
# ---------------------------------------------------------------------------


def test_zone_style_formats_sorted_deterministic() -> None:
    xml_1 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    xml_2 = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=THEME_FULL
    )
    assert xml_1 == xml_2

    root = ET.fromstring(xml_1)
    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    attrs = [f.get("attr") for f in chart_zone.find("zone-style").findall("format")]
    assert attrs == sorted(attrs), f"<format> children must be sorted alphabetically: {attrs}"


# ---------------------------------------------------------------------------
# D2-8  XSD validity for themed dashboards
# ---------------------------------------------------------------------------


def test_xsd_valid_themed_dashboard() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_MIXED, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_FULL
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_themed_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_FULL,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_themed_kpi_band_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_FULL,
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# D2-9  Integration: POST /workbook/dashboard with a camelCase designTheme
# ---------------------------------------------------------------------------

_DESIGN_THEME_CAMEL = {
    "name": "executive_dark",
    "dashboardBackground": "#2f2e41",
    "spacing": {"outerMargin": 25, "gutter": 30},
    "chartCard": {
        "background": "#f5f3f4",
        "border": {"color": "#000000", "style": "none", "width": 0},
        "padding": 25,
        "margin": 0,
    },
}


def test_post_workbook_dashboard_with_design_theme_returns_200_and_applies_zone_style(
    tmp_path: Path,
) -> None:
    """POST /workbook/dashboard with a camelCase designTheme block must return
    200 and the generated .twbx must carry the themed <zone-style> elements."""
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
        "designTheme": _DESIGN_THEME_CAMEL,
    }

    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. Response body: {response.text}"
    )
    body = response.json()

    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")

    _assert_xsd_valid(twb_xml)
    root = ET.fromstring(twb_xml)
    chart_zone = root.find(".//dashboards/dashboard/zones//zone[@name='Revenue by Region']")
    assert chart_zone is not None
    zone_style = chart_zone.find("zone-style")
    assert zone_style is not None
    formats = {f.get("attr"): f.get("value") for f in zone_style.findall("format")}
    assert formats["background-color"] == "#f5f3f4"
    assert formats["margin"] == "0"

    canvas = root.find(".//dashboards/dashboard/zones/zone[@type-v2='layout-basic']")
    assert canvas is not None
    canvas_style = canvas.find("zone-style")
    assert canvas_style is not None
    canvas_formats = {f.get("attr"): f.get("value") for f in canvas_style.findall("format")}
    assert canvas_formats == {"background-color": "#2f2e41"}


@pytest.mark.parametrize("hex_key", ["dashboardBackground"])
def test_design_theme_absent_returns_200_without_zone_style(
    tmp_path: Path, hex_key: str
) -> None:
    """Requests without designTheme must still succeed and emit no <zone-style>."""
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
        ],
    }
    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200
    body = response.json()
    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")
    root = ET.fromstring(twb_xml)
    assert root.find(".//zone-style") is None
