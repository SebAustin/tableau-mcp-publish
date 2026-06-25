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
from typing import Any

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


class SheetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    mark_type: str = Field(default="bar", alias="markType")
    rows: list[str] = []
    cols: list[str] = []
    measures: list[str] = []


class WorkbookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    datasource_name: str = Field(alias="datasourceName")
    datasource_content_url: str = Field(alias="datasourceContentUrl")
    site: str = ""
    server_url: str = Field(default="", alias="serverUrl")
    sheets: list[SheetModel]


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


class FileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    file_path: str = Field(alias="filePath")
    file_type: str | None = Field(default=None, alias="fileType")
    excel_sheet: str | int | None = Field(default=None, alias="excelSheet")
    json_path: str | None = Field(default=None, alias="jsonPath")
    max_rows: int = Field(default=hyper_builder.DEFAULT_MAX_ROWS, alias="maxRows")


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
    """Build a .twbx with a <dashboard> block tiling all sheets."""
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
    dashboards = [{"name": "Dashboard 1", "titles": sheet_titles}]

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
    """Response body for /datasource/from-file."""

    model_config = ConfigDict(populate_by_name=True)

    path: str
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    columns = hyper_builder.dataframe_columns(df)
    hyper_path = hyper_builder.dataframe_to_hyper(df, _out("hyper"))
    tdsx_path = tds_builder.hyper_to_tdsx(hyper_path, req.name, _out("tdsx"))
    return FileResult(
        path=str(tdsx_path),
        columns=[ColumnInfo(name=c["name"], dataType=c["dataType"]) for c in columns],
    )
