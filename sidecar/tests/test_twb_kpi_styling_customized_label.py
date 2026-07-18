"""Design Excellence, Slice D4 — live-probe #3 hotfix: BAN typography via
``<customized-label>``.

Fresh renders (probe #3, after the round-2 hotfix committed as ``a8d2483``)
still showed the render ignoring cell-rule fonts/colors and local compact
formats, even though the published XML was independently verified correct.
A direct diff against WB-118's real, published "Sales KPI (BAN) New"
worksheet (WB-118.twbx) found the decisive
difference and REVERSED this builder's earlier D4 assessment:

- Their pane keeps ALL its ``<text>`` encodings AND adds a
  ``<customized-label>`` — verified directly: pane children, in order, are
  ``view, mark, encodings, customized-label, style``. The label does not
  replace the encodings; it is a presentation template layered on top.
- The CDATA-style placeholder run (``<[ds].[instance]>``) renders the
  referenced field's value USING its resolved ``default-format`` — which
  is exactly why the round-2 hotfix's worksheet-local compact/arrow
  ``default-format`` values (correct in the published XML all along) never
  visibly took effect: nothing was READING them. ``<customized-label>`` is
  that missing reader.
- The TABLE-level ``element='cell'`` font-size/font-family/color rule this
  slice originally used for BAN typography is DEAD — fresh renders proved
  it does nothing for a naked (rows/cols empty) Text mark. It has been
  DELETED (``_kpi_tile_field_style_rule`` no longer exists); see
  ``test_twb_kpi_styling.py``'s D4-3 section, now a permanent regression
  guard.

See ``twb_builder._kpi_tile_customized_label``'s docstring for the full
run-shape rationale and every documented divergence from the mined
worksheet (no caption run — zone titles already label the tiles; a plain
``"\\n"`` instead of the mined ``"Æ\\n"`` glyph; primary+delta only, no
comparison run, since no mined worksheet stacks more than one explicit
value placeholder in a single label).

Test groups
-----------
CL-1  Pane child ORDER (encodings -> customized-label -> style), XSD-verified
CL-2  Encodings coexist — customized-label does NOT drop other <text> fields
CL-3  Primary run: fontcolor/fontname/fontsize from ban typography + ban_color
CL-4  Placeholder field refs match the ACTUAL <text> encoding instance names
CL-5  Delta run: smaller fontsize, fontcolor, no fontname; newline separator
CL-6  No caption run; documented divergences
CL-7  Comparison measure gets NO dedicated label run (documented)
CL-8  Gating: absent kpi_tile / no delta / no brand
CL-9  No-theme byte-identical; XSD validity (both entry points)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree

import hyper_builder
import twb_builder
from server import BrandModel, DesignThemeModel

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
            "Sales PP": [90.0, 150.0],
            "Sales Delta": [10.0, -5.0],
            "Region": ["East", "West"],
        }
    )
    out = tmp_path / "kpi_customized_label.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


KPI_SHEET_FULL = {
    "title": "KPI Sales",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales", "Sales PP", "Sales Delta"],
    "kpi": {
        "primary_measure": "Sales",
        "comparison_measure": "Sales PP",
        "delta_measure": "Sales Delta",
    },
}
KPI_SHEET_PRIMARY_ONLY = {
    "title": "KPI Sales Primary Only",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales"],
    "kpi": {"primary_measure": "Sales"},
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Sales"],
}
SHEETS_BASIC = [KPI_SHEET_FULL, CHART_SHEET]

DASHBOARD_KPI_BAND = [
    {
        "name": "Executive Dashboard",
        "titles": ["KPI Sales", "Revenue by Region"],
        "title": "Executive Dashboard",
        "subtitle": None,
        "text_zones": [],
        "layout_grammar": {
            "kind": "kpi_band_over_charts",
            "kpi_tile_titles": ["KPI Sales"],
            "chart_titles": ["Revenue by Region"],
        },
    }
]


def _kpi_theme(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "executive_dark",
        "kpi_tile": {
            "background": "#2f2e41",
            "border": {"color": "#000000", "style": "none", "width": 0},
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": True,
        },
    }
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_KPI = _kpi_theme()
THEME_NO_KPI_TILE = DesignThemeModel(name="executive_dark").model_dump()  # type: ignore[arg-type]

BRAND: dict[str, Any] = BrandModel(
    palette={
        "categorical": ["#4e79a7"],
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

DS_REF = "[sqlproxy.kpi_ds]"


def _field(measure: str) -> str:
    return f"{DS_REF}.{twb_builder._measure_instance(measure)}"


def _pane(worksheet: ET.Element) -> ET.Element:
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    return pane


def _label_runs(worksheet: ET.Element) -> list[ET.Element]:
    return worksheet.findall(".//panes/pane/customized-label/formatted-text/run")


# ---------------------------------------------------------------------------
# CL-1  Pane child ORDER
# ---------------------------------------------------------------------------


def test_pane_child_order_encodings_then_customized_label_then_style() -> None:
    """Mirrors the XSD's PaneSpecification-G sequence (encodings ->
    [CustomTooltip] -> customized-label -> [Stylesheet]) and the mined
    "Sales KPI (BAN) New" pane byte-for-byte (this builder emits neither
    CustomTooltip nor the optional HiddenFields/DropLine/Trendline/
    ReferenceLine groups, so the effective order collapses to exactly
    this)."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    pane = _pane(worksheet)
    tags = [c.tag for c in pane]
    assert tags == ["view", "mark", "encodings", "customized-label", "style"]


# ---------------------------------------------------------------------------
# CL-2  Encodings coexist with customized-label
# ---------------------------------------------------------------------------


def test_all_three_text_encodings_survive_alongside_customized_label() -> None:
    """The decisive reversal: customized-label does NOT replace/drop the
    other encoded measures — all 3 <text> elements stay declared."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    encodings = worksheet.findall(".//panes/pane/encodings/text")
    columns = {e.get("column") for e in encodings}
    assert columns == {_field("Sales"), _field("Sales PP"), _field("Sales Delta")}
    assert worksheet.find(".//panes/pane/customized-label") is not None


# ---------------------------------------------------------------------------
# CL-3  Primary run typography
# ---------------------------------------------------------------------------


def test_primary_run_gets_ban_typography_and_color() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    primary_run = runs[0]
    assert primary_run.get("fontcolor") == "#ffffff"
    assert primary_run.get("fontname") == "Tableau Bold"
    assert primary_run.get("fontsize") == "36"
    # Alphabetical attribute order (fontcolor, fontname, fontsize) — same
    # discipline as _build_text_zone's <run> construction.
    assert list(primary_run.attrib.keys()) == ["fontcolor", "fontname", "fontsize"]


# ---------------------------------------------------------------------------
# CL-4  Placeholder field refs match the ACTUAL <text> encoding
# ---------------------------------------------------------------------------


def test_primary_placeholder_matches_actual_text_encoding_instance() -> None:
    """Independent cross-check: the placeholder run's text must equal
    exactly one of the worksheet's OWN <text> encoding column values
    (angle-bracket wrapped) — read from the live XML, not re-derived via
    the same _measure_instance() helper the implementation uses."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    encoded_columns = {e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")}
    runs = _label_runs(worksheet)
    primary_run = runs[0]
    assert primary_run.text is not None
    assert primary_run.text.startswith("<") and primary_run.text.endswith(">")
    placeholder_column = primary_run.text[1:-1]
    assert placeholder_column in encoded_columns
    assert placeholder_column == _field("Sales")


# ---------------------------------------------------------------------------
# CL-5  Delta run + newline separator
# ---------------------------------------------------------------------------


def test_delta_run_smaller_fontsize_same_color_no_fontname() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    assert len(runs) == 3  # primary, newline, delta

    newline_run = runs[1]
    assert newline_run.attrib == {}
    assert newline_run.text == "\n"

    delta_run = runs[2]
    assert delta_run.get("fontname") is None
    assert delta_run.get("fontcolor") == "#ffffff"
    assert delta_run.get("fontsize") == "18"  # round(36 / 2)
    assert delta_run.text == f"<{_field('Sales Delta')}>"


def test_delta_run_fallback_fontsize_without_brand() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    delta_run = runs[2]
    assert delta_run.get("fontsize") == str(twb_builder._KPI_LABEL_DELTA_FALLBACK_FONTSIZE)
    primary_run = runs[0]
    assert primary_run.get("fontname") is None
    assert primary_run.get("fontsize") is None
    assert primary_run.get("fontcolor") == "#ffffff"


# ---------------------------------------------------------------------------
# CL-6  No caption run; documented divergences
# ---------------------------------------------------------------------------


def test_no_caption_run_zone_title_labels_the_tile_instead() -> None:
    """Mined worksheets open with a caption run (e.g. "S A L E S"). This
    builder deliberately omits it — the per-worksheet element='title' rule
    (Slice D4) already labels the tile via its zone title."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    texts = [r.text for r in runs]
    assert not any(t and t.strip().isupper() and " " in (t or "") for t in texts if t), texts
    # First run IS the primary value placeholder, not a caption string.
    assert runs[0].text == f"<{_field('Sales')}>"
    # The title rule is still present and does the labeling job instead.
    assert worksheet.find("table/style/style-rule[@element='title']") is not None


# ---------------------------------------------------------------------------
# CL-7  Comparison measure gets no dedicated label run
# ---------------------------------------------------------------------------


def test_comparison_measure_has_no_dedicated_label_run() -> None:
    """Documented decision: no mined worksheet stacks more than one value
    placeholder in a single label (WB-118 splits primary/delta into
    SEPARATE adjacent worksheets). Comparison's compact default-format is
    still emitted (round-2 hotfix) for tooltip/data use — just not given
    its own visible label line."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    placeholder_texts = {r.text for r in runs if r.text and r.text.startswith("<")}
    assert placeholder_texts == {f"<{_field('Sales')}>", f"<{_field('Sales Delta')}>"}
    assert f"<{_field('Sales PP')}>" not in placeholder_texts

    # But the comparison measure's own compact default-format is untouched.
    local_formats = {
        c.get("name"): c.get("default-format")
        for c in worksheet.findall(".//datasource-dependencies/column")
    }
    assert local_formats["[Sales PP]"] == 'c"$"#,##0,.0K;-"$"#,##0,.0K'


# ---------------------------------------------------------------------------
# CL-8  Gating
# ---------------------------------------------------------------------------


def test_customized_label_absent_without_kpi_tile_block() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_NO_KPI_TILE,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find(".//panes/pane/customized-label") is None


def test_customized_label_absent_without_design_theme() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, brand=BRAND
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    assert worksheet.find(".//panes/pane/customized-label") is None


def test_customized_label_has_one_run_when_no_delta_measure() -> None:
    sheets = [KPI_SHEET_PRIMARY_ONLY]
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Sales Primary Only"],
            "title": "Executive",
            "subtitle": None,
            "text_zones": [],
            "layout_grammar": {
                "kind": "kpi_band_over_charts",
                "kpi_tile_titles": ["KPI Sales Primary Only"],
                "chart_titles": [],
            },
        }
    ]
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", sheets, dashboards=dashboard, design_theme=THEME_KPI, brand=BRAND
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales Primary Only']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    assert len(runs) == 1
    assert runs[0].text == f"<{_field('Sales')}>"


def test_customized_label_absent_when_no_primary_measure() -> None:
    """Defensive: a kpi_tile sheet with an empty kpi spec (no
    primary_measure) emits no label at all."""
    sheet = {**KPI_SHEET_PRIMARY_ONLY, "title": "KPI Empty", "kpi": {}}
    dashboard = [
        {
            "name": "Executive Dashboard",
            "titles": ["KPI Empty"],
            "title": "Executive",
            "subtitle": None,
            "text_zones": [],
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
    assert worksheet.find(".//panes/pane/customized-label") is None


# ---------------------------------------------------------------------------
# CL-9  No-theme byte-identical; XSD validity
# ---------------------------------------------------------------------------


def test_no_theme_byte_identical_build_twb_xml() -> None:
    xml_omitted = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, brand=BRAND
    )
    xml_explicit_none = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        brand=BRAND,
        design_theme=None,
    )
    assert xml_omitted == xml_explicit_none


def test_xsd_valid_customized_label_dashboard_sqlproxy() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


def test_xsd_valid_customized_label_dashboard_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)
