"""Design Excellence, M2 — computed YoY period-comparison delta for KPI
tiles (external skill backlog #1/#2, GAPS.md Sec 4).

Closes ACCEPTANCE.md's documented NULL-delta residual: a KPI tile's
``delta_measure`` previously required a PRE-EXISTING delta COLUMN in the
data (100% NULL on the real Superstore CSV, which carries no period-compare
columns). This generalizes :func:`twb_builder._append_kpi_ban_calc_column`
to accept an arbitrary ``formula``/``derivation`` (default unchanged —
``SUM([field])`` / ``"User"``, byte-identical to before this slice) and adds
:func:`twb_builder._append_computed_yoy_delta_calc`, which emits CY/PY/Delta
calculated columns computing a REAL period-over-period delta from the
primary measure + a date field alone — no pre-existing delta column needed.

DATA-RELATIVE latest year (not ``YEAR(TODAY())``)
---------------------------------------------------
The naive recipe (``IF YEAR([d])=YEAR(TODAY()) THEN [m] END``) fails on
historical datasets (e.g. Superstore's Order Date years 2014-2017ish, never
equal to the real build/open date) — CY resolves to all NULL, and the delta
is NULL again. ``{ MAX(YEAR([d])) }`` is a FIXED-less table-scalar LOD that
evaluates once, over the whole data source, to the latest year actually
PRESENT in the data — non-NULL on any dataset with >=2 distinct years,
independent of when the workbook is built or opened.

Test groups
-----------
YD-1  CY/PY calc columns: exact data-relative LOD formula strings
YD-2  Delta calc column: formula references the CY/PY calc's OWN internal
      field names (not the raw measure)
YD-3  Delta run in <customized-label> references the delta calc instance
      (same generic mechanism as an explicit delta_measure — untouched)
YD-4  Encodings repointed at calc instances; no comparison_measure raw field
YD-5  An explicit delta_measure always wins over comparison_kind/date_field
YD-6  Byte-identical when neither delta_measure nor comparison_kind/date_field set
YD-7  XSD validity (sqlproxy + embedded entry points)
YD-8  comparison_kind='mom' is carried but produces NO computed delta (scoping)
YD-9  Integration: POST /workbook/dashboard camelCase dateField -> 200 + calc columns
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
            "Sales": [100.0, 200.0, 150.0],
            "Order Date": ["2016-01-01", "2017-01-01", "2017-06-01"],
            "Region": ["East", "West", "East"],
        }
    )
    out = tmp_path / "kpi_computed_yoy.hyper"
    hyper_builder.dataframe_to_hyper(df, out)
    return out


KPI_SHEET_COMPUTED_YOY = {
    "title": "KPI Sales",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales"],
    "kpi": {
        "primary_measure": "Sales",
        "comparison_kind": "yoy",
        "date_field": "Order Date",
    },
}
KPI_SHEET_EXPLICIT_DELTA = {
    "title": "KPI Sales Explicit",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales", "Sales Delta"],
    "kpi": {
        "primary_measure": "Sales",
        "delta_measure": "Sales Delta",
        # Defensive: explicit delta_measure must win even if a caller also
        # (incorrectly) sets comparison_kind/date_field.
        "comparison_kind": "yoy",
        "date_field": "Order Date",
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
KPI_SHEET_MOM = {
    "title": "KPI Sales MoM",
    "mark_type": "text",
    "kind": "kpi_tile",
    "cols": [],
    "rows": [],
    "measures": ["Sales"],
    "kpi": {
        "primary_measure": "Sales",
        "comparison_kind": "mom",
        "date_field": "Order Date",
    },
}
CHART_SHEET = {
    "title": "Revenue by Region",
    "mark_type": "bar",
    "cols": ["Region"],
    "rows": [],
    "measures": ["Sales"],
}

SHEETS_COMPUTED_YOY = [KPI_SHEET_COMPUTED_YOY, CHART_SHEET]

DASHBOARD_COMPUTED_YOY = [
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


def _yoy_calc_field(field: str, suffix: str) -> str:
    return f"{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug(field)}{suffix}"


def _sum_instance(field: str, suffix: str) -> str:
    """Instance-name fragment for a 'Sum'-derivation calc column (CY/PY)."""
    return f"[sum:{_yoy_calc_field(field, suffix)}:qk]"


def _user_instance(field: str, suffix: str) -> str:
    """Instance-name fragment for a 'User'-derivation calc column (Delta)."""
    return f"[usr:{_yoy_calc_field(field, suffix)}:qk]"


def _calc_field_ref(field: str, suffix: str, *, sum_derivation: bool = False) -> str:
    instance = _sum_instance(field, suffix) if sum_derivation else _user_instance(field, suffix)
    return f"{DS_REF}.{instance}"


def _worksheet(xml: str, name: str) -> ET.Element:
    root = ET.fromstring(xml)
    worksheet = root.find(f".//worksheets/worksheet[@name='{name}']")
    assert worksheet is not None
    return worksheet


def _dep_column(worksheet: ET.Element, name: str) -> ET.Element | None:
    return worksheet.find(f".//datasource-dependencies/column[@name='{name}']")


def _label_runs(worksheet: ET.Element) -> list[ET.Element]:
    return worksheet.findall(".//panes/pane/customized-label/formatted-text/run")


def _build_yoy_xml(sheets: list[dict[str, Any]] | None = None) -> str:
    return twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        sheets if sheets is not None else SHEETS_COMPUTED_YOY,
        dashboards=DASHBOARD_COMPUTED_YOY,
        design_theme=THEME_KPI,
        brand=BRAND,
    )


# ---------------------------------------------------------------------------
# YD-1  CY/PY calc columns: exact data-relative LOD formula strings
# ---------------------------------------------------------------------------


def test_cy_calc_column_exact_formula() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    cy_name = f"[{_yoy_calc_field('Sales', '_YoY_CY')}]"
    cy_col = _dep_column(worksheet, cy_name)
    assert cy_col is not None
    calc = cy_col.find("calculation")
    assert calc is not None
    assert calc.get("class") == "tableau"
    assert (
        calc.get("formula")
        == "IF YEAR([Order Date]) = { MAX(YEAR([Order Date])) } THEN [Sales] END"
    )


def test_py_calc_column_exact_formula() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    py_name = f"[{_yoy_calc_field('Sales', '_YoY_PY')}]"
    py_col = _dep_column(worksheet, py_name)
    assert py_col is not None
    calc = py_col.find("calculation")
    assert calc is not None
    assert (
        calc.get("formula")
        == "IF YEAR([Order Date]) = { MAX(YEAR([Order Date])) } - 1 THEN [Sales] END"
    )


def test_cy_py_calc_columns_use_sum_derivation_instance() -> None:
    """Row-level (non-aggregating) formulas get derivation='Sum' (the 'sum:'
    instance prefix) — mirrors WB-118's own mined convention documented on
    _append_kpi_ban_calc_column."""
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    cy_instance = worksheet.find(
        f".//datasource-dependencies/column-instance[@name='{_sum_instance('Sales', '_YoY_CY')}']"
    )
    py_instance = worksheet.find(
        f".//datasource-dependencies/column-instance[@name='{_sum_instance('Sales', '_YoY_PY')}']"
    )
    assert cy_instance is not None
    assert cy_instance.get("derivation") == "Sum"
    assert py_instance is not None
    assert py_instance.get("derivation") == "Sum"


# ---------------------------------------------------------------------------
# YD-2  Delta calc column: formula references CY/PY's OWN internal field names
# ---------------------------------------------------------------------------


def test_delta_calc_column_exact_formula_references_cy_py() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    delta_name = f"[{_yoy_calc_field('Sales', '_Delta')}]"
    delta_col = _dep_column(worksheet, delta_name)
    assert delta_col is not None
    calc = delta_col.find("calculation")
    assert calc is not None
    cy_field = _yoy_calc_field("Sales", "_YoY_CY")
    py_field = _yoy_calc_field("Sales", "_YoY_PY")
    assert calc.get("formula") == f"SUM([{cy_field}]) - SUM([{py_field}])"


def test_delta_calc_column_uses_user_derivation_instance() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    delta_instance = worksheet.find(
        f".//datasource-dependencies/column-instance[@name='{_user_instance('Sales', '_Delta')}']"
    )
    assert delta_instance is not None
    assert delta_instance.get("derivation") == "User"


def test_delta_calc_column_gets_arrow_format_when_semantic_colors_on() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    delta_name = f"[{_yoy_calc_field('Sales', '_Delta')}]"
    delta_col = _dep_column(worksheet, delta_name)
    assert delta_col is not None
    assert delta_col.get("default-format") == twb_builder._KPI_DELTA_ARROW_FORMAT


# ---------------------------------------------------------------------------
# YD-3  Delta run in <customized-label> references the delta calc instance
# ---------------------------------------------------------------------------


def test_delta_run_references_computed_delta_calc_instance() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    runs = _label_runs(worksheet)
    assert len(runs) == 6  # caption, separator, primary, newline, delta, trailing
    delta_run = runs[4]
    assert delta_run.text == f"<{_calc_field_ref('Sales', '_Delta')}>"
    assert delta_run.get("fontsize") == "12"
    assert delta_run.get("fontcolor") == "#ffffff"


def test_primary_run_references_primary_calc_instance() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    runs = _label_runs(worksheet)
    primary_run = runs[2]
    primary_instance = (
        f"[usr:{twb_builder._KPI_BAN_CALC_PREFIX}{twb_builder._slug('Sales')}:qk]"
    )
    assert primary_run.text == f"<{DS_REF}.{primary_instance}>"


# ---------------------------------------------------------------------------
# YD-4  Encodings repointed at calc instances; no comparison_measure raw field
# ---------------------------------------------------------------------------


def test_encodings_repointed_primary_and_delta_no_comparison_field() -> None:
    xml = _build_yoy_xml()
    worksheet = _worksheet(xml, "KPI Sales")
    columns = {e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")}
    assert columns == {
        _calc_field_ref("Sales", ""),
        _calc_field_ref("Sales", "_Delta"),
    }


# ---------------------------------------------------------------------------
# YD-5  An explicit delta_measure always wins over comparison_kind/date_field
# ---------------------------------------------------------------------------


def test_explicit_delta_measure_wins_over_comparison_kind() -> None:
    xml = _build_yoy_xml([KPI_SHEET_EXPLICIT_DELTA, CHART_SHEET])
    worksheet = ET.fromstring(xml).find(
        ".//worksheets/worksheet[@name='KPI Sales Explicit']"
    )
    assert worksheet is not None
    # No CY/PY calc columns should exist — the explicit delta path never
    # invokes the computed-YoY helper.
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_YoY_CY')}]") is None
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_YoY_PY')}]") is None
    # The explicit delta calc column (field='Sales Delta') is present instead.
    explicit_delta_name = f"[{_yoy_calc_field('Sales Delta', '_Delta')}]"
    assert _dep_column(worksheet, explicit_delta_name) is not None


# ---------------------------------------------------------------------------
# YD-6  Byte-identical when neither delta_measure nor comparison_kind/date_field set
# ---------------------------------------------------------------------------


def test_byte_identical_when_no_comparison_signal() -> None:
    xml_a = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        [KPI_SHEET_PRIMARY_ONLY, CHART_SHEET],
        dashboards=[
            {
                "name": "Executive Dashboard",
                "titles": ["KPI Sales Primary Only", "Revenue by Region"],
                "layout_grammar": {
                    "kind": "kpi_band_over_charts",
                    "kpi_tile_titles": ["KPI Sales Primary Only"],
                    "chart_titles": ["Revenue by Region"],
                },
            }
        ],
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    # Re-build with the identical spec a second time — determinism guard,
    # AND no CY/PY/Delta calc columns should appear anywhere.
    xml_b = twb_builder.build_twb_xml(
        "DS",
        "kpi_ds",
        "site",
        [KPI_SHEET_PRIMARY_ONLY, CHART_SHEET],
        dashboards=[
            {
                "name": "Executive Dashboard",
                "titles": ["KPI Sales Primary Only", "Revenue by Region"],
                "layout_grammar": {
                    "kind": "kpi_band_over_charts",
                    "kpi_tile_titles": ["KPI Sales Primary Only"],
                    "chart_titles": ["Revenue by Region"],
                },
            }
        ],
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    assert xml_a == xml_b
    assert "_YoY_CY" not in xml_a
    assert "_YoY_PY" not in xml_a


# ---------------------------------------------------------------------------
# YD-7  XSD validity (sqlproxy + embedded entry points)
# ---------------------------------------------------------------------------


def test_xsd_valid_computed_yoy_sqlproxy() -> None:
    xml = _build_yoy_xml()
    _assert_xsd_valid(xml)


def test_xsd_valid_computed_yoy_embedded(tmp_path: Path) -> None:
    hyper_file = _build_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper_file)
    xml = twb_builder.build_embedded_twb_xml(
        datasource_name="DS",
        hyper_filename=hyper_file.name,
        columns=columns,
        sheets=SHEETS_COMPUTED_YOY,
        dashboards=DASHBOARD_COMPUTED_YOY,
        design_theme=THEME_KPI,
        brand=BRAND,
    )
    _assert_xsd_valid(xml)


# ---------------------------------------------------------------------------
# YD-8  comparison_kind='mom' is carried but produces NO computed delta
# ---------------------------------------------------------------------------


def test_mom_comparison_kind_produces_no_computed_delta() -> None:
    """Scoping decision (see comparison.ts's docstring): the builder only
    implements the data-relative YoY recipe this slice. A 'mom' kind is
    carried on the wire but the KPI tile falls back to primary-BAN-only
    (no worse than before) until a future slice adds the MoM recipe."""
    xml = _build_yoy_xml([KPI_SHEET_MOM, CHART_SHEET])
    worksheet = ET.fromstring(xml).find(".//worksheets/worksheet[@name='KPI Sales MoM']")
    assert worksheet is not None
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_Delta')}]") is None
    runs = _label_runs(worksheet)
    assert len(runs) == 4  # caption, separator, primary, newline — no delta run
    columns = {e.get("column") for e in worksheet.findall(".//panes/pane/encodings/text")}
    assert columns == {_calc_field_ref("Sales", "")}


# ---------------------------------------------------------------------------
# YD-9  Integration: POST /workbook/dashboard camelCase dateField -> 200
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


def test_post_workbook_dashboard_computed_yoy_returns_200(tmp_path: Path) -> None:
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
                "kpi": {
                    "primaryMeasure": "Sales",
                    "comparisonKind": "yoy",
                    "dateField": "Order Date",
                },
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
    worksheet = _worksheet(twb_xml, "KPI Sales")
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_YoY_CY')}]") is not None
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_YoY_PY')}]") is not None
    assert _dep_column(worksheet, f"[{_yoy_calc_field('Sales', '_Delta')}]") is not None
    runs = _label_runs(worksheet)
    assert len(runs) == 6
