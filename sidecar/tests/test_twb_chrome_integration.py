"""Design Excellence, Slice D3 — chrome rules integration + defensive fields.

Split out of ``test_twb_chrome.py`` (D3-1..D3-10 cover the core builder
unit-level XML shape; this file covers D3-11/D3-12) to keep each file under
the ~800-line file-size guideline.

Test groups
-----------
D3-11 Integration: POST /workbook/dashboard with a camelCase designTheme.chrome block
D3-12 Optional defensive chrome.title_color workbook-level style-rule (builder-only)

See ``test_twb_chrome.py``'s module docstring for the full mined-vocabulary
provenance and the mark-labels/datalabel scoping rationale.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from lxml import etree

import hyper_builder
import twb_builder
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
            "Region": ["East", "West"],
            "Revenue": [1.0, 2.0],
            "Quarter": ["Q1", "Q2"],
            "Profit": [1.0, 2.0],
        }
    )
    out = tmp_path / "chrome_integration.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


def _raw_theme(chrome: dict[str, Any]) -> dict[str, Any]:
    """A hand-built (non-Pydantic) design_theme dict — see ``test_twb_chrome.py``'s
    twin helper docstring for why this is used for the defensive ``.get()``
    reads (``chrome.title_color``) that Slice D1's ``ThemeChromeModel``
    doesn't declare yet."""
    return {"name": "raw", "chrome": chrome}


CHART_SHEET_BAR = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}
CHART_SHEET_LINE = {
    "title": "Profit Trend",
    "mark_type": "line",
    "cols": ["Quarter"],
    "rows": [],
    "measures": ["Profit"],
}
SHEETS_BASIC = [CHART_SHEET_BAR, CHART_SHEET_LINE]
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
THEME_CHROME_FULL = {
    "name": "executive_dark",
    "chrome": {
        "hide_gridlines": True,
        "hide_zeroline": True,
        "hide_axis_ticks": True,
        "show_mark_labels": True,
        "datalabel": {"font_size": 11, "font_weight": "bold", "color_mode": "auto"},
    },
}


# ---------------------------------------------------------------------------
# D3-11  Integration: POST /workbook/dashboard with camelCase designTheme.chrome
# ---------------------------------------------------------------------------

_DESIGN_THEME_CHROME_CAMEL = {
    "name": "executive_dark",
    "chrome": {
        "hideGridlines": True,
        "hideZeroline": True,
        "hideAxisTicks": True,
        "showMarkLabels": True,
        "datalabel": {"fontSize": 11, "fontWeight": "bold", "colorMode": "auto"},
    },
}


def test_post_workbook_dashboard_with_design_theme_chrome_returns_200(
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
        "designTheme": _DESIGN_THEME_CHROME_CAMEL,
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

    style = root.find("style")
    assert style is not None
    assert {r.get("element") for r in style.findall("style-rule")} >= {"gridline", "zeroline"}

    worksheet = root.find(".//worksheets/worksheet[@name='Revenue by Region']")
    assert worksheet is not None
    assert worksheet.find("table/style/style-rule[@element='axis']") is not None
    assert worksheet.find("table/panes/pane/style/style-rule[@element='mark']") is not None
    assert worksheet.find("table/panes/pane/style/style-rule[@element='datalabel']") is not None


def test_design_theme_chrome_absent_returns_200_without_style(tmp_path: Path) -> None:
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
    assert root.find("style") is None
    assert root.find(".//worksheets/worksheet/table/panes/pane/style") is None


# ---------------------------------------------------------------------------
# D3-12  Optional defensive chrome.title_color workbook-level style-rule
# ---------------------------------------------------------------------------


def test_title_color_style_rule_when_present_via_raw_theme() -> None:
    """Mirrors WB-117's /workbook/style/style-rule[1] (element='title',
    attr='color'). Slice D1's ThemeChromeModel doesn't declare title_color
    yet, so this is exercised via a hand-built raw dict (builder-only support;
    see the D3 report for the deferred TS/Pydantic wiring)."""
    theme = _raw_theme({"title_color": "#2f2e41"})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    style = root.find("style")
    assert style is not None
    title_rule = style.find("style-rule[@element='title']")
    assert title_rule is not None
    formats = {f.get("attr"): f.get("value") for f in title_rule.findall("format")}
    assert formats == {"color": "#2f2e41"}


def test_title_color_absent_when_not_set() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_PLAIN,
        design_theme=THEME_CHROME_FULL,
    )
    root = ET.fromstring(xml)
    style = root.find("style")
    assert style is not None
    assert style.find("style-rule[@element='title']") is None


@pytest.mark.parametrize("empty_title_color", ["", None])
def test_title_color_falsy_values_do_not_emit_title_rule(empty_title_color: str | None) -> None:
    theme = _raw_theme({"hide_gridlines": True, "title_color": empty_title_color})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_PLAIN, design_theme=theme
    )
    root = ET.fromstring(xml)
    style = root.find("style")
    assert style is not None
    assert style.find("style-rule[@element='title']") is None
