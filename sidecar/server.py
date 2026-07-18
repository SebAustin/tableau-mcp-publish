"""FastAPI authoring sidecar.

Exposes the .hyper / .tdsx / .twbx builders over loopback HTTP. The TypeScript MCP
server spawns this process and is the only intended caller. When SIDECAR_TOKEN is
set in the environment, every request must carry a matching X-Sidecar-Token header.
"""

from __future__ import annotations

import hmac
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import hyper_builder
import tds_builder
import twb_builder

app = FastAPI(title="tableau-authoring-sidecar")

OUTPUT_DIR = Path(tempfile.gettempdir()) / "tableau-mcp-publish"


@app.middleware("http")
async def token_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject requests without the shared per-spawn token (when one is configured)."""
    expected = os.environ.get("SIDECAR_TOKEN")
    if expected and not hmac.compare_digest(request.headers.get("X-Sidecar-Token", ""), expected):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


def _out(ext: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{uuid.uuid4().hex}.{ext}"


class QueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection: dict[str, Any]
    sql: str = ""
    name: str
    max_rows: int = Field(default=hyper_builder.DEFAULT_MAX_ROWS, alias="maxRows")


class TableRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    csv_path: str | None = Field(default=None, alias="csvPath")
    records: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Phase-1 optional encoding sub-models (Slice 2: carry end-to-end).
# These just need to PARSE and survive model_dump().
# Slice 3 will consume them in the builder.
# ---------------------------------------------------------------------------


class SheetColorModel(BaseModel):
    """Color encoding for a worksheet (mirrors schema.ts SheetColor)."""

    model_config = ConfigDict(populate_by_name=True)

    field: str
    kind: str  # "dimension" | "measure_names" | "measure"


class SheetKpiModel(BaseModel):
    """KPI tile configuration (mirrors schema.ts SheetKpi)."""

    model_config = ConfigDict(populate_by_name=True)

    primary_measure: str = Field(alias="primaryMeasure")
    comparison_measure: str | None = Field(default=None, alias="comparisonMeasure")
    delta_measure: str | None = Field(default=None, alias="deltaMeasure")
    delta_is_positive_good: bool | None = Field(default=None, alias="deltaIsPositiveGood")
    sparkline_field: str | None = Field(default=None, alias="sparklineField")
    value_prefix: str | None = Field(default=None, alias="valuePrefix")
    value_suffix: str | None = Field(default=None, alias="valueSuffix")


class SheetScatterModel(BaseModel):
    """Scatter-plot axis binding (mirrors schema.ts SheetScatter)."""

    model_config = ConfigDict(populate_by_name=True)

    x: str
    y: str
    breakdown: str | None = None


class SheetGeoModel(BaseModel):
    """Geographic / filled-map encoding (mirrors schema.ts SheetGeo)."""

    model_config = ConfigDict(populate_by_name=True)

    geo_field: str = Field(alias="geoField")
    geo_role: str = Field(alias="geoRole")  # "state" | "country" | "city" | "zipcode"
    color_measure: str | None = Field(default=None, alias="colorMeasure")


class SheetStyleRuleModel(BaseModel):
    """A single worksheet style-rule override (mirrors schema.ts SheetStyleRule).

    Design Excellence, Slice D1 — carry-only: not read by the builder yet.
    """

    model_config = ConfigDict(populate_by_name=True)

    element: str
    formats: dict[str, str]


class SheetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    mark_type: str = Field(default="bar", alias="markType")
    rows: list[str] = []
    cols: list[str] = []
    measures: list[str] = []
    # Phase-1 optional encoding fields (Slice 2: carry end-to-end).
    # Consumed by the builder in Slice 3.
    kind: str | None = None  # "chart" | "kpi_tile"
    color: SheetColorModel | None = None
    kpi: SheetKpiModel | None = None
    scatter: SheetScatterModel | None = None
    geo: SheetGeoModel | None = None
    # Design Excellence, Slice D1: optional per-sheet style-rule overrides.
    # Carry-only — the builder does not read this yet.
    style_rules: list[SheetStyleRuleModel] | None = Field(default=None, alias="styleRules")


class WorkbookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    datasource_name: str = Field(alias="datasourceName")
    datasource_content_url: str = Field(alias="datasourceContentUrl")
    site: str = ""
    server_url: str = Field(default="", alias="serverUrl")
    sheets: list[SheetModel]


# ---------------------------------------------------------------------------
# Dashboard-level text-zone and layout-grammar sub-models
# ---------------------------------------------------------------------------


class TextZoneModel(BaseModel):
    """A text zone to render in the dashboard (mirrors schema.ts TextZone)."""

    model_config = ConfigDict(populate_by_name=True)

    text: str
    position: str  # "header" | "footer"


class LayoutGrammarModel(BaseModel):
    """Layout grammar for the dashboard canvas (mirrors schema.ts LayoutGrammar)."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str  # "kpi_band_over_charts" | "tiled_vertical" | "tiled_horizontal"
    kpi_tile_titles: list[str] | None = Field(default=None, alias="kpiTileTitles")
    chart_titles: list[str] | None = Field(default=None, alias="chartTitles")


# ---------------------------------------------------------------------------
# Story sub-models (Phase E4 — Stories). Mirrors sidecar.ts's
# StoryPoint/Story interfaces. Same MODEL_DUMP LESSON as BrandModel below:
# these accept camelCase on the wire but model_dump() produces the snake_case
# field names (`captured_sheet`, `nav_type`) that twb_builder.py reads.
# ---------------------------------------------------------------------------


class StoryPointModel(BaseModel):
    """One story point (mirrors schema.ts's StoryArcPoint / sidecar.ts's StoryPoint)."""

    model_config = ConfigDict(populate_by_name=True)

    caption: str
    captured_sheet: str = Field(alias="capturedSheet")


class StoryModel(BaseModel):
    """A story — Tableau storyboard dashboard (Phase E4). Mirrors sidecar.ts's Story."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    nav_type: str = Field(default="caption", alias="navType")
    points: list[StoryPointModel]


# ---------------------------------------------------------------------------
# Brand block sub-models (Phase E1, Slice B — "Builder applies branding").
#
# Mirrors the flat wire shape produced by src/branding/builderBrand.ts's
# toBuilderBrand(), which itself projects the nested, zod-validated
# BrandFile (src/branding/schema.ts) down to just what the builder needs.
#
# THE MODEL_DUMP LESSON (see test_server_rich_dashboard.py): these models
# accept camelCase on the wire (via `alias=`) but `req.brand.model_dump()`
# — the only way twb_builder ever sees this data — produces the snake_case
# FIELD names below, never the aliases. twb_builder.py reads snake_case keys
# accordingly (`brand_name`, not `brandName`).
# ---------------------------------------------------------------------------


class BrandPaletteModel(BaseModel):
    """Flattened palette (mirrors builderBrand.ts BuilderBrandPalette)."""

    model_config = ConfigDict(populate_by_name=True)

    categorical: list[str] = Field(default_factory=list)
    sequential: list[str] = Field(default_factory=list)
    diverging: list[str] = Field(default_factory=list)
    good: str = "#59a14f"
    bad: str = "#e15759"
    neutral: str = "#898989"


class BrandFontSpecModel(BaseModel):
    """Title/body font spec: font + size + optional color."""

    model_config = ConfigDict(populate_by_name=True)

    font: str
    size: float
    color: str | None = None


class BrandBanFontSpecModel(BaseModel):
    """BAN (Big Number / KPI hero figure) font spec: font + size, no color."""

    model_config = ConfigDict(populate_by_name=True)

    font: str
    size: float


class BrandTypographyModel(BaseModel):
    """Mirrors builderBrand.ts BuilderBrandTypography."""

    model_config = ConfigDict(populate_by_name=True)

    title: BrandFontSpecModel
    body: BrandFontSpecModel
    ban: BrandBanFontSpecModel


class BrandFormatsModel(BaseModel):
    """Mirrors builderBrand.ts BuilderBrandFormats."""

    model_config = ConfigDict(populate_by_name=True)

    currency: str = "$#,##0"
    percent: str = "0.0%"
    number: str = "#,##0"


class BrandModel(BaseModel):
    """The resolved brand block a caller may attach to a dashboard-workbook build.

    Optional on the request: when absent, the builder emits byte-identical
    output to before this slice (no `<preferences>`, hardcoded title/subtitle
    fonts, no `default-format` on measure columns).
    """

    model_config = ConfigDict(populate_by_name=True)

    palette: BrandPaletteModel
    typography: BrandTypographyModel
    formats: BrandFormatsModel
    brand_name: str = Field(default="Brand", alias="brandName")


# ---------------------------------------------------------------------------
# Design-theme block sub-models (Design Excellence, Slice D1 — wire plumbing,
# carry-only). Mirrors the flat wire shape the corpus/theme layer will
# eventually resolve and embed on a DashboardPlan's `designTheme`
# (`src/planner/schema.ts`'s `DesignThemeSchema`) and forward as-is
# (`src/sidecar.ts`'s `DesignTheme`).
#
# THE MODEL_DUMP LESSON (see BrandModel's docstring above): these models
# accept camelCase on the wire (via `alias=`) but model_dump() produces the
# snake_case FIELD names below, never the aliases. This slice only carries
# the data end-to-end — the builder does not read `design_theme` yet (that
# lands in Slice D2 onward); absent → unchanged behavior, matching the
# Phase-1 "encodings carry-only" slice precedent.
# ---------------------------------------------------------------------------


class ThemeBorderModel(BaseModel):
    """Hairline border spec for a themed zone (mirrors schema.ts ThemeBorder)."""

    model_config = ConfigDict(populate_by_name=True)

    color: str | None = None
    style: str | None = None
    width: int | None = None


class ThemeDatalabelModel(BaseModel):
    """Datalabel styling (mirrors schema.ts ThemeDatalabel)."""

    model_config = ConfigDict(populate_by_name=True)

    font_size: int | None = Field(default=None, alias="fontSize")
    font_weight: str | None = Field(default=None, alias="fontWeight")
    color_mode: str | None = Field(default=None, alias="colorMode")


class ThemeChromeModel(BaseModel):
    """Chrome removal: gridlines/zeroline/ticks/mark-labels (mirrors schema.ts ThemeChrome)."""

    model_config = ConfigDict(populate_by_name=True)

    hide_gridlines: bool = Field(default=False, alias="hideGridlines")
    hide_zeroline: bool = Field(default=False, alias="hideZeroline")
    hide_axis_ticks: bool = Field(default=False, alias="hideAxisTicks")
    show_mark_labels: bool = Field(default=False, alias="showMarkLabels")
    datalabel: ThemeDatalabelModel | None = None


class ThemeSpacingModel(BaseModel):
    """Canvas spacing (mirrors schema.ts ThemeSpacing)."""

    model_config = ConfigDict(populate_by_name=True)

    outer_margin: int | None = Field(default=None, alias="outerMargin")
    gutter: int | None = None


class ThemeChartCardModel(BaseModel):
    """Chart-card zone-style box model (mirrors schema.ts ThemeChartCard)."""

    model_config = ConfigDict(populate_by_name=True)

    background: str | None = None
    border: ThemeBorderModel | None = None
    padding: int | None = None
    margin: int | None = None
    corner_radius: int | None = Field(default=None, alias="cornerRadius")


class ThemeKpiTileModel(BaseModel):
    """KPI-tile zone-style box model (mirrors schema.ts ThemeKpiTile)."""

    model_config = ConfigDict(populate_by_name=True)

    background: str | None = None
    border: ThemeBorderModel | None = None
    padding: int | None = None
    ban_color: str | None = Field(default=None, alias="banColor")
    use_semantic_delta_colors: bool = Field(default=True, alias="useSemanticDeltaColors")


class ThemeHeaderModel(BaseModel):
    """Header-band styling (mirrors schema.ts ThemeHeader)."""

    model_config = ConfigDict(populate_by_name=True)

    background: str | None = None
    title_color: str | None = Field(default=None, alias="titleColor")
    subtitle_color: str | None = Field(default=None, alias="subtitleColor")


class DesignThemeModel(BaseModel):
    """Resolved design theme block (mirrors schema.ts DesignTheme / sidecar.ts DesignTheme).

    Optional on the request: when absent, behavior is unchanged from before
    this slice (the builder does not read this field yet).
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    dashboard_background: str | None = Field(default=None, alias="dashboardBackground")
    spacing: ThemeSpacingModel | None = None
    chart_card: ThemeChartCardModel | None = Field(default=None, alias="chartCard")
    kpi_tile: ThemeKpiTileModel | None = Field(default=None, alias="kpiTile")
    header: ThemeHeaderModel | None = None
    chrome: ThemeChromeModel | None = None


class DashboardWorkbookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    datasource_name: str = Field(alias="datasourceName")
    datasource_content_url: str = Field(alias="datasourceContentUrl")
    site: str = ""
    server_url: str = Field(default="", alias="serverUrl")
    sheets: list[SheetModel]
    dashboard_layout: str = Field(default="tiled_vertical", alias="dashboardLayout")
    canvas_width: int = Field(default=1000, alias="canvasWidth")
    canvas_height: int = Field(default=800, alias="canvasHeight")
    # When provided, the workbook embeds the .hyper extract directly (federated
    # connection) instead of referencing a published datasource via sqlproxy.
    # This is the self-contained path that renders on Tableau Cloud without a
    # prior publish_datasource step.
    hyper_path: str | None = Field(default=None, alias="hyperPath")
    # Phase-1 optional dashboard-level fields (Slice 2: carry end-to-end).
    # Consumed by the builder in Slice 3.
    dashboard_title: str | None = Field(default=None, alias="dashboardTitle")
    dashboard_subtitle: str | None = Field(default=None, alias="dashboardSubtitle")
    text_zones: list[TextZoneModel] | None = Field(default=None, alias="textZones")
    layout_grammar: LayoutGrammarModel | None = Field(default=None, alias="layoutGrammar")
    # Phase E1, Slice B: optional resolved brand block. Absent → byte-identical
    # output to before this slice (see BrandModel docstring + twb_builder guards).
    brand: BrandModel | None = None
    # Design Excellence, Slice D1: optional resolved design-theme block.
    # Carry-only — the builder does not read this yet (lands in Slice D2
    # onward). Absent → unchanged output, same guard pattern as `brand`.
    design_theme: DesignThemeModel | None = Field(default=None, alias="designTheme")
    # Phase E4: optional stories (Tableau storyboard dashboards). Each story's
    # captured_sheet is validated against the actual worksheet/dashboard names
    # by twb_builder._build_story (raises ValueError, listing valid names,
    # otherwise). Absent → byte-identical output to before this slice.
    stories: list[StoryModel] | None = None


class FileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    file_path: str = Field(alias="filePath")
    file_type: str | None = Field(default=None, alias="fileType")
    excel_sheet: str | int | None = Field(default=None, alias="excelSheet")
    json_path: str | None = Field(default=None, alias="jsonPath")
    max_rows: int = Field(default=hyper_builder.DEFAULT_MAX_ROWS, alias="maxRows")
    # csv only; auto-sniffed from the file's BOM/header when omitted.
    encoding: str | None = Field(default=None, alias="encoding")
    delimiter: str | None = Field(default=None, alias="delimiter")


# ---------------------------------------------------------------------------
# Live-connection datasource models — Phase E2 slice C (data connectivity +
# embedded-credential publish). See tds_builder.py's live-connection section
# for the full VERIFY-LIVE attribute mapping + the known-impossible Snowflake
# key-pair case. This model NEVER carries credentials — username/password
# travel separately, at publish time, via the TypeScript layer's
# `<connectionCredentials>` element (src/rest/credentials.ts); if a caller
# mistakenly includes them here, Pydantic silently drops the unknown fields
# (no `extra="forbid"`) since this model simply doesn't declare them.
# ---------------------------------------------------------------------------

_SNOWFLAKE_AUTH_METHODS = {"username-password", "oauth"}
# Known-impossible per the Phase E2 plan: Snowflake key-pair auth cannot be
# embedded via REST (Tableau Desktop-only) — rejected with a clean 400
# instead of silently emitting XML that will never actually authenticate.
_SNOWFLAKE_IMPOSSIBLE_AUTH = {"key-pair", "keypair", "key_pair", "jwt"}


class LiveConnectionSpec(BaseModel):
    """Cloud live-connection topology (Snowflake or Presto/Trino) — no credentials, no extract.

    THE MODEL_DUMP LESSON (see ``BrandModel``'s docstring above): this model
    accepts camelCase on the wire (via ``alias=``) but
    ``req.connection.model_dump()`` — the only way ``tds_builder`` ever sees
    this data — produces the snake_case FIELD names below, never the
    aliases. ``tds_builder.build_live_tds`` reads snake_case keys accordingly
    (``db_schema``, not ``schema``).
    """

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["snowflake", "presto"]
    server: str
    db_schema: str = Field(alias="schema")
    table: str
    # Snowflake-only (ignored when type == "presto").
    warehouse: str | None = None
    dbname: str | None = None
    authentication: str = "username-password"
    role: str | None = None
    # Presto-only (ignored when type == "snowflake").
    port: int | None = None
    catalog: str | None = None
    ssl: bool = True


class LiveDatasourceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    connection: LiveConnectionSpec


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/datasource/from-query")
def datasource_from_query(req: QueryRequest) -> dict[str, str]:
    df = hyper_builder.query_to_dataframe(req.connection, req.sql, req.max_rows)
    hyper_path = hyper_builder.dataframe_to_hyper(df, _out("hyper"))
    tdsx_path = tds_builder.hyper_to_tdsx(hyper_path, req.name, _out("tdsx"))
    return {"path": str(tdsx_path)}


@app.post("/datasource/from-table")
def datasource_from_table(req: TableRequest) -> dict[str, str]:
    if req.csv_path:
        df = hyper_builder.query_to_dataframe({"type": "csv", "path": req.csv_path}, "")
    elif req.records:
        df = hyper_builder.records_to_dataframe(req.records)
    else:
        raise HTTPException(status_code=400, detail="csvPath or records is required")
    hyper_path = hyper_builder.dataframe_to_hyper(df, _out("hyper"))
    tdsx_path = tds_builder.hyper_to_tdsx(hyper_path, req.name, _out("tdsx"))
    return {"path": str(tdsx_path)}


@app.post("/workbook/starter")
def workbook_starter(req: WorkbookRequest) -> dict[str, str]:
    sheets = [s.model_dump() for s in req.sheets]
    twbx_path = twb_builder.build_starter_twbx(
        datasource_name=req.datasource_name,
        datasource_content_url=req.datasource_content_url,
        site=req.site,
        sheets=sheets,
        out_path=_out("twbx"),
        server_url=req.server_url,
    )
    return {"path": str(twbx_path)}


@app.post("/workbook/dashboard")
def workbook_dashboard(req: DashboardWorkbookRequest) -> dict[str, str]:
    """Build a .twbx with a <dashboard> block tiling all sheets.

    When ``hyperPath`` is provided the workbook embeds the .hyper extract
    directly (federated connection, self-contained) so it renders on Tableau
    Cloud without a separately published datasource.

    When ``hyperPath`` is absent the workbook references the published
    datasource via sqlproxy (legacy path, kept for backward compatibility).
    """
    allowed_layouts = {"tiled_vertical", "tiled_horizontal"}
    if req.dashboard_layout not in allowed_layouts:
        raise HTTPException(
            status_code=400,
            detail=f"dashboard_layout must be one of {sorted(allowed_layouts)}",
        )
    if req.canvas_width <= 0 or req.canvas_height <= 0:
        raise HTTPException(status_code=400, detail="canvasWidth and canvasHeight must be positive")

    sheets = [s.model_dump() for s in req.sheets]
    sheet_titles = [str(s["title"]) for s in sheets]

    # Build the dashboard spec, threading optional Slice 3B fields through.
    # ``layout_grammar`` is serialised from the Pydantic model; we normalise it
    # to a plain dict so _build_dashboard can access it via .get().
    layout_grammar_dict: dict[str, object] | None = None
    if req.layout_grammar is not None:
        layout_grammar_dict = {
            "kind": req.layout_grammar.kind,
            "kpi_tile_titles": req.layout_grammar.kpi_tile_titles,
            "chart_titles": req.layout_grammar.chart_titles,
        }

    text_zones_list: list[dict[str, str]] | None = None
    if req.text_zones:
        text_zones_list = [{"text": tz.text, "position": tz.position} for tz in req.text_zones]

    dashboards = [
        {
            "name": "Dashboard 1",
            "titles": sheet_titles,
            "title": req.dashboard_title,
            "subtitle": req.dashboard_subtitle,
            "text_zones": text_zones_list,
            "layout_grammar": layout_grammar_dict,
        }
    ]

    # Phase E1, Slice B: model_dump() the optional brand block once, snake_case
    # (THE MODEL_DUMP LESSON — see BrandModel's docstring). None when absent,
    # which keeps both build paths byte-identical to before this slice.
    brand_dict: dict[str, Any] | None = req.brand.model_dump() if req.brand is not None else None

    # Phase E4: model_dump() the optional stories list once, snake_case (same
    # MODEL_DUMP LESSON). None when absent, keeping both build paths
    # byte-identical to before this slice.
    stories_list: list[dict[str, Any]] | None = (
        [s.model_dump() for s in req.stories] if req.stories else None
    )

    # Design Excellence, Slice D2: model_dump() the optional design_theme
    # block once, snake_case (same MODEL_DUMP LESSON as brand/stories above).
    # None when absent, keeping both build paths byte-identical to before
    # this slice.
    design_theme_dict: dict[str, Any] | None = (
        req.design_theme.model_dump() if req.design_theme is not None else None
    )

    if req.hyper_path:
        hyper_file = Path(req.hyper_path)
        if not hyper_file.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"hyperPath not found or not a file: {req.hyper_path!r}. "
                "Build the extract first via /datasource/from-file.",
            )
        twbx_path = twb_builder.build_embedded_twbx(
            datasource_name=req.datasource_name,
            hyper_path=hyper_file,
            sheets=sheets,
            out_path=_out("twbx"),
            dashboards=dashboards,
            dashboard_layout=req.dashboard_layout,
            canvas_width=req.canvas_width,
            canvas_height=req.canvas_height,
            brand=brand_dict,
            stories=stories_list,
            design_theme=design_theme_dict,
        )
    else:
        twbx_path = twb_builder.build_starter_twbx(
            datasource_name=req.datasource_name,
            datasource_content_url=req.datasource_content_url,
            site=req.site,
            sheets=sheets,
            out_path=_out("twbx"),
            server_url=req.server_url,
            dashboards=dashboards,
            dashboard_layout=req.dashboard_layout,
            canvas_width=req.canvas_width,
            canvas_height=req.canvas_height,
            brand=brand_dict,
            stories=stories_list,
            design_theme=design_theme_dict,
        )
    return {"path": str(twbx_path)}


class ColumnInfo(BaseModel):
    """A single column descriptor returned by the file-ingest route.

    Uses camelCase ``dataType`` to match the TypeScript ``ColumnInfo`` interface.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    dataType: str  # noqa: N815 — intentional camelCase to match the TS client contract


class FileResult(BaseModel):
    """Response body for /datasource/from-file.

    ``path`` is the .tdsx path (published datasource archive).
    ``hyper_path`` is the raw .hyper extract path; pass it as ``hyperPath``
    to /workbook/dashboard so the workbook embeds the extract directly.
    ``columns`` are the real column descriptors from the file schema.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    hyper_path: str = Field(alias="hyperPath")
    columns: list[ColumnInfo]


@app.post("/datasource/from-file")
def datasource_from_file(req: FileRequest) -> FileResult:
    """Build a .tdsx from a local file (csv, json, jsonl, xlsx, xls, parquet).

    The response includes ``columns``: a list of ``{name, dataType}`` descriptors
    derived from the file's real schema so callers can bind worksheets to actual
    field names rather than guessing.
    """
    file_type = req.file_type
    if file_type is None:
        # Infer from extension.
        ext = req.file_path.rsplit(".", 1)[-1].lower() if "." in req.file_path else ""
        supported = {"csv", "json", "jsonl", "xlsx", "xls", "parquet"}
        if ext not in supported:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot infer file type from extension {ext!r}. "
                f"Provide fileType explicitly. Supported: {sorted(supported)}",
            )
        file_type = ext

    try:
        df = hyper_builder.file_to_dataframe(
            file_type=file_type,
            path=req.file_path,
            excel_sheet=req.excel_sheet,
            json_path=req.json_path,
            max_rows=req.max_rows,
            encoding=req.encoding,
            sep=req.delimiter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    columns = hyper_builder.dataframe_columns(df)
    hyper_path = hyper_builder.dataframe_to_hyper(df, _out("hyper"))
    tdsx_path = tds_builder.hyper_to_tdsx(hyper_path, req.name, _out("tdsx"))
    return FileResult(
        path=str(tdsx_path),
        hyperPath=str(hyper_path),
        columns=[ColumnInfo(name=c["name"], dataType=c["dataType"]) for c in columns],
    )


@app.post("/datasource/live")
def datasource_live(req: LiveDatasourceRequest) -> dict[str, str]:
    """Build a live-connection .tds (Snowflake or Presto — no extract, no credentials).

    Validates type-specific required fields and rejects the known-impossible
    Snowflake key-pair authentication case with a clean, actionable 400
    (Tableau's Publish Datasource API only supports embedding
    username/password or OAuth credentials — key-pair auth is Desktop-only,
    see tds_builder.py's live-connection section).
    """
    conn = req.connection
    auth_normalized = conn.authentication.strip().lower().replace(" ", "-").replace("_", "-")

    if conn.type == "snowflake":
        if auth_normalized in _SNOWFLAKE_IMPOSSIBLE_AUTH:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Snowflake key-pair authentication ({conn.authentication!r}) is not "
                    "REST-publishable: Tableau's Publish Datasource API only supports embedding "
                    "username/password or OAuth credentials. Configure key-pair auth in Tableau "
                    "Desktop and publish from there instead."
                ),
            )
        if auth_normalized not in _SNOWFLAKE_AUTH_METHODS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported Snowflake authentication {conn.authentication!r}. "
                    f"Use one of {sorted(_SNOWFLAKE_AUTH_METHODS)}."
                ),
            )
        missing = [f for f in ("warehouse", "dbname") if getattr(conn, f) is None]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"snowflake connections require {missing} to be set.",
            )
    else:  # presto
        if conn.catalog is None:
            raise HTTPException(status_code=400, detail="presto connections require 'catalog'.")

    tds_xml = tds_builder.build_live_tds(req.name, conn.model_dump())
    out_path = _out("tds")
    out_path.write_text(tds_xml, encoding="utf-8")
    return {"path": str(out_path)}
