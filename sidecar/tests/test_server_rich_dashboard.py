"""Integration test: server → builder round-trip for rich dashboard fields.

Verifies the full data-flow path:
  camelCase JSON payload
    → DashboardWorkbookRequest.model_validate()    (Pydantic, accepts camelCase)
    → model_dump()                                  (produces snake_case)
    → build_embedded_twbx()                         (reads snake_case)
    → .twb XML assertions

ROOT CAUSE guarded here: prior to the fix, model_dump() produced snake_case keys
(geo_field, geo_role, color_measure, primary_measure, etc.) but the builder read
camelCase keys (geoField, geoRole, colorMeasure, primaryMeasure, etc.), causing
KeyError at runtime despite the unit tests passing (they used camelCase dicts
directly, bypassing model_dump).

This test explicitly goes through model_dump() so it will fail if the casing is
ever mismatched again.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import hyper_builder
import twb_builder
from server import DashboardWorkbookRequest, app

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _build_hyper(tmp_path: Path) -> Path:
    """Return a tiny .hyper with State (string), Sales (real), Revenue (real)."""
    df = pd.DataFrame(
        {
            "State": ["California", "Texas", "New York"],
            "Sales": [100.0, 200.0, 150.0],
            "Revenue": [500.0, 600.0, 450.0],
            "Category": ["Furniture", "Tech", "Office"],
        }
    )
    out = tmp_path / "test_rich.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Canonical camelCase payload (as the TypeScript client sends it)
# ---------------------------------------------------------------------------

_CAMEL_PAYLOAD = {
    "datasourceName": "Sales Data",
    "datasourceContentUrl": "sales_data",
    "sheets": [
        # KPI tile sheet — kpi binding uses camelCase aliases
        {
            "title": "Revenue KPI",
            "markType": "text",
            "kind": "kpi_tile",
            "cols": [],
            "rows": [],
            "measures": [],
            "kpi": {
                "primaryMeasure": "Revenue",
                "comparisonMeasure": "Sales",
                "deltaMeasure": "Sales",
            },
        },
        # Color-encoded bar sheet
        {
            "title": "Sales by Category",
            "markType": "bar",
            "cols": ["Category"],
            "rows": [],
            "measures": ["Sales"],
            "color": {"field": "Category", "kind": "dimension"},
        },
        # Filled-map sheet — geo binding uses camelCase aliases
        {
            "title": "Sales Map",
            "markType": "map_filled",
            "cols": [],
            "rows": [],
            "measures": [],
            "geo": {
                "geoField": "State",
                "geoRole": "state",
                "colorMeasure": "Sales",
            },
        },
    ],
    # Dashboard-level fields
    "dashboardTitle": "Executive Sales Dashboard",
    "dashboardSubtitle": "Q4 Performance",
    "layoutGrammar": {
        "kind": "kpi_band_over_charts",
        "kpiTileTitles": ["Revenue KPI"],
        "chartTitles": ["Sales by Category", "Sales Map"],
    },
    "dashboardLayout": "tiled_vertical",
    "canvasWidth": 1200,
    "canvasHeight": 900,
}


# ---------------------------------------------------------------------------
# A — model_dump() produces snake_case → builder does NOT raise KeyError
# ---------------------------------------------------------------------------


def test_model_dump_produces_snake_case_geo() -> None:
    """model_dump() on a camelCase geo payload must produce snake_case keys."""
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD)
    sheets = [s.model_dump() for s in req.sheets]
    map_sheet = next(s for s in sheets if s["title"] == "Sales Map")
    geo = map_sheet["geo"]
    assert "geo_field" in geo, (
        f"model_dump() must produce 'geo_field' (snake_case), got keys: {list(geo.keys())}"
    )
    assert "geo_role" in geo, (
        f"model_dump() must produce 'geo_role' (snake_case), got keys: {list(geo.keys())}"
    )
    assert "color_measure" in geo, (
        f"model_dump() must produce 'color_measure' (snake_case), got keys: {list(geo.keys())}"
    )
    # Confirm the camelCase aliases are NOT present in model_dump() output
    assert "geoField" not in geo, "model_dump() must not produce camelCase 'geoField'"
    assert "geoRole" not in geo, "model_dump() must not produce camelCase 'geoRole'"
    assert "colorMeasure" not in geo, "model_dump() must not produce camelCase 'colorMeasure'"


def test_model_dump_produces_snake_case_kpi() -> None:
    """model_dump() on a camelCase kpi payload must produce snake_case keys."""
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD)
    sheets = [s.model_dump() for s in req.sheets]
    kpi_sheet = next(s for s in sheets if s["title"] == "Revenue KPI")
    kpi = kpi_sheet["kpi"]
    assert "primary_measure" in kpi, (
        f"model_dump() must produce 'primary_measure', got keys: {list(kpi.keys())}"
    )
    assert "comparison_measure" in kpi, (
        f"model_dump() must produce 'comparison_measure', got keys: {list(kpi.keys())}"
    )
    assert "delta_measure" in kpi, (
        f"model_dump() must produce 'delta_measure', got keys: {list(kpi.keys())}"
    )
    assert "primaryMeasure" not in kpi, "model_dump() must not produce camelCase 'primaryMeasure'"


# ---------------------------------------------------------------------------
# B — build_embedded_twbx does NOT raise (the core integration path)
# ---------------------------------------------------------------------------


def test_build_embedded_twbx_succeeds_with_model_dump_sheets(tmp_path: Path) -> None:
    """build_embedded_twbx must not raise KeyError when sheets come from model_dump().

    This is the exact path the server handler follows:
      sheets = [s.model_dump() for s in req.sheets]
    """
    hyper_file = _build_hyper(tmp_path)
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD)

    sheets = [s.model_dump() for s in req.sheets]
    sheet_titles = [str(s["title"]) for s in sheets]

    # Build layout_grammar dict exactly as the server handler does
    layout_grammar_dict: dict[str, object] | None = None
    if req.layout_grammar is not None:
        layout_grammar_dict = {
            "kind": req.layout_grammar.kind,
            "kpi_tile_titles": req.layout_grammar.kpi_tile_titles,
            "chart_titles": req.layout_grammar.chart_titles,
        }

    dashboards = [
        {
            "name": "Dashboard 1",
            "titles": sheet_titles,
            "title": req.dashboard_title,
            "subtitle": req.dashboard_subtitle,
            "text_zones": None,
            "layout_grammar": layout_grammar_dict,
        }
    ]

    out_path = tmp_path / "output.twbx"
    # Must not raise KeyError
    result = twb_builder.build_embedded_twbx(
        datasource_name=req.datasource_name,
        hyper_path=hyper_file,
        sheets=sheets,
        out_path=out_path,
        dashboards=dashboards,
        dashboard_layout=req.dashboard_layout,
        canvas_width=req.canvas_width,
        canvas_height=req.canvas_height,
    )
    assert result.is_file(), "build_embedded_twbx must return an existing .twbx file"


# ---------------------------------------------------------------------------
# C — TWB XML structural assertions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rich_twb_xml(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Build the rich .twbx once and return its TWB XML string."""
    tmp_path = tmp_path_factory.mktemp("rich_dashboard")
    hyper_file = _build_hyper(tmp_path)
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD)

    sheets = [s.model_dump() for s in req.sheets]
    sheet_titles = [str(s["title"]) for s in sheets]

    layout_grammar_dict: dict[str, object] | None = None
    if req.layout_grammar is not None:
        layout_grammar_dict = {
            "kind": req.layout_grammar.kind,
            "kpi_tile_titles": req.layout_grammar.kpi_tile_titles,
            "chart_titles": req.layout_grammar.chart_titles,
        }

    dashboards = [
        {
            "name": "Dashboard 1",
            "titles": sheet_titles,
            "title": req.dashboard_title,
            "subtitle": req.dashboard_subtitle,
            "text_zones": None,
            "layout_grammar": layout_grammar_dict,
        }
    ]

    columns = hyper_builder.read_hyper_columns(hyper_file)
    return twb_builder.build_embedded_twb_xml(
        datasource_name=req.datasource_name,
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=sheets,
        dashboards=dashboards,
        dashboard_layout=req.dashboard_layout,
        canvas_width=req.canvas_width,
        canvas_height=req.canvas_height,
    )


def test_kpi_text_encoding_present(rich_twb_xml: str) -> None:
    """KPI tile sheet must emit at least one <text column='...'> encoding."""
    root = ET.fromstring(rich_twb_xml)
    # Find the Revenue KPI worksheet
    kpi_ws = next(
        (ws for ws in root.findall(".//worksheet") if ws.get("name") == "Revenue KPI"),
        None,
    )
    assert kpi_ws is not None, "Revenue KPI worksheet not found"
    text_els = kpi_ws.findall(".//pane/encodings/text")
    assert len(text_els) >= 1, (
        f"KPI tile must emit >=1 <text column='...'> encoding; got {len(text_els)}"
    )


def test_color_encoding_present(rich_twb_xml: str) -> None:
    """Color-encoded bar sheet must emit a <color column='...'> encoding."""
    root = ET.fromstring(rich_twb_xml)
    color_els = root.findall(".//pane/encodings/color")
    assert len(color_els) >= 1, (
        "Expected >=1 <color column='...'> encoding across worksheets"
    )


def test_map_class_present(rich_twb_xml: str) -> None:
    """Filled-map sheet must result in <mark class='Automatic'/> (map_filled path)."""
    root = ET.fromstring(rich_twb_xml)
    map_ws = next(
        (ws for ws in root.findall(".//worksheet") if ws.get("name") == "Sales Map"),
        None,
    )
    assert map_ws is not None, "Sales Map worksheet not found"
    # map_filled worksheets have Automatic mark class
    marks = map_ws.findall(".//pane/mark[@class='Automatic']")
    assert len(marks) >= 1, "Sales Map must have at least one <mark class='Automatic'/>"


def test_semantic_role_state_present(rich_twb_xml: str) -> None:
    """The geo dimension State must receive semantic-role='[State].[Name]'."""
    root = ET.fromstring(rich_twb_xml)
    state_col = next(
        (
            c
            for c in root.findall(".//datasources/datasource/column")
            if c.get("name") == "[State]"
        ),
        None,
    )
    assert state_col is not None, "[State] column not found in datasource"
    assert state_col.get("semantic-role") == "[State].[Name]", (
        f"Expected semantic-role='[State].[Name]', got {state_col.get('semantic-role')!r}"
    )


def test_dashboard_title_text_zone_present(rich_twb_xml: str) -> None:
    """Dashboard title must produce a type-v2='text' zone carrying the title text."""
    root = ET.fromstring(rich_twb_xml)
    text_zones = root.findall(".//dashboards//zone[@type-v2='text']")
    title_text = "Executive Sales Dashboard"
    found = any(
        (ft := z.find("formatted-text")) is not None
        and any(run.text and title_text in run.text for run in ft.findall("run"))
        for z in text_zones
    )
    assert found, (
        f"Dashboard title '{title_text}' not found in any text zone; "
        f"found {len(text_zones)} text zone(s)"
    )


def test_kpi_band_over_charts_layout_flow(rich_twb_xml: str) -> None:
    """kpi_band_over_charts layout must produce nested param='horz' layout-flow zones."""
    root = ET.fromstring(rich_twb_xml)
    horz_flows = root.findall(".//dashboards//zone[@type-v2='layout-flow'][@param='horz']")
    assert len(horz_flows) >= 2, (
        f"kpi_band_over_charts must produce >=2 param='horz' layout-flow zones; "
        f"got {len(horz_flows)}"
    )


def test_viewpoints_list_all_worksheets(rich_twb_xml: str) -> None:
    """Dashboard window <viewpoints> must list every worksheet by name."""
    root = ET.fromstring(rich_twb_xml)
    expected_titles = {"Revenue KPI", "Sales by Category", "Sales Map"}
    for win in root.findall(".//windows/window[@class='dashboard']"):
        viewpoints_el = win.find("viewpoints")
        assert viewpoints_el is not None, "Dashboard window missing <viewpoints>"
        vp_names = {vp.get("name") for vp in viewpoints_el.findall("viewpoint")}
        assert expected_titles == vp_names, (
            f"Viewpoints {vp_names!r} must match all worksheet titles {expected_titles!r}"
        )


# ---------------------------------------------------------------------------
# D — FastAPI TestClient end-to-end: POST /workbook/dashboard returns 200
# ---------------------------------------------------------------------------


def test_post_workbook_dashboard_returns_200(tmp_path: Path) -> None:
    """POST /workbook/dashboard with a rich payload must return 200 (not 500).

    Uses hyperPath so the workbook embeds the extract directly.
    """
    hyper_file = _build_hyper(tmp_path)

    payload = {
        **_CAMEL_PAYLOAD,
        "hyperPath": str(hyper_file),
    }

    client = TestClient(app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        f"Response body: {response.text}"
    )
    body = response.json()
    assert "path" in body, f"Response must have 'path' key; got {body!r}"
    assert body["path"].endswith(".twbx"), (
        f"Response path must end with .twbx; got {body['path']!r}"
    )


# ---------------------------------------------------------------------------
# E — Phase E1, Slice B: camelCase `brand` block round-trips through
#     model_dump() (snake_case) and reaches the generated .twbx.
# ---------------------------------------------------------------------------

_BRAND_CAMEL = {
    "palette": {
        "categorical": ["#4e79a7", "#f28e2b", "#e15759"],
        "sequential": ["#c6dbef", "#6baed6", "#08519c"],
        "diverging": ["#e15759", "#f2f2f2", "#59a14f"],
        "good": "#59a14f",
        "bad": "#e15759",
        "neutral": "#898989",
    },
    "typography": {
        "title": {"font": "Tableau Bold", "size": 24, "color": "#1f1f1f"},
        "body": {"font": "Tableau Book", "size": 11, "color": "#4d4d4d"},
        "ban": {"font": "Tableau Bold", "size": 36},
    },
    "formats": {
        "currency": "$#,##0",
        "percent": "0.0%",
        "number": "#,##0",
    },
    "brandName": "Acme Corp",
}

_CAMEL_PAYLOAD_WITH_BRAND = {**_CAMEL_PAYLOAD, "brand": _BRAND_CAMEL}


def test_model_dump_produces_snake_case_brand_name() -> None:
    """model_dump() on a camelCase brand payload must produce 'brand_name', not 'brandName'.

    Same root-cause guard as test_model_dump_produces_snake_case_geo/kpi above,
    extended to the Phase E1 brand block.
    """
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD_WITH_BRAND)
    assert req.brand is not None
    brand_dict = req.brand.model_dump()
    assert "brand_name" in brand_dict, (
        f"model_dump() must produce 'brand_name' (snake_case), got keys: {list(brand_dict.keys())}"
    )
    assert brand_dict["brand_name"] == "Acme Corp"
    assert "brandName" not in brand_dict, "model_dump() must not produce camelCase 'brandName'"


def test_brand_absent_when_not_supplied() -> None:
    """Requests without a brand block must parse with brand=None (no error)."""
    req = DashboardWorkbookRequest.model_validate(_CAMEL_PAYLOAD)
    assert req.brand is None


def test_post_workbook_dashboard_with_brand_returns_200_and_applies_preferences(
    tmp_path: Path,
) -> None:
    """POST /workbook/dashboard with a brand block must return 200 and the
    generated .twbx must carry the branded <preferences><color-palette>."""
    hyper_file = _build_hyper(tmp_path)

    payload = {
        **_CAMEL_PAYLOAD_WITH_BRAND,
        "hyperPath": str(hyper_file),
    }

    client = TestClient(app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. Response body: {response.text}"
    )
    body = response.json()

    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")

    root = ET.fromstring(twb_xml)
    palette = root.find("preferences/color-palette")
    assert palette is not None, "Branded .twbx must carry <preferences><color-palette>"
    assert palette.get("name") == "Acme Corp Palette"
    assert palette.get("custom") == "true"
    colors = [c.text for c in palette.findall("color")]
    assert colors == _BRAND_CAMEL["palette"]["categorical"]

    # Title run must carry the branded typography (fontcolor/fontname/fontsize).
    title_run = next(
        r
        for z in root.findall(".//dashboards//zone[@type-v2='text']")
        for r in z.find("formatted-text").findall("run")
        if r.text and "Executive Sales Dashboard" in r.text
    )
    assert title_run.get("fontname") == "Tableau Bold"
    assert title_run.get("fontcolor") == "#1f1f1f"
    assert title_run.get("fontsize") == "24"
