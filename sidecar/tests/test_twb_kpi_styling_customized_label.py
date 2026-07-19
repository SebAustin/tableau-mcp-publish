"""Design Excellence, Slice D4 FINAL SHAPE — BAN typography via
``<customized-label>``, proven by a live-probe #3 offline bisect ladder.

Live-probe #3's fresh renders showed the round-3 hotfix's ``<customized-label>``
being silently ignored by Tableau Cloud — even though the published XML was
independently verified correct (escaped-text placeholders, white 36px runs,
worksheet-local compact/arrow default-formats). This builder's earlier
assessment (font-size/font-color from theme/brand, plain ``"\\n"``
separators, no caption run, escaped-text placeholders, raw-field local
default-formats) looked independently plausible but simply did not render.

Rather than continue guessing, an offline bisect ladder of a dozen
``.twbx`` variants (V0-V12; kept in scratchpad, never committed to the
repo) isolated the EXACT working shape by starting from a near-verbatim
GRAFT of WB-118's real, published "Sales KPI (BAN) New" worksheet
(WB-118.twbx) and making the smallest possible
edits, instead of continuing to morph this builder's own shape one
attribute at a time:

- V0 (the graft, unmodified): renders correctly on Cloud.
- V1-V5 (individual attributes — fontalignment, trailing runs, global
  vs. local default-format, CDATA vs. escaped text, caption-qualified
  placeholders — added to THIS builder's shape one at a time): all still
  rendered the plain default label. None of these individually explain it.
- V7 (a worksheet-local CALCULATED column's own default-format, instead of
  a RAW field's): its compact value rendered — but via the UNLABELED
  shelf's own fallback text-mark, not the customized-label (the label was
  STILL broken). This proved raw-field local default-format is inert on
  Cloud, independent of the label question.
- V9/V10 (this builder's shape + calc columns + a pane-level
  ``mark-labels-show``/``mark-labels-cull`` rule): rendered BLANK — worse
  than the plain-default fallback, because mark-labels-show suppressed
  that fallback too. Proved mark-labels-show is NECESSARY but not
  SUFFICIENT alone.
- V11/V12 (the GRAFT itself + calc columns, changing nothing else — caption
  run, glyph-prefixed newline runs, CDATA placeholders, mark-labels rule,
  selection-relaxation, all verbatim): rendered the caption + a correctly
  compact-formatted value. PROVEN.

:func:`twb_builder._kpi_tile_customized_label` now reproduces V11/V12's
shape byte-for-byte, with VALUES (not structure) parameterized off
``kpi_tile``/``brand``/the calc-column instances built by
:func:`twb_builder._append_kpi_ban_calc_column`.

Test groups
-----------
CL-1  Pane child ORDER + selection-relaxation-option, XSD-verified
CL-2  Encodings: primary/delta repointed at calc instances, comparison stays raw
CL-3  Caption run: letter-spaced uppercase title, color, fontsize
CL-4  Primary value run: CDATA placeholder, ban typography, NO fontname
CL-5  Newline-glyph separator runs (literal "Æ\\n"/"Æ\\n\\n", not plain "\\n")
CL-6  Delta run: fixed fontsize 12, same value color, no fontname, CDATA
CL-7  Comparison measure gets NO dedicated label run and NO calc column
CL-8  Gating: absent kpi_tile / no delta / no primary measure / no brand fallback
CL-10 No-theme byte-identical; XSD validity (both entry points)

CL-9 (pane-level mark-labels-show/cull rule) and the two-KPI-tiles-same-
datasource XSD case moved to ``test_twb_kpi_styling_integration.py`` (D4-11)
to keep this file under the ~800-line file-size guideline — same split
discipline as ``test_twb_chrome.py`` / ``test_twb_chrome_integration.py``.
"""

from __future__ import annotations

import re
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


def _calc_instance(field: str, *, delta: bool = False) -> str:
    suffix = "_Delta" if delta else ""
    name = f"{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug(field)}{suffix}"
    return f"[usr:{name}:qk]"


def _calc_field(field: str, *, delta: bool = False) -> str:
    return f"{DS_REF}.{_calc_instance(field, delta=delta)}"


def _pane(worksheet: ET.Element) -> ET.Element:
    pane = worksheet.find(".//panes/pane")
    assert pane is not None
    return pane


def _label_runs(worksheet: ET.Element) -> list[ET.Element]:
    return worksheet.findall(".//panes/pane/customized-label/formatted-text/run")


def _label_run_texts_raw(xml_str: str) -> list[str]:
    """Extract the customized-label's run TEXT CONTENT from the RAW XML
    string (not a re-parsed ElementTree) so CDATA sections survive intact —
    ET.tostring() always re-escapes CDATA content back to '&lt;...&gt;' on
    round-trip, which would hide the exact distinction CL-4/CL-6 test."""
    label_start = xml_str.index("<customized-label>")
    label_end = xml_str.index("</customized-label>") + len("</customized-label>")
    label_xml = xml_str[label_start:label_end]
    return re.findall(r"<run[^>]*>(.*?)</run>", label_xml, re.S)


# ---------------------------------------------------------------------------
# CL-1  Pane child ORDER + selection-relaxation-option
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


def test_pane_gets_selection_relaxation_option() -> None:
    """Mined fidelity — part of the proven V11/V12 shape."""
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
    assert pane.get("selection-relaxation-option") == "selection-relaxation-allow"


# ---------------------------------------------------------------------------
# CL-2  Encodings: primary/delta repointed at calc instances
# ---------------------------------------------------------------------------


def test_primary_and_delta_encodings_repointed_at_calc_instances() -> None:
    """FINAL SHAPE: the primary/delta <text> shelf encodings reference the
    worksheet-local CALCULATED columns, not the raw fields — the calc
    column's own default-format is what actually renders (bisect V7)."""
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
    columns = {e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")}
    assert columns == {
        _calc_field("Sales"),
        _field("Sales PP"),
        _calc_field("Sales Delta", delta=True),
    }
    assert worksheet.find(".//panes/pane/customized-label") is not None


def test_comparison_measure_stays_raw_field_encoding() -> None:
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
    columns = [e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")]
    assert _field("Sales PP") in columns


# ---------------------------------------------------------------------------
# CL-3  Caption run
# ---------------------------------------------------------------------------


def test_caption_run_is_letter_spaced_uppercase_title() -> None:
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
    caption_run = runs[0]
    assert caption_run.text == "K P I   S A L E S"  # "KPI Sales" letter-spaced/uppercased
    assert caption_run.get("fontalignment") == "0"
    assert caption_run.get("fontsize") == "7"
    # background set -> caption color defaults to ban_color (contrast on
    # the same themed background as the value runs).
    assert caption_run.get("fontcolor") == "#ffffff"


def test_caption_color_defaults_to_gray_when_no_background_set() -> None:
    theme = _kpi_theme(
        kpi_tile={
            "background": None,
            "border": None,
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": True,
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    caption_run = _label_runs(worksheet)[0]
    assert caption_run.get("fontcolor") == "#555555"


def test_caption_color_explicit_override_wins() -> None:
    theme = _kpi_theme(
        kpi_tile={
            "background": "#2f2e41",
            "border": None,
            "padding": 4,
            "ban_color": "#ffffff",
            "use_semantic_delta_colors": True,
            "caption_color": "#f2c94c",
        }
    )
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=theme
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    caption_run = _label_runs(worksheet)[0]
    assert caption_run.get("fontcolor") == "#f2c94c"


# ---------------------------------------------------------------------------
# CL-4  Primary value run: CDATA placeholder, ban typography, NO fontname
# ---------------------------------------------------------------------------


def test_primary_run_gets_ban_typography_no_fontname() -> None:
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
    primary_run = runs[2]  # caption, separator, PRIMARY VALUE, ...
    assert primary_run.get("fontcolor") == "#ffffff"
    # brand.typography.ban.size is 36, clamped to the mined max 26 (live-
    # probe #2e: 36 overflows the ~240px tile cell as "###") — see
    # test_primary_fontsize_clamped_to_mined_max_when_brand_requests_larger.
    assert primary_run.get("fontsize") == "26"
    # Design Excellence, Slice D4 FINAL SHAPE: NO fontname — V11/V12 (the
    # proven shape) carry none; an earlier version of this function set one
    # from brand.typography.ban.font, but that attribute was never part of
    # any variant that actually rendered.
    assert primary_run.get("fontname") is None
    assert primary_run.get("fontalignment") == "0"
    # Alphabetical attribute order (fontalignment, fontcolor, fontsize).
    assert list(primary_run.attrib.keys()) == ["fontalignment", "fontcolor", "fontsize"]


def test_primary_placeholder_is_cdata_and_matches_calc_encoding() -> None:
    """The placeholder must be a REAL CDATA section (not escaped text — the
    graft's own mined form) and must reference the SAME calc instance the
    primary <text> shelf encoding uses."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    run_texts = _label_run_texts_raw(xml)
    primary_marker = run_texts[2]
    assert primary_marker == f"<![CDATA[<{_calc_field('Sales')}>]]>"

    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    encoded_columns = {e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")}
    assert _calc_field("Sales") in encoded_columns
    # ET's own re-parse (which un-escapes CDATA to plain text) confirms the
    # placeholder's DECODED content is the expected "<[ds].[instance]>" form.
    primary_run = _label_runs(worksheet)[2]
    assert primary_run.text == f"<{_calc_field('Sales')}>"


def test_primary_fontsize_fallback_17_without_brand() -> None:
    xml = twb_builder.build_twb_xml(
        "DS", "kpi_ds", "site", SHEETS_BASIC, dashboards=DASHBOARD_KPI_BAND, design_theme=THEME_KPI
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    primary_run = _label_runs(worksheet)[2]
    assert primary_run.get("fontsize") == "17"
    assert primary_run.get("fontname") is None


def _brand_with_ban_size(size: int) -> dict[str, Any]:
    ban_font = {"font": "Tableau Bold", "size": size}
    typography = {**BRAND["typography"], "ban": ban_font}
    return BrandModel(**{**BRAND, "typography": typography}).model_dump()  # type: ignore[arg-type]


def test_primary_fontsize_clamped_to_mined_max_when_brand_requests_larger() -> None:
    """Live-probe #2e: the full-pipeline label mechanism renders correctly,
    but a large requested fontsize (e.g. brand.yaml's 36) overflows the
    ~240px KPI tile cell and Tableau renders the value as '###' — clamp to
    26, the largest BAN fontsize anywhere in the mined corpus."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=_brand_with_ban_size(36),
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    primary_run = _label_runs(worksheet)[2]
    assert primary_run.get("fontsize") == "26"


def test_primary_fontsize_unclamped_when_within_mined_range() -> None:
    """A brand-requested size already within the mined range (<=26) is
    emitted verbatim, unchanged by the clamp."""
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_KPI_BAND,
        design_theme=THEME_KPI,
        brand=_brand_with_ban_size(20),
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales']")
    assert worksheet is not None
    primary_run = _label_runs(worksheet)[2]
    assert primary_run.get("fontsize") == "20"


# ---------------------------------------------------------------------------
# CL-5  Newline-glyph separator runs
# ---------------------------------------------------------------------------


def test_newline_runs_use_the_mined_glyph_not_a_plain_newline() -> None:
    """Design Excellence, Slice D4 FINAL SHAPE: an earlier hotfix round used
    a plain '\\n' (documented as a deliberate, reasoned divergence from the
    mined 'Æ\\n' glyph); live-probe #3's bisect ladder (V9/V10) proved that
    divergence was part of the FAILING shape — the literal glyph is
    required."""
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
    # caption(0), separator(1), primary(2), newline(3), delta(4), trailing(5)
    assert runs[1].text == "Æ\n\n"
    assert runs[1].attrib == {"fontalignment": "0"}
    assert runs[3].text == "Æ\n"
    assert runs[3].attrib == {"fontalignment": "0"}
    assert runs[5].text == "Æ\n"
    assert runs[5].attrib == {"fontalignment": "0"}


def test_single_trailing_newline_when_no_delta_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        [KPI_SHEET_PRIMARY_ONLY],
        dashboards=[
            {
                "name": "Executive Dashboard",
                "titles": ["KPI Sales Primary Only"],
                "layout_grammar": {
                    "kind": "kpi_band_over_charts",
                    "kpi_tile_titles": ["KPI Sales Primary Only"],
                    "chart_titles": [],
                },
            }
        ],
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales Primary Only']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    assert len(runs) == 4  # caption, separator, primary value, trailing newline
    assert runs[3].text == "Æ\n"


# ---------------------------------------------------------------------------
# CL-6  Delta run
# ---------------------------------------------------------------------------


def test_delta_run_fixed_fontsize_same_color_no_fontname() -> None:
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
    assert len(runs) == 6  # caption, separator, primary, newline, delta, trailing

    delta_run = runs[4]
    assert delta_run.get("fontname") is None
    assert delta_run.get("fontcolor") == "#ffffff"
    # Design Excellence, Slice D4 FINAL SHAPE: a FIXED 12 (V12's exact
    # tested value) — NOT derived as half the primary's fontsize (an
    # earlier version of this function did that; the ladder never tested
    # the derived-half form, only the literal 12).
    assert delta_run.get("fontsize") == "12"
    assert delta_run.text == f"<{_calc_field('Sales Delta', delta=True)}>"


def test_delta_run_absent_when_no_delta_measure() -> None:
    xml = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        [KPI_SHEET_PRIMARY_ONLY],
        dashboards=[
            {
                "name": "Executive Dashboard",
                "titles": ["KPI Sales Primary Only"],
                "layout_grammar": {
                    "kind": "kpi_band_over_charts",
                    "kpi_tile_titles": ["KPI Sales Primary Only"],
                    "chart_titles": [],
                },
            }
        ],
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    root = ET.fromstring(xml)
    worksheet = root.find(".//worksheets/worksheet[@name='KPI Sales Primary Only']")
    assert worksheet is not None
    runs = _label_runs(worksheet)
    assert len(runs) == 4
    assert runs[2].text == f"<{_calc_field('Sales')}>"


# ---------------------------------------------------------------------------
# CL-7  Comparison measure: no dedicated label run, no calc column
# ---------------------------------------------------------------------------


def test_comparison_measure_has_no_dedicated_label_run_or_calc_column() -> None:
    """Documented decision: no mined worksheet stacks more than a
    primary+delta pair of value placeholders in a single label (WB-118
    splits comparison into a SEPARATE, adjacent worksheet). The
    comparison measure's raw <text> encoding stays declared and dependency-
    declared, but gets NO calc column and NO label line."""
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
    assert placeholder_texts == {
        f"<{_calc_field('Sales')}>",
        f"<{_calc_field('Sales Delta', delta=True)}>",
    }
    assert f"<{_field('Sales PP')}>" not in placeholder_texts

    dep_names = {
        c.get("name") for c in worksheet.findall(".//datasource-dependencies/column")
    }
    assert "[Sales PP]" in dep_names  # raw field still declared (the encoding needs it)
    calc_prefix = f"[{twb_builder._KPI_BAN_CALC_PREFIX}"
    assert not any(
        n is not None and n.startswith(calc_prefix) and "Sales_PP" in n for n in dep_names
    )


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


def test_customized_label_absent_when_no_primary_measure() -> None:
    """Defensive: a kpi_tile sheet with an empty kpi spec (no
    primary_measure) emits no label, no calc columns, at all."""
    sheet = {**KPI_SHEET_PRIMARY_ONLY, "title": "KPI Empty", "kpi": {}}
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
    assert worksheet.find(".//panes/pane/customized-label") is None
    assert twb_builder._KPI_BAN_CALC_PREFIX not in ET.tostring(worksheet, encoding="unicode")


# ---------------------------------------------------------------------------
# CL-9  Pane-level mark-labels-show/cull rule + the two-KPI-tiles-same-
# datasource XSD case move to test_twb_kpi_styling_integration.py (D4-11) —
# this file was over the ~800-line file-size guideline; same split
# discipline as test_twb_chrome.py / test_twb_chrome_integration.py.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CL-10  No-theme byte-identical; XSD validity
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


# The two-KPI-tiles-same-datasource XSD case and the pane-level
# mark-labels-show/cull rule coverage (CL-9) live in
# test_twb_kpi_styling_integration.py (D4-11) — this file was over the
# ~800-line file-size guideline; same split discipline as
# test_twb_chrome.py / test_twb_chrome_integration.py.
