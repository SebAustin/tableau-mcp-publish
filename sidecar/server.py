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
