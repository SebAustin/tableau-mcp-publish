"""Tests for Slice 2 — schema growth: SheetModel / DashboardWorkbookRequest new optional fields.

Verifies that:
1. A minimal old-style payload (title/markType/rows/cols/measures) still parses
   (backward-compatibility guard).
2. All new SheetModel optional fields (kind / color / kpi / scatter / geo) parse
   and round-trip through model_dump().
3. DashboardWorkbookRequest accepts the new optional dashboard-level fields
   (dashboard_title / dashboard_subtitle / text_zones / layout_grammar) and they
   survive model_dump().
4. Old minimal DashboardWorkbookRequest payloads still parse (backward-compat).
"""

from __future__ import annotations

from server import (
    DashboardWorkbookRequest,
    SheetModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_SHEET_PAYLOAD: dict[str, object] = {
    "title": "Revenue by Region",
    "markType": "bar",
    "rows": ["Region"],
    "cols": [],
    "measures": ["Sales"],
}

MINIMAL_DASHBOARD_PAYLOAD: dict[str, object] = {
    "datasourceName": "Superstore",
    "datasourceContentUrl": "superstore",
    "site": "",
    "sheets": [MINIMAL_SHEET_PAYLOAD],
    "dashboardLayout": "tiled_vertical",
    "canvasWidth": 1000,
    "canvasHeight": 800,
}


# ===========================================================================
# SheetModel — backward-compatibility
# ===========================================================================


def test_sheet_model_minimal_parses() -> None:
    """A minimal old-style sheet payload must parse without new fields."""
    sheet = SheetModel.model_validate(MINIMAL_SHEET_PAYLOAD)
    assert sheet.title == "Revenue by Region"
    assert sheet.mark_type == "bar"
    assert sheet.rows == ["Region"]
    assert sheet.kind is None
    assert sheet.color is None
    assert sheet.kpi is None
    assert sheet.scatter is None
    assert sheet.geo is None


def test_sheet_model_minimal_round_trips() -> None:
    """model_dump() on a minimal sheet must not include None optional fields as non-None."""
    sheet = SheetModel.model_validate(MINIMAL_SHEET_PAYLOAD)
    dumped = sheet.model_dump()
    assert dumped["title"] == "Revenue by Region"
    assert dumped["kind"] is None
    assert dumped["color"] is None
    assert dumped["kpi"] is None
    assert dumped["scatter"] is None
    assert dumped["geo"] is None


# ===========================================================================
# SheetModel — optional kind field
# ===========================================================================


def test_sheet_model_kind_kpi_tile() -> None:
    """kind='kpi_tile' must parse and survive round-trip."""
    sheet = SheetModel.model_validate({**MINIMAL_SHEET_PAYLOAD, "kind": "kpi_tile"})
    assert sheet.kind == "kpi_tile"
    assert sheet.model_dump()["kind"] == "kpi_tile"


def test_sheet_model_kind_chart() -> None:
    """kind='chart' must parse."""
    sheet = SheetModel.model_validate({**MINIMAL_SHEET_PAYLOAD, "kind": "chart"})
    assert sheet.kind == "chart"


# ===========================================================================
# SheetModel — optional color field
# ===========================================================================


def test_sheet_model_color_dimension() -> None:
    """color block with kind='dimension' must parse and round-trip."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "color": {"field": "Category", "kind": "dimension"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.color is not None
    assert sheet.color.field == "Category"
    assert sheet.color.kind == "dimension"
    dumped = sheet.model_dump()
    assert dumped["color"]["field"] == "Category"


def test_sheet_model_color_measure() -> None:
    """color block with kind='measure' must parse."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "markType": "map_filled",
        "color": {"field": "Profit", "kind": "measure"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.color is not None
    assert sheet.color.kind == "measure"


def test_sheet_model_color_measure_names() -> None:
    """color block with kind='measure_names' must parse."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "color": {"field": "Measure Names", "kind": "measure_names"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.color is not None
    assert sheet.color.kind == "measure_names"


# ===========================================================================
# SheetModel — optional kpi field
# ===========================================================================


def test_sheet_model_kpi_full() -> None:
    """A fully-specified kpi block must parse and round-trip."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "kind": "kpi_tile",
        "kpi": {
            "primaryMeasure": "CP Sales",
            "comparisonMeasure": "PP Sales",
            "deltaMeasure": "Sales Difference",
            "deltaIsPositiveGood": True,
            "sparklineField": "Order Date",
            "valuePrefix": "$",
            "valueSuffix": "K",
        },
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.kpi is not None
    assert sheet.kpi.primary_measure == "CP Sales"
    assert sheet.kpi.comparison_measure == "PP Sales"
    assert sheet.kpi.delta_is_positive_good is True
    assert sheet.kpi.value_prefix == "$"
    dumped = sheet.model_dump()
    assert dumped["kpi"]["primary_measure"] == "CP Sales"


def test_sheet_model_kpi_minimal() -> None:
    """A kpi block with only primaryMeasure must parse."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "kind": "kpi_tile",
        "kpi": {"primaryMeasure": "Profit"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.kpi is not None
    assert sheet.kpi.primary_measure == "Profit"
    assert sheet.kpi.comparison_measure is None
    assert sheet.kpi.delta_measure is None


# ===========================================================================
# SheetModel — optional scatter field
# ===========================================================================


def test_sheet_model_scatter_with_breakdown() -> None:
    """scatter block with x, y, and breakdown must parse and round-trip."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "markType": "scatter",
        "scatter": {"x": "Sales", "y": "Profit", "breakdown": "Category"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.scatter is not None
    assert sheet.scatter.x == "Sales"
    assert sheet.scatter.y == "Profit"
    assert sheet.scatter.breakdown == "Category"
    dumped = sheet.model_dump()
    assert dumped["scatter"]["x"] == "Sales"


def test_sheet_model_scatter_without_breakdown() -> None:
    """scatter block without breakdown must parse (breakdown is optional)."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "markType": "scatter",
        "scatter": {"x": "Quantity", "y": "Discount"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.scatter is not None
    assert sheet.scatter.breakdown is None


# ===========================================================================
# SheetModel — optional geo field
# ===========================================================================


def test_sheet_model_geo_state_with_color() -> None:
    """geo block with geoRole='state' and colorMeasure must parse and round-trip."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "markType": "map_filled",
        "geo": {"geoField": "State", "geoRole": "state", "colorMeasure": "Profit"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.geo is not None
    assert sheet.geo.geo_field == "State"
    assert sheet.geo.geo_role == "state"
    assert sheet.geo.color_measure == "Profit"
    dumped = sheet.model_dump()
    assert dumped["geo"]["geo_field"] == "State"


def test_sheet_model_geo_country_no_color() -> None:
    """geo block with geoRole='country' and no colorMeasure must parse."""
    payload = {
        **MINIMAL_SHEET_PAYLOAD,
        "markType": "map_filled",
        "geo": {"geoField": "Country", "geoRole": "country"},
    }
    sheet = SheetModel.model_validate(payload)
    assert sheet.geo is not None
    assert sheet.geo.color_measure is None


# ===========================================================================
# DashboardWorkbookRequest — backward-compatibility
# ===========================================================================


def test_dashboard_request_minimal_parses() -> None:
    """A minimal old-style request must parse without new dashboard-level fields."""
    req = DashboardWorkbookRequest.model_validate(MINIMAL_DASHBOARD_PAYLOAD)
    assert req.datasource_name == "Superstore"
    assert req.dashboard_title is None
    assert req.dashboard_subtitle is None
    assert req.text_zones is None
    assert req.layout_grammar is None


# ===========================================================================
# DashboardWorkbookRequest — new optional dashboard-level fields
# ===========================================================================


def test_dashboard_request_with_title_and_subtitle() -> None:
    """dashboard_title and dashboard_subtitle must parse and survive round-trip."""
    payload = {
        **MINIMAL_DASHBOARD_PAYLOAD,
        "dashboardTitle": "Executive Overview",
        "dashboardSubtitle": "Q1 2024 Performance",
    }
    req = DashboardWorkbookRequest.model_validate(payload)
    assert req.dashboard_title == "Executive Overview"
    assert req.dashboard_subtitle == "Q1 2024 Performance"
    dumped = req.model_dump()
    assert dumped["dashboard_title"] == "Executive Overview"
    assert dumped["dashboard_subtitle"] == "Q1 2024 Performance"


def test_dashboard_request_with_text_zones() -> None:
    """text_zones list must parse and survive round-trip."""
    payload = {
        **MINIMAL_DASHBOARD_PAYLOAD,
        "textZones": [
            {"text": "Q1 2024 Executive Dashboard", "position": "header"},
            {"text": "Confidential", "position": "footer"},
        ],
    }
    req = DashboardWorkbookRequest.model_validate(payload)
    assert req.text_zones is not None
    assert len(req.text_zones) == 2
    assert req.text_zones[0].text == "Q1 2024 Executive Dashboard"
    assert req.text_zones[0].position == "header"
    assert req.text_zones[1].position == "footer"
    dumped = req.model_dump()
    assert len(dumped["text_zones"]) == 2


def test_dashboard_request_with_layout_grammar_kpi_band() -> None:
    """layoutGrammar with kind='kpi_band_over_charts' must parse and survive round-trip."""
    payload = {
        **MINIMAL_DASHBOARD_PAYLOAD,
        "layoutGrammar": {
            "kind": "kpi_band_over_charts",
            "kpiTileTitles": ["Sales KPI", "Profit KPI"],
            "chartTitles": ["Revenue by Region", "Sales by Category"],
        },
    }
    req = DashboardWorkbookRequest.model_validate(payload)
    assert req.layout_grammar is not None
    assert req.layout_grammar.kind == "kpi_band_over_charts"
    assert req.layout_grammar.kpi_tile_titles == ["Sales KPI", "Profit KPI"]
    assert req.layout_grammar.chart_titles == ["Revenue by Region", "Sales by Category"]
    dumped = req.model_dump()
    assert dumped["layout_grammar"]["kind"] == "kpi_band_over_charts"
    assert dumped["layout_grammar"]["kpi_tile_titles"] == ["Sales KPI", "Profit KPI"]


def test_dashboard_request_with_all_new_fields() -> None:
    """All four new dashboard-level fields together must parse cleanly."""
    payload = {
        **MINIMAL_DASHBOARD_PAYLOAD,
        "dashboardTitle": "Executive Overview",
        "dashboardSubtitle": "Q1 2024",
        "textZones": [{"text": "Header Text", "position": "header"}],
        "layoutGrammar": {"kind": "tiled_vertical"},
        "sheets": [
            {
                **MINIMAL_SHEET_PAYLOAD,
                "kind": "kpi_tile",
                "kpi": {"primaryMeasure": "Sales"},
                "color": {"field": "Category", "kind": "dimension"},
                "scatter": {"x": "Sales", "y": "Profit"},
                "geo": {"geoField": "State", "geoRole": "state"},
            }
        ],
    }
    req = DashboardWorkbookRequest.model_validate(payload)
    assert req.dashboard_title == "Executive Overview"
    assert req.layout_grammar is not None
    assert req.layout_grammar.kind == "tiled_vertical"
    assert len(req.sheets) == 1
    sheet = req.sheets[0]
    assert sheet.kind == "kpi_tile"
    assert sheet.kpi is not None
    assert sheet.kpi.primary_measure == "Sales"
    assert sheet.geo is not None
    assert sheet.geo.geo_role == "state"
