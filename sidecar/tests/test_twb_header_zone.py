"""Design Excellence, Slice D5 — themed header band (multi-run text zone).

Mirrors the mined MULTI-RUN title-zone vocabulary in
``design/corpus/recipes/text_zones.yaml`` at
``/workbook/dashboards/dashboard[2]/zones/zone[4]/zone[1]/zone[1]`` (source
``WB-117``, cited verbatim by
``design/corpus/themes/executive_dark.yaml``'s header provenance entry): a
bold title run, a bare glyph-separator run (``"Æ  "``), and a plain subtitle
run — all inside ONE ``<formatted-text>``, replacing the pre-D5 separate
title-zone/subtitle-zone pair whenever ``design_theme.header`` is present.

Test groups
-----------
D5-1  No-theme / no-header byte-identical guards (separate-zone path unchanged)
D5-2  Header present + title + subtitle -> ONE zone, 3 runs
D5-3  Header present + title only (no subtitle) -> ONE zone, 1 run
D5-4  Header zone-style background-color from header.background, LAST child
D5-5  Header zone height mirrors the mined title_height_ratio median, h=6960
      (Slice T2 fix — superseded the D5-original single-exemplar h=9722;
      vs. non-themed h=6000)
D5-6  Brand typography still drives font family/size; header overrides fontcolor
D5-7  XSD validity (build_twb_xml + build_embedded_twb_xml)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree

import hyper_builder
import twb_builder
from server import DesignThemeModel

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def _load_schema() -> etree.XMLSchema:
    xsd_doc = etree.parse(str(XSD_PATH), _safe_parser())
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str) -> None:
    schema = _load_schema()
    doc = etree.fromstring(xml_str.encode(), _safe_parser())
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED:\n{errors}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SHEETS_BASIC = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARD_TITLE_SUBTITLE = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region"],
        "title": "Executive Overview",
        "subtitle": "Q4 Performance",
        "text_zones": [],
        "layout_grammar": None,
    }
]

DASHBOARD_TITLE_ONLY = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region"],
        "title": "Executive Overview",
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": None,
    }
]

BRAND: dict[str, Any] = {
    "typography": {
        "title": {"font": "Tableau Bold", "size": 24, "color": "#1f1f1f"},
        "body": {"font": "Tableau Book", "size": 11, "color": "#4d4d4d"},
    },
}


def _theme(**overrides: object) -> dict[str, object]:
    """Build a ``DesignThemeModel.model_dump()`` (snake_case) dict, same
    discipline as ``test_twb_design_theme.py``'s ``_theme()``."""
    base: dict[str, object] = {"name": "executive_dark"}
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_WITH_HEADER = _theme(
    header={
        "background": "#2f2e41",
        "title_color": "#ffffff",
        "subtitle_color": "#ffffff",
    }
)

THEME_NO_HEADER = _theme(dashboard_background="#2f2e41")


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame({"Region": ["East", "West"], "Revenue": [1.0, 2.0]})
    out = tmp_path / "header_zone.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


def _header_zone(root: ET.Element) -> ET.Element:
    zone = root.find(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert zone is not None, "no <zone type-v2='text'> found"
    return zone


# ---------------------------------------------------------------------------
# D5-1  No-theme / no-header byte-identical guards
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_title_subtitle_zones() -> None:
    xml_no_theme = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE, design_theme=None
    )
    assert xml_no_theme == xml_explicit_none

    root = ET.fromstring(xml_no_theme)
    text_zones = root.findall(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert len(text_zones) == 2, "no-theme path must keep the separate title + subtitle zones"


def test_theme_without_header_block_keeps_separate_title_subtitle_zones() -> None:
    """A theme with OTHER blocks set (dashboard_background) but no `header`
    block must still emit the pre-D5 separate title/subtitle zones."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_NO_HEADER,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert len(text_zones) == 2
    for zone in text_zones:
        assert zone.find("zone-style") is None, (
            "no header background -> no zone-style on text zones"
        )


def test_no_theme_byte_identical_build_embedded_twb_xml(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_omitted = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_TITLE_SUBTITLE,
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


# ---------------------------------------------------------------------------
# D5-2  Header present + title + subtitle -> ONE zone, 3 runs
# ---------------------------------------------------------------------------


def test_header_present_collapses_title_and_subtitle_into_one_zone() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert len(text_zones) == 1, "header present -> ONE combined zone, not two"

    runs = text_zones[0].find("formatted-text").findall("run")
    assert len(runs) == 3
    assert runs[0].text == "Executive Overview"
    assert runs[0].get("fontcolor") == "#ffffff"
    assert runs[0].get("bold") == "true"  # no brand -> hardcoded default bold=True

    assert runs[1].text == "Æ  "
    assert runs[1].get("fontcolor") == "#ffffff"
    # Separator run carries ONLY fontcolor — no fontsize/fontname/bold, mirrors
    # the mined <run fontcolor='#ffffff'>Æ  </run> exactly.
    assert runs[1].get("fontsize") is None
    assert runs[1].get("fontname") is None
    assert runs[1].get("bold") is None

    assert runs[2].text == "Q4 Performance"
    assert runs[2].get("fontcolor") == "#ffffff"
    assert runs[2].get("bold") is None  # subtitle default is never bold


# ---------------------------------------------------------------------------
# D5-3  Header present + title only -> ONE zone, 1 run
# ---------------------------------------------------------------------------


def test_header_present_title_only_emits_single_run() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_ONLY,
        design_theme=THEME_WITH_HEADER,
    )
    root = ET.fromstring(xml)
    text_zones = root.findall(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert len(text_zones) == 1
    runs = text_zones[0].find("formatted-text").findall("run")
    assert len(runs) == 1
    assert runs[0].text == "Executive Overview"


# ---------------------------------------------------------------------------
# D5-4  Header zone-style background-color, LAST child
# ---------------------------------------------------------------------------


def test_header_zone_gets_background_zone_style_as_last_child() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    root = ET.fromstring(xml)
    zone = _header_zone(root)
    assert zone[-1].tag == "zone-style"
    formats = {f.get("attr"): f.get("value") for f in zone.find("zone-style").findall("format")}
    assert formats == {"background-color": "#2f2e41"}


def test_header_zone_no_zone_style_when_background_unset() -> None:
    theme = _theme(header={"title_color": "#ffffff"})
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE, design_theme=theme
    )
    root = ET.fromstring(xml)
    zone = _header_zone(root)
    assert zone.find("zone-style") is None


# ---------------------------------------------------------------------------
# D5-5  Header zone height mirrors the mined title_height_ratio (Slice T2)
# ---------------------------------------------------------------------------


def test_header_zone_height_is_6960_vs_non_themed_6000() -> None:
    """Slice T2 (PLAN.md's Top-100 Corpus plan) beauty-gate fix: the themed
    header zone height is the T1-mined 900-1400-stratum title_height_ratio
    median (0.0696 * 100000 = 6960,
    design/corpus/stats/dashboard_norms.yaml), not the D5-original single
    exemplar's own h=9722."""
    xml_themed = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    themed_zone = _header_zone(ET.fromstring(xml_themed))
    assert themed_zone.get("h") == "6960"

    xml_plain = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE
    )
    plain_title_zone = ET.fromstring(xml_plain).findall(
        ".//dashboards/dashboard/zones//zone[@type-v2='text']"
    )[0]
    assert plain_title_zone.get("h") == "6000"


# ---------------------------------------------------------------------------
# D5-6  Brand typography drives font family/size; header overrides fontcolor
# ---------------------------------------------------------------------------


def test_header_run_uses_brand_typography_with_header_fontcolor_override() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER, brand=BRAND,
    )
    root = ET.fromstring(xml)
    runs = _header_zone(root).find("formatted-text").findall("run")
    title_run, _sep_run, subtitle_run = runs

    # Brand supplies fontname/fontsize/boldness (named-bold-font convention);
    # header.title_color OVERRIDES brand's own #1f1f1f fontcolor.
    assert title_run.get("fontname") == "Tableau Bold"
    assert title_run.get("fontsize") == "24"
    assert title_run.get("fontcolor") == "#ffffff"
    assert title_run.get("bold") is None

    assert subtitle_run.get("fontname") == "Tableau Book"
    assert subtitle_run.get("fontsize") == "11"
    assert subtitle_run.get("fontcolor") == "#ffffff"


# ---------------------------------------------------------------------------
# D5-7  XSD validity — both entry points
# ---------------------------------------------------------------------------


def test_xsd_valid_themed_header_build_twb_xml() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_themed_header_title_only_build_twb_xml() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_TITLE_ONLY,
        design_theme=THEME_WITH_HEADER,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_themed_header_build_embedded_twb_xml(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    _assert_xsd_valid(xml)


def test_post_workbook_dashboard_with_header_theme_returns_200(tmp_path: Path) -> None:
    """Integration: POST /workbook/dashboard with a camelCase header block."""
    from fastapi.testclient import TestClient

    from server import app as fastapi_app

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
        "dashboardTitle": "Executive Overview",
        "dashboardSubtitle": "Q4 Performance",
        "designTheme": {
            "name": "executive_dark",
            "header": {
                "background": "#2f2e41",
                "titleColor": "#ffffff",
                "subtitleColor": "#ffffff",
            },
        },
    }
    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")

    _assert_xsd_valid(twb_xml)
    root = ET.fromstring(twb_xml)
    text_zones = root.findall(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert len(text_zones) == 1
    runs = text_zones[0].find("formatted-text").findall("run")
    assert len(runs) == 3
    assert runs[0].text == "Executive Overview"
    assert runs[2].text == "Q4 Performance"
