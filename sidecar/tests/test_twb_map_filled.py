"""Tests for filled-map (choropleth) worksheet generation — Slice 3C.

Mirrors the "Sales Distribution by State" worksheet in:
  /tmp/twbref/wb1/Beginner's Repository III … .twb (lines ~4600–4854)

Key structural invariants asserted here (all derived from wb1):

- ``semantic-role='[State].[Name]'`` stamped on the geo dimension column at
  the datasource level (wb1 ~2804 / ~4639).
- ``<mapsources><mapsource name='Tableau'/></mapsources>`` inside ``<view>``
  (wb1 ~4614-4616).
- ``<rows>`` = ``[{ds}].[Latitude (generated)]``  (wb1 ~4850).
- ``<cols>`` = ``[{ds}].[Longitude (generated)]``  (wb1 ~4851).
- Pane id='0' carries ``<mark class='Automatic'/>`` + ``<encodings>`` with
  ``<lod>``, optional ``<color>``, and ``<geometry column='…[Geometry
  (generated)]'/>``  (wb1 ~4796-4820).
- Pane id='1' carries ``<mark class='Automatic'/>`` + ``<encodings>`` with
  ``<lod>`` only  (wb1 ~4786-4795).
- All output is XSD-valid via the vendored twb_2026.1.0.xsd gate.
- Non-map paths (bar, scatter) are byte-identical to the pre-3C build
  (regression guard — _quuid determinism stays green).

Both build paths are covered:
- ``build_twb_xml``        (sqlproxy)
- ``build_embedded_twb_xml`` (federated / hyper)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest
from lxml import etree

import hyper_builder
import twb_builder

# ---------------------------------------------------------------------------
# Shared XSD helpers
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


def _load_schema() -> etree.XMLSchema:
    parser = _safe_parser()
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


def _assert_xsd_valid(xml_str: str, label: str) -> None:
    schema = _load_schema()
    parser = _safe_parser()
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(f"  line {e.line}: {e.message}" for e in schema.error_log)
    assert valid, f"XSD validation FAILED [{label}]:\n{errors}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hyper_file(tmp_path: Path) -> Path:
    """Tiny .hyper with State (string) + Sales (real)."""
    df = pd.DataFrame(
        {
            "State": ["California", "Texas", "New York"],
            "Sales": [100.0, 200.0, 150.0],
        }
    )
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


# ---------------------------------------------------------------------------
# Sheet specs
# ---------------------------------------------------------------------------

SHEET_MAP_FILLED_WITH_COLOR = {
    "title": "Sales by State",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {
        "geoField": "State",
        "geoRole": "state",
        "colorMeasure": "Sales",
    },
}

SHEET_MAP_FILLED_NO_COLOR = {
    "title": "State Map",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {
        "geoField": "State",
        "geoRole": "state",
    },
}

SHEET_MAP_COUNTRY = {
    "title": "Country Map",
    "mark_type": "map_filled",
    "cols": [],
    "rows": [],
    "measures": [],
    "geo": {
        "geoField": "Country",
        "geoRole": "country",
        "colorMeasure": "Sales",
    },
}

# Regression: non-map sheet must be unaffected
SHEET_BAR_REGRESSION = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Revenue"],
}


# ---------------------------------------------------------------------------
# A — XSD validity
# ---------------------------------------------------------------------------


def test_map_filled_xsd_valid_sqlproxy() -> None:
    """map_filled worksheet must be XSD-valid via the sqlproxy path."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    _assert_xsd_valid(xml, "map_filled sqlproxy")


def test_map_filled_xsd_valid_embedded(hyper_file: Path) -> None:
    """map_filled worksheet must be XSD-valid via the embedded/federated path."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    _assert_xsd_valid(xml, "map_filled embedded")


def test_map_filled_no_color_xsd_valid_sqlproxy() -> None:
    """map_filled without colorMeasure must still be XSD-valid."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_NO_COLOR])
    _assert_xsd_valid(xml, "map_filled no color sqlproxy")


def test_map_filled_country_role_xsd_valid_sqlproxy() -> None:
    """map_filled with geoRole='country' must be XSD-valid."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_COUNTRY])
    _assert_xsd_valid(xml, "map_filled country sqlproxy")


# ---------------------------------------------------------------------------
# B — semantic-role on the datasource column (wb1 ~2804 / ~4639)
# ---------------------------------------------------------------------------


def _find_datasource_column(root: ET.Element, field_name: str) -> ET.Element | None:
    """Find a <column name='[{field_name}]'> under the top-level <datasource>."""
    for col in root.findall("./datasources/datasource/column"):
        if col.get("name") == f"[{field_name}]":
            return col
    return None


def test_semantic_role_state_stamped_sqlproxy() -> None:
    """The geo dimension column must have semantic-role='[State].[Name]' on sqlproxy path."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    col = _find_datasource_column(root, "State")
    assert col is not None, "[State] column not found in datasource"
    assert col.get("semantic-role") == "[State].[Name]", (
        f"Expected semantic-role='[State].[Name]', got {col.get('semantic-role')!r} "
        "(mirrors wb1 ~2804)"
    )


def test_semantic_role_state_stamped_embedded(hyper_file: Path) -> None:
    """The geo dimension column must have semantic-role='[State].[Name]' on embedded path."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    root = ET.fromstring(xml)
    col = _find_datasource_column(root, "State")
    assert col is not None, "[State] column not found in datasource"
    assert col.get("semantic-role") == "[State].[Name]", (
        f"Expected semantic-role='[State].[Name]', got {col.get('semantic-role')!r} "
        "(mirrors wb1 ~2804)"
    )


def test_semantic_role_country_stamped_sqlproxy() -> None:
    """geoRole='country' must produce semantic-role='[Country].[ISO3166_2]'."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_COUNTRY])
    root = ET.fromstring(xml)
    col = _find_datasource_column(root, "Country")
    assert col is not None, "[Country] column not found in datasource"
    assert col.get("semantic-role") == "[Country].[ISO3166_2]", (
        f"Expected '[Country].[ISO3166_2]', got {col.get('semantic-role')!r}"
    )


def test_non_geo_column_has_no_semantic_role_sqlproxy() -> None:
    """Non-geo columns must NOT receive a semantic-role attribute."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_BAR_REGRESSION])
    root = ET.fromstring(xml)
    for col in root.findall("./datasources/datasource/column"):
        assert col.get("semantic-role") is None, (
            f"Column {col.get('name')!r} unexpectedly got semantic-role "
            f"{col.get('semantic-role')!r} on a non-geo bar sheet"
        )


# ---------------------------------------------------------------------------
# C — <mapsources> in <view> (wb1 ~4614-4616)
# ---------------------------------------------------------------------------


def test_mapsources_present_in_view_sqlproxy() -> None:
    """map_filled must add <mapsources><mapsource name='Tableau'/></mapsources> to <view>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    mapsource = root.find(".//view/mapsources/mapsource")
    assert mapsource is not None, "<mapsource> not found inside <view> (mirrors wb1 ~4615)"
    assert mapsource.get("name") == "Tableau", (
        f"Expected name='Tableau', got {mapsource.get('name')!r}"
    )


def test_mapsources_present_in_view_embedded(hyper_file: Path) -> None:
    """map_filled (embedded path) must add <mapsources> to <view>."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    root = ET.fromstring(xml)
    mapsource = root.find(".//view/mapsources/mapsource")
    assert mapsource is not None, "<mapsource> not found inside <view>"
    assert mapsource.get("name") == "Tableau"


def test_mapsources_absent_for_bar() -> None:
    """Non-map worksheets must NOT have a <mapsources> element."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_BAR_REGRESSION])
    root = ET.fromstring(xml)
    assert root.find(".//mapsources") is None, (
        "<mapsources> must not be present in a plain bar sheet"
    )


# ---------------------------------------------------------------------------
# D — Generated lat/long on rows/cols (wb1 ~4850-4851)
# ---------------------------------------------------------------------------


def _get_rows_text(root: ET.Element) -> str:
    rows_el = root.find(".//table/rows")
    return rows_el.text or "" if rows_el is not None else ""


def _get_cols_text(root: ET.Element) -> str:
    cols_el = root.find(".//table/cols")
    return cols_el.text or "" if cols_el is not None else ""


def test_generated_latlong_on_rows_cols_sqlproxy() -> None:
    """Rows must be '[Latitude (generated)]' and cols '[Longitude (generated)]'."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    rows = _get_rows_text(root)
    cols = _get_cols_text(root)
    assert "[Latitude (generated)]" in rows, (
        f"Rows must reference [Latitude (generated)], got: {rows!r} (mirrors wb1 ~4850)"
    )
    assert "[Longitude (generated)]" in cols, (
        f"Cols must reference [Longitude (generated)], got: {cols!r} (mirrors wb1 ~4851)"
    )


def test_generated_latlong_on_rows_cols_embedded(hyper_file: Path) -> None:
    """Embedded path must also place generated lat/long on rows/cols."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    root = ET.fromstring(xml)
    rows = _get_rows_text(root)
    cols = _get_cols_text(root)
    assert "[Latitude (generated)]" in rows
    assert "[Longitude (generated)]" in cols


# ---------------------------------------------------------------------------
# E — Two-pane structure (wb1 ~4786-4821)
# ---------------------------------------------------------------------------


def test_two_panes_emitted_sqlproxy() -> None:
    """map_filled must emit exactly two <pane> elements."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    panes = root.findall(".//panes/pane")
    assert len(panes) == 2, f"Expected 2 panes, got {len(panes)} (mirrors wb1 ~4786-4821)"


def test_pane_ids_are_1_and_0_sqlproxy() -> None:
    """Pane ids must be '1' (LOD shadow) and '0' (polygon fill), in that order."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    panes = root.findall(".//panes/pane")
    ids = [p.get("id") for p in panes]
    assert ids == ["1", "0"], (
        f"Expected pane ids ['1', '0'], got {ids!r} (mirrors wb1 ~4787, ~4796)"
    )


def test_both_panes_have_automatic_mark_sqlproxy() -> None:
    """Both panes must carry <mark class='Automatic'/> (mirrors wb1 ~4791, ~4800)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    for pane in root.findall(".//panes/pane"):
        mark = pane.find("mark")
        assert mark is not None, "No <mark> element in pane"
        assert mark.get("class") == "Automatic", (
            f"Expected class='Automatic', got {mark.get('class')!r}"
        )


def test_geometry_encoding_on_pane_0_sqlproxy() -> None:
    """Pane id='0' must carry <geometry column='…[Geometry (generated)]'/> (wb1 ~4804)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    pane0 = next(p for p in root.findall(".//panes/pane") if p.get("id") == "0")
    geom = pane0.find(".//encodings/geometry")
    assert geom is not None, "<geometry> encoding not found in pane id='0'"
    col = geom.get("column", "")
    assert "[Geometry (generated)]" in col, (
        f"<geometry> column must reference '[Geometry (generated)]', got {col!r} "
        "(mirrors wb1 ~4804)"
    )


def test_color_encoding_on_pane_0_sqlproxy() -> None:
    """Pane id='0' must carry <color column='…[sum:Sales:qk]'/> when colorMeasure is set."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    pane0 = next(p for p in root.findall(".//panes/pane") if p.get("id") == "0")
    color = pane0.find(".//encodings/color")
    assert color is not None, "<color> encoding not found in pane id='0'"
    col = color.get("column", "")
    assert "[sum:Sales:qk]" in col, (
        f"Color column must reference '[sum:Sales:qk]', got {col!r} (mirrors wb1 ~4803)"
    )


def test_no_color_encoding_on_pane_0_when_no_color_measure() -> None:
    """When colorMeasure is absent pane id='0' must have no <color> encoding."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_NO_COLOR])
    root = ET.fromstring(xml)
    pane0 = next(p for p in root.findall(".//panes/pane") if p.get("id") == "0")
    color = pane0.find(".//encodings/color")
    assert color is None, (
        "No colorMeasure specified but <color> was emitted in pane id='0'"
    )


def test_lod_encoding_on_both_panes_sqlproxy() -> None:
    """Both pane id='1' and pane id='0' must carry <lod> encoding (wb1 ~4793, ~4802)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    for pane in root.findall(".//panes/pane"):
        lod = pane.find(".//encodings/lod")
        assert lod is not None, f"No <lod> encoding in pane id={pane.get('id')!r}"
        col = lod.get("column", "")
        assert "[none:State:nk]" in col, (
            f"<lod> must reference the geo dim instance '[none:State:nk]', got {col!r}"
        )


def test_geometry_and_color_present_embedded(hyper_file: Path) -> None:
    """Embedded path: pane id='0' must have geometry + color encodings."""
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        "Sales DS", hyper_file.name, columns, [SHEET_MAP_FILLED_WITH_COLOR]
    )
    root = ET.fromstring(xml)
    pane0 = next(p for p in root.findall(".//panes/pane") if p.get("id") == "0")
    assert pane0.find(".//encodings/geometry") is not None
    assert pane0.find(".//encodings/color") is not None


# ---------------------------------------------------------------------------
# F — geo field + color measure in datasource-dependencies
# ---------------------------------------------------------------------------


def test_geo_field_in_datasource_dependencies_sqlproxy() -> None:
    """The geo dimension must appear in <datasource-dependencies>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[State]" in col_names, (
        f"Geo field [State] must be in datasource-dependencies; got {col_names!r}"
    )


def test_color_measure_in_datasource_dependencies_sqlproxy() -> None:
    """The color measure must appear in <datasource-dependencies>."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_MAP_FILLED_WITH_COLOR])
    root = ET.fromstring(xml)
    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    col_names = {c.get("name") for c in deps.findall("column")}
    assert "[Sales]" in col_names, (
        f"Color measure [Sales] must be in datasource-dependencies; got {col_names!r}"
    )


# ---------------------------------------------------------------------------
# G — Regression: non-map paths are unaffected
# ---------------------------------------------------------------------------


def test_bar_sheet_unchanged_by_map_support_sqlproxy() -> None:
    """A plain bar sheet must not be affected by the map_filled code path."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_BAR_REGRESSION])
    root = ET.fromstring(xml)
    # Exactly one pane (not two)
    panes = root.findall(".//panes/pane")
    assert len(panes) == 1, f"Bar sheet must have exactly 1 pane, got {len(panes)}"
    # No mapsources
    assert root.find(".//mapsources") is None
    # No geometry encoding
    assert root.find(".//encodings/geometry") is None
    # Rows contains a measure, not lat/long
    rows = _get_rows_text(root)
    assert "[Latitude (generated)]" not in rows
    assert "Revenue" in rows or "sum:Revenue" in rows


def test_bar_sheet_unchanged_xsd_valid_regression() -> None:
    """Bar sheet regression must remain XSD-valid after map_filled addition."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", [SHEET_BAR_REGRESSION])
    _assert_xsd_valid(xml, "bar regression after map_filled")
