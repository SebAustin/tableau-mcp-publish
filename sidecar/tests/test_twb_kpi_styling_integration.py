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
D4-11 FINAL SHAPE (live-probe #3's bisect ladder): pane-level mark-labels-
      show/cull rule gating + a two-KPI-tiles-same-datasource XSD case
      (moved here from ``test_twb_kpi_styling_customized_label.py``'s CL-9
      to keep that file under the ~800-line file-size guideline)
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
    # Design Excellence, Slice D4 FINAL SHAPE: the zone suppresses its own
    # title (mirrors WB-118's mined attribute) since the worksheet's
    # customized-label now carries an in-label caption.
    assert tile_zone.get("show-title") == "false"

    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None

    # Live probe #3: BAN typography (cosmetic font-size/font-name/color)
    # no longer lives on a table-level cell rule (proven ineffective for a
    # naked BAN view) — it's on the pane's <customized-label> instead.
    assert worksheet.find("table/style/style-rule[@element='cell']") is None
    label_runs = worksheet.findall(".//panes/pane/customized-label//run")
    # Design Excellence, Slice D4 FINAL SHAPE (live-probe #3's bisect
    # ladder, V11/V12): caption, separator, primary value, newline, delta
    # value, trailing newline.
    assert len(label_runs) == 6
    caption_run = label_runs[0]
    assert caption_run.text == "K P I   S A L E S"
    primary_run = label_runs[2]
    assert primary_run.get("fontcolor") == "#ffffff"
    assert primary_run.get("fontname") is None  # never emitted — see D4 FINAL SHAPE
    assert primary_run.get("fontsize") == "36"
    assert primary_run.text == "<[federated.Sales_Data].[usr:Calculation_BAN_Sales:qk]>"

    # Number formatting: the CALCULATED column's OWN default-format (FINAL
    # SHAPE — bisect V7 proved a RAW field's local default-format is
    # ignored by Cloud) — the placeholder run above renders using THIS
    # resolved default-format.
    local_columns = {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }
    assert local_columns.get("[Calculation_BAN_Sales]") == 'c"$"#,##0,.0K;-"$"#,##0,.0K'
    assert local_columns.get("[Calculation_BAN_Sales_Delta_Delta]") == "*▲ #,##;▼ #,##"
    # The raw fields' own local columns never get a default-format at all.
    assert local_columns.get("[Sales]") is None
    assert local_columns.get("[Sales Delta]") is None

    # Table-level style carries the transparency rule (live probe #2b) but
    # NOT the title rule — redundant now that the in-label caption + zone
    # show-title='false' suppression handle tile labeling.
    assert worksheet.find("table/style/style-rule[@element='title']") is None
    assert worksheet.find("table/style/style-rule[@element='table']") is not None

    # Design Excellence, Slice D4 FINAL SHAPE: pane-level mark-labels-show/
    # cull rule (required for the label to render at all) + selection-
    # relaxation-option.
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    assert pane.get("selection-relaxation-option") == "selection-relaxation-allow"
    assert pane.find("style/style-rule[@element='mark']") is not None


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


# ---------------------------------------------------------------------------
# D4-11  FINAL SHAPE (live-probe #3's bisect ladder): pane-level
# mark-labels-show/cull rule gating + two-KPI-tiles-same-datasource XSD case
# ---------------------------------------------------------------------------


def test_mark_labels_show_cull_rule_present_alongside_cell_rule() -> None:
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
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    pane_style = pane.find("style")
    assert pane_style is not None
    rule_elements = [r.get("element") for r in pane_style.findall("style-rule")]
    assert rule_elements == ["cell", "mark"]
    mark_rule = pane_style.find("style-rule[@element='mark']")
    assert mark_rule is not None
    formats = {f.get("attr"): f.get("value") for f in mark_rule.findall("format")}
    assert formats == {"mark-labels-show": "true", "mark-labels-cull": "true"}


def test_mark_labels_rule_absent_when_no_primary_measure() -> None:
    """The mark-labels rule is gated on the BAN label mechanism being
    active, not merely on kpi_tile theming being present — a degenerate
    sheet with no primary_measure gets the cell rule but not the mark rule."""
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
        "DS", "kpi_ds", "site", [sheet], dashboards=dashboard, design_theme=THEME_KPI, brand=BRAND
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Empty']")
    assert worksheet is not None
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    pane_style = pane.find("style")
    assert pane_style is not None
    rule_elements = {r.get("element") for r in pane_style.findall("style-rule")}
    assert rule_elements == {"cell"}


def test_xsd_valid_two_kpi_tiles_same_raw_primary_field(tmp_path: Path) -> None:
    """A second KPI tile referencing the SAME raw primary field as another
    sheet must still validate — each worksheet's calc column lives in its
    OWN local <datasource-dependencies>, so there is no name collision at
    the workbook level even though both worksheets say 'Sales'."""
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    second_tile = {**KPI_SHEET_SALES, "title": "KPI Sales Two"}
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Sales", "KPI Sales Two", "Revenue by Region"],
            "layout_grammar": {
                "kind": "kpi_band_over_charts",
                "kpi_tile_titles": ["KPI Sales", "KPI Sales Two"],
                "chart_titles": ["Revenue by Region"],
            },
        }
    ]
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=[KPI_SHEET_SALES, second_tile, CHART_SHEET],
        dashboards=dashboard,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)
