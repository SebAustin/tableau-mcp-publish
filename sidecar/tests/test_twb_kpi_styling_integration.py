"""Design Excellence, Slice D4 — styled KPI tiles: XSD validity + integration.

Split out of ``test_twb_kpi_styling.py`` (D4-1..D4-8 cover the core builder
unit-level XML shape; this file covers D4-9/D4-10) to keep each file under
the ~800-line file-size guideline — same discipline as
``test_twb_chrome.py`` / ``test_twb_chrome_integration.py`` (Slice D3). See
``test_twb_kpi_styling.py``'s module docstring for the full mined-vocabulary
provenance, the BAN-styling deviation rationale, and the delta-color
assessment.

Test groups
-----------
D4-9  XSD validity (both entry points)
D4-10 Integration: POST /workbook/dashboard with camelCase designTheme+brand
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
from server import BrandModel, DesignThemeModel
from server import app as fastapi_app

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
    out = tmp_path / "kpi_styling_integration.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


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

THEME_KPI = DesignThemeModel(
    name="executive_dark",
    kpi_tile={  # type: ignore[arg-type]
        "background": "#2f2e41",
        "border": {"color": "#000000", "style": "none", "width": 0},
        "padding": 4,
        "ban_color": "#ffffff",
        "use_semantic_delta_colors": True,
    },
).model_dump()

BRAND = BrandModel(
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


# ---------------------------------------------------------------------------
# D4-9  XSD validity (both entry points)
# ---------------------------------------------------------------------------


def test_xsd_valid_kpi_styled_dashboard_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_kpi_styled_dashboard_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_MIXED,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# D4-10  Integration: POST /workbook/dashboard, camelCase designTheme + brand
# ---------------------------------------------------------------------------

_DESIGN_THEME_KPI_CAMEL = {
    "name": "executive_dark",
    "kpiTile": {
        "background": "#2f2e41",
        "border": {"color": "#000000", "style": "none", "width": 0},
        "padding": 4,
        "banColor": "#ffffff",
        "useSemanticDeltaColors": True,
    },
}
_BRAND_CAMEL = {
    "palette": {"categorical": ["#4e79a7"], "good": "#59a14f", "bad": "#e15759"},
    "typography": {
        "title": {"font": "Tableau Bold", "size": 24, "color": "#1f1f1f"},
        "body": {"font": "Tableau Book", "size": 11, "color": "#4d4d4d"},
        "ban": {"font": "Tableau Bold", "size": 36},
    },
    "formats": {"currency": "$#,##0", "percent": "0.0%", "number": "#,##0"},
    "brandName": "Acme Corp",
}


def test_post_workbook_dashboard_with_kpi_tile_theme_returns_200(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "KPI Sales",
                "markType": "text",
                "kind": "kpi_tile",
                "cols": [],
                "rows": [],
                "measures": ["Sales"],
                "kpi": {"primaryMeasure": "Sales", "deltaMeasure": "Sales Delta"},
            },
            {
                "title": "Revenue by Region",
                "markType": "bar",
                "cols": ["Region"],
                "rows": [],
                "measures": ["Sales"],
            },
        ],
        "dashboardTitle": "Executive Dashboard",
        "layoutGrammar": {
            "kind": "kpi_band_over_charts",
            "kpiTileTitles": ["KPI Sales"],
            "chartTitles": ["Revenue by Region"],
        },
        "designTheme": _DESIGN_THEME_KPI_CAMEL,
        "brand": _BRAND_CAMEL,
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

    tile_zone = root.find(".//dashboards/dashboard/zones//zone[@name='KPI Sales']")
    assert tile_zone is not None
    assert tile_zone.find("zone-style") is not None

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None

    # Cosmetic BAN typography: the field-scoped cell rule (live probe #2
    # confirmed this DOES render on a naked BAN view).
    cell_rule = worksheet.find("table/style/style-rule[@element='cell']")
    assert cell_rule is not None
    attrs = {f.get("attr") for f in cell_rule.findall("format")}
    assert {"font-size", "font-family", "color"} <= attrs

    # Number formatting: the worksheet-LOCAL default-format override (live
    # probe #2 hotfix location — the cell rule above does NOT carry
    # text-format; a competing default-format silently wins over it on a
    # naked BAN view, so the compact/arrow patterns live here instead).
    local_columns = {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }
    assert local_columns.get("[Sales]") == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert local_columns.get("[Sales Delta]") == "*▲ #,##;▼ #,##"
    assert "text-format" not in attrs

    assert worksheet.find("table/style/style-rule[@element='title']") is not None


def test_post_workbook_dashboard_without_kpi_tile_returns_200_unstyled(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "KPI Sales",
                "markType": "text",
                "kind": "kpi_tile",
                "cols": [],
                "rows": [],
                "measures": ["Sales"],
                "kpi": {"primaryMeasure": "Sales"},
            },
        ],
        "layoutGrammar": {
            "kind": "kpi_band_over_charts",
            "kpiTileTitles": ["KPI Sales"],
            "chartTitles": [],
        },
    }
    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200
    body = response.json()
    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")
    root = ET.fromstring(twb_xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='cell']") is None
