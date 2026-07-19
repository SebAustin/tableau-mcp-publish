"""Design Excellence, Slice D6 — brand sequential/diverging palettes on maps.

Two independent, independently-gated constructs, both mirrored from real
mined XML (``design/corpus/recipes/palettes.yaml`` + the raw exemplars it was
mined from):

1. Workbook-level ``<preferences>`` registration. When ``brand.palette``
   carries ``sequential``/``diverging`` stops, an ADDITIONAL
   ``<color-palette custom='true' name='<brandName> Sequential'
   type='ordered-sequential'>``/``...Diverging.../type='ordered-diverging'``
   is emitted alongside the existing categorical ``<brandName> Palette``
   registration — mirrors the mined ``WB-062``/``WB-015``
   entries in ``palettes.yaml`` (``type: ordered-sequential`` /
   ``type: ordered-diverging``, ``custom='true'``). Gated ONLY on ``brand``
   being present and the relevant list being non-empty — same discipline as
   the existing categorical registration, independent of ``design_theme``
   (preferences are a harmless additive registration; see the D6 plan's
   explicit decision).

2. Worksheet-level ``<style-rule element='mark'><encoding attr='color'
   field='...' palette='<brandName> Sequential' type='palette'/></style-rule>``
   on ``map_filled`` sheets with a ``geo.color_measure``. This is the ONLY
   verified real-world construct pairing a ``palette=`` attribute directly on
   an ``<encoding attr='color'>`` element — found in the scratchpad-restored
   ``WB-058`` (``<encoding attr='color'
   field='[none:...:ok]&#10;[none:...:ok]' palette='miller_stone_10_0'
   type='palette'>``, alongside a discrete bucket map). Here ``palette=``
   references the brand's OWN ``<preferences>`` entry from (1) above instead
   of a Tableau built-in name — the color stops live in exactly one place.
   Gated on BOTH ``design_theme`` being present AND
   ``brand.palette.sequential`` being non-empty, to protect the no-theme
   byte-identical guard (see group C below).

   Deviation from the alternative embedded-color construct: 3 separate mined
   exemplars (``WB-015`` x2, ``WB-062``/``WB-063`` x5)
   instead embed a full, unnamed ``<color-palette type='ordered-sequential'>``
   directly inside the style-rule's ``<encoding type='custom-interpolated'>``
   (duplicating the color stops per-worksheet) rather than referencing a
   name. The plan explicitly asks for the ``palette='<brandName> Sequential'``
   attribute-reference shape (single source of truth in ``<preferences>``),
   which is verified schema-legal (``Encoding-G``'s ``palette`` attribute)
   and verified as a REAL, if differently-typed, mined construct — see the
   module-level report for the full trade-off writeup.

Test groups
-----------
A  Preferences: sequential/diverging registration (presence, absence, order,
   hex normalization)
B  Map-filled worksheet color encoding: ``palette=`` attribute gating
C  Byte-identical / gating guards (no theme, no brand, empty palette)
D  XSD validity
E  FastAPI integration: POST /workbook/dashboard, camelCase brand + designTheme
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi.testclient import TestClient
from lxml import etree

import hyper_builder
import twb_builder
from server import DesignThemeModel
from server import app as fastapi_app

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


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
    doc = etree.fromstring(xml_str.encode(), _safe_parser())
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED:\n{errors}"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Full brand, both sequential and diverging populated (mirrors
# test_twb_branding.py's BRAND fixture exactly).
BRAND: dict[str, Any] = {
    "palette": {
        "categorical": ["#4e79a7", "#f28e2b", "#e15759", "#59a14f"],
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
    "brand_name": "Acme Corp",
}

# Same brand, but with EMPTY sequential/diverging — the "categorical only"
# case the plan calls out as unchanged behavior.
BRAND_NO_SEQ_DIV: dict[str, Any] = {
    **BRAND,
    "palette": {**BRAND["palette"], "sequential": [], "diverging": []},
}

SHEETS_BASIC = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
]

SHEET_MAP_FILLED_WITH_COLOR = {
    "title": "Sales by State",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {
        "geo_field": "State",
        "geo_role": "state",
        "color_measure": "Sales",
    },
}

SHEET_MAP_FILLED_NO_COLOR = {
    "title": "State Map",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {
        "geo_field": "State",
        "geo_role": "state",
    },
}

SHEET_BAR_REGRESSION = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}


def _theme(**overrides: object) -> dict[str, object]:
    """Build a ``DesignThemeModel.model_dump()`` (snake_case) dict.

    Only ``name`` is required by the model; every other field defaults to
    ``None`` — sufficient to flip the ``design_theme is not None`` gate
    without needing the full D2 zone-style block.
    """
    base: dict[str, object] = {"name": "executive_dark"}
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_MINIMAL = _theme()


def _build_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "State": ["California", "Texas", "New York"],
            "Sales": [100.0, 200.0, 150.0],
            "Region": ["West", "West", "East"],
            "Revenue": [1.0, 2.0, 3.0],
        }
    )
    out = tmp_path / "palettes.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


def _find_color_palettes(root: ET.Element) -> list[ET.Element]:
    prefs = root.find("preferences")
    assert prefs is not None
    return prefs.findall("color-palette")


def _find_map_palette_encoding(root: ET.Element) -> ET.Element | None:
    """Find the ``<style-rule element='mark'><encoding attr='color'
    palette=...>`` in the worksheet's TABLE-level ``<style>`` (not the pane's
    ``<encodings><color>`` shelf element — a different, palette-less
    construct from Phase 1)."""
    for rule in root.findall(".//table/style/style-rule[@element='mark']"):
        for enc in rule.findall("encoding"):
            if enc.get("attr") == "color" and enc.get("palette") is not None:
                return enc
    return None


# ---------------------------------------------------------------------------
# A — Preferences: sequential/diverging registration
# ---------------------------------------------------------------------------


def test_preferences_sequential_palette_present_when_brand_has_sequential() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    root = ET.fromstring(xml)
    palettes = _find_color_palettes(root)
    seq = next((p for p in palettes if p.get("type") == "ordered-sequential"), None)
    assert seq is not None, "Expected an ordered-sequential <color-palette> in <preferences>"
    assert seq.get("custom") == "true"
    assert seq.get("name") == "Acme Corp Sequential"
    colors = [c.text for c in seq.findall("color")]
    assert colors == BRAND["palette"]["sequential"]


def test_preferences_diverging_palette_present_when_brand_has_diverging() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    root = ET.fromstring(xml)
    palettes = _find_color_palettes(root)
    div = next((p for p in palettes if p.get("type") == "ordered-diverging"), None)
    assert div is not None, "Expected an ordered-diverging <color-palette> in <preferences>"
    assert div.get("custom") == "true"
    assert div.get("name") == "Acme Corp Diverging"
    colors = [c.text for c in div.findall("color")]
    assert colors == BRAND["palette"]["diverging"]


def test_preferences_sequential_and_diverging_absent_when_empty() -> None:
    """Categorical-only brand (empty sequential/diverging) — unchanged behavior."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND_NO_SEQ_DIV)
    root = ET.fromstring(xml)
    palettes = _find_color_palettes(root)
    types = [p.get("type") for p in palettes]
    assert types == ["regular"], f"Expected categorical-only, got {types!r}"


def test_preferences_absent_without_brand() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC)
    root = ET.fromstring(xml)
    assert root.find("preferences") is None


def test_preferences_color_palette_order_categorical_sequential_diverging() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    root = ET.fromstring(xml)
    palettes = _find_color_palettes(root)
    assert [p.get("type") for p in palettes] == [
        "regular",
        "ordered-sequential",
        "ordered-diverging",
    ]
    # Existing categorical lookup (`prefs.find("color-palette")` returns the
    # FIRST match) must still resolve to the categorical palette — the
    # existing test_twb_branding.py assertions rely on this.
    assert root.find("preferences/color-palette").get("type") == "regular"


def test_preferences_sequential_expands_short_hex_colors() -> None:
    brand = {**BRAND, "palette": {**BRAND["palette"], "sequential": ["#5af", "#000"]}}
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=brand)
    root = ET.fromstring(xml)
    seq = next(p for p in _find_color_palettes(root) if p.get("type") == "ordered-sequential")
    colors = [c.text for c in seq.findall("color")]
    assert colors == ["#55aaff", "#000000"]


def test_preferences_only_diverging_populated() -> None:
    """Sequential empty, diverging populated — the two lists are independent."""
    brand = {**BRAND, "palette": {**BRAND["palette"], "sequential": []}}
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=brand)
    root = ET.fromstring(xml)
    types = [p.get("type") for p in _find_color_palettes(root)]
    assert types == ["regular", "ordered-diverging"]


# ---------------------------------------------------------------------------
# B — Map-filled worksheet color encoding: palette= attribute gating
# ---------------------------------------------------------------------------


def test_map_color_encoding_gets_palette_attr_when_theme_and_brand_sequential_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], brand=BRAND, design_theme=THEME_MINIMAL
    )
    root = ET.fromstring(xml)
    enc = _find_map_palette_encoding(root)
    assert enc is not None, "Expected a palette= <encoding attr='color'> style-rule"
    assert enc.get("palette") == "Acme Corp Sequential"
    assert enc.get("type") == "palette"
    assert "[sum:Sales:qk]" in (enc.get("field") or "")
    # The original Phase-1 shelf-level <color column='...'/> encoding must
    # still be present and untouched (a separate, palette-less construct).
    shelf_color = root.find(".//panes/pane[@id='0']/encodings/color")
    assert shelf_color is not None
    assert shelf_color.get("palette") is None


def test_map_color_encoding_gets_palette_attr_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS",
        hyper_file.name,
        columns,
        [SHEET_MAP_FILLED_WITH_COLOR],
        brand=BRAND,
        design_theme=THEME_MINIMAL,
    )
    root = ET.fromstring(xml)
    enc = _find_map_palette_encoding(root)
    assert enc is not None
    assert enc.get("palette") == "Acme Corp Sequential"


def test_map_color_encoding_attribute_order_matches_mined_shape() -> None:
    """Attribute insertion order mirrors WB-058's own ``<encoding
    attr='color' field='...' palette='...' type='palette'>`` (alphabetical:
    attr, field, palette, type)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], brand=BRAND, design_theme=THEME_MINIMAL
    )
    root = ET.fromstring(xml)
    enc = _find_map_palette_encoding(root)
    assert enc is not None
    assert list(enc.attrib.keys()) == ["attr", "field", "palette", "type"]


def test_map_color_encoding_no_style_rule_when_no_color_measure() -> None:
    """Map without geo.color_measure -> nothing to color by, no style-rule."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_NO_COLOR], brand=BRAND, design_theme=THEME_MINIMAL
    )
    root = ET.fromstring(xml)
    assert _find_map_palette_encoding(root) is None


def test_non_map_sheet_never_gets_palette_encoding() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_BAR_REGRESSION], brand=BRAND, design_theme=THEME_MINIMAL
    )
    root = ET.fromstring(xml)
    assert _find_map_palette_encoding(root) is None


# ---------------------------------------------------------------------------
# C — Byte-identical / gating guards
# ---------------------------------------------------------------------------


def test_map_color_encoding_absent_without_design_theme() -> None:
    """Brand WITH sequential colors but NO design_theme -> no palette=
    reference at all (protects the no-theme byte-identical output)."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], brand=BRAND
    )
    root = ET.fromstring(xml)
    assert _find_map_palette_encoding(root) is None


def test_map_color_encoding_absent_without_brand_sequential() -> None:
    """design_theme present but brand.palette.sequential empty -> no palette=."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [SHEET_MAP_FILLED_WITH_COLOR],
        brand=BRAND_NO_SEQ_DIV,
        design_theme=THEME_MINIMAL,
    )
    root = ET.fromstring(xml)
    assert _find_map_palette_encoding(root) is None


def test_map_color_encoding_absent_without_brand_at_all() -> None:
    """design_theme present but no brand block at all -> no palette=."""
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], design_theme=THEME_MINIMAL
    )
    root = ET.fromstring(xml)
    assert _find_map_palette_encoding(root) is None


def test_build_twb_xml_no_brand_no_theme_map_sheet_byte_identical_to_pre_d6() -> None:
    """Map-filled sheet, no brand, no design_theme — the D6 code path (map
    color-measure lookup) must be a total no-op: omitted vs explicit None
    for both params, byte-identical."""
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR]
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], brand=None, design_theme=None
    )
    assert xml_omitted == xml_explicit_none


def test_build_embedded_twb_xml_no_brand_no_theme_byte_identical(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml_omitted = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    xml_explicit_none = twb_builder.build_embedded_twb_xml(
        "Sales DS",
        hyper_file.name,
        columns,
        [SHEET_MAP_FILLED_WITH_COLOR],
        brand=None,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def test_non_map_sheets_byte_identical_regardless_of_brand_sequential() -> None:
    """A plain bar sheet's XML must not change whether brand.sequential is
    populated or empty — the D6 preferences addition only touches
    <preferences>, never worksheet XML for non-map sheets."""
    xml_with_seq = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_BAR_REGRESSION], brand=BRAND, design_theme=THEME_MINIMAL
    )
    xml_no_seq = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        [SHEET_BAR_REGRESSION],
        brand=BRAND_NO_SEQ_DIV,
        design_theme=THEME_MINIMAL,
    )
    root_with_seq = ET.fromstring(xml_with_seq)
    root_no_seq = ET.fromstring(xml_no_seq)
    worksheets_with_seq = ET.tostring(root_with_seq.find("worksheets"))
    worksheets_no_seq = ET.tostring(root_no_seq.find("worksheets"))
    assert worksheets_with_seq == worksheets_no_seq


# ---------------------------------------------------------------------------
# D — XSD validity
# ---------------------------------------------------------------------------


def test_xsd_valid_preferences_sequential_and_diverging() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS_BASIC, brand=BRAND)
    _assert_xsd_valid(xml)


def test_xsd_valid_map_filled_palette_encoding_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR], brand=BRAND, design_theme=THEME_MINIMAL
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_map_filled_palette_encoding_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS",
        hyper_file.name,
        columns,
        [SHEET_MAP_FILLED_WITH_COLOR],
        brand=BRAND,
        design_theme=THEME_MINIMAL,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_mixed_sheets_with_full_brand_and_theme(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS",
        hyper_file.name,
        columns,
        [SHEET_MAP_FILLED_WITH_COLOR, SHEET_BAR_REGRESSION],
        brand=BRAND,
        design_theme=THEME_MINIMAL,
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# E — FastAPI integration: POST /workbook/dashboard
# ---------------------------------------------------------------------------

_BRAND_CAMEL: dict[str, Any] = {
    "palette": {
        "categorical": ["#4e79a7", "#f28e2b", "#e15759", "#59a14f"],
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
    "formats": {"currency": "$#,##0", "percent": "0.0%", "number": "#,##0"},
    "brandName": "Acme Corp",
}

_DESIGN_THEME_CAMEL: dict[str, Any] = {"name": "executive_dark"}


def test_post_workbook_dashboard_camelcase_brand_with_sequential_returns_200_with_palette(
    tmp_path: Path,
) -> None:
    """POST /workbook/dashboard with a camelCase brand (sequential populated)
    + designTheme must return 200 and the generated .twbx must carry both the
    <preferences> sequential registration and the map encoding's palette=
    reference."""
    hyper_file = _build_hyper(tmp_path)

    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "Sales by State",
                "markType": "map_filled",
                "cols": [],
                "rows": [],
                "measures": [],
                "geo": {"geoField": "State", "geoRole": "state", "colorMeasure": "Sales"},
            },
        ],
        "brand": _BRAND_CAMEL,
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

    seq = next(
        (p for p in _find_color_palettes(root) if p.get("type") == "ordered-sequential"), None
    )
    assert seq is not None
    assert seq.get("name") == "Acme Corp Sequential"

    enc = _find_map_palette_encoding(root)
    assert enc is not None
    assert enc.get("palette") == "Acme Corp Sequential"


def test_post_workbook_dashboard_brand_without_design_theme_no_map_palette(
    tmp_path: Path,
) -> None:
    """Brand present (sequential populated) but NO designTheme -> preferences
    gets the sequential registration, but the map encoding does NOT."""
    hyper_file = _build_hyper(tmp_path)

    payload = {
        "datasourceName": "Sales Data",
        "datasourceContentUrl": "sales_data",
        "hyperPath": str(hyper_file),
        "sheets": [
            {
                "title": "Sales by State",
                "markType": "map_filled",
                "cols": [],
                "rows": [],
                "measures": [],
                "geo": {"geoField": "State", "geoRole": "state", "colorMeasure": "Sales"},
            },
        ],
        "brand": _BRAND_CAMEL,
    }

    client = TestClient(fastapi_app)
    response = client.post("/workbook/dashboard", json=payload)
    assert response.status_code == 200
    body = response.json()

    with zipfile.ZipFile(body["path"]) as archive:
        twb_name = next(n for n in archive.namelist() if n.endswith(".twb"))
        twb_xml = archive.read(twb_name).decode("utf-8")

    root = ET.fromstring(twb_xml)
    seq = next(
        (p for p in _find_color_palettes(root) if p.get("type") == "ordered-sequential"), None
    )
    assert seq is not None, "Preferences sequential registration must NOT depend on designTheme"
    assert _find_map_palette_encoding(root) is None, (
        "Map encoding palette= must be absent without designTheme"
    )
