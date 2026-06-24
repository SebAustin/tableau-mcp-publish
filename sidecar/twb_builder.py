"""Generate a starter .twb / .twbx workbook bound to a *published* datasource.

The workbook references the datasource on the server (class='sqlproxy' +
repository-location), so it carries no local data — a plain .twb zipped into a
.twbx is sufficient. Each sheet spec becomes a worksheet with a Columns/Rows
shelf layout, a mark class, and a <datasource-dependencies> block whose field
names match the requested fields exactly.

Mark types bar/line/text are well supported; map is experimental.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

TWB_VERSION = "18.1"
SOURCE_BUILD = "2024.1.0"

_MARK_CLASS = {"bar": "Bar", "line": "Line", "text": "Text", "map": "Map"}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return cleaned or "datasource"


def _dim_instance(field: str) -> str:
    return f"[none:{field}:nk]"


def _measure_instance(field: str) -> str:
    return f"[sum:{field}:qk]"


def _repository_path(site: str) -> str:
    return f"/t/{site}/datasources" if site else "/datasources"


def _add_dependency_columns(
    parent: ET.Element, dimensions: list[str], measures: list[str]
) -> None:
    for field in dimensions:
        ET.SubElement(
            parent,
            "column",
            {"datatype": "string", "name": f"[{field}]", "role": "dimension", "type": "nominal"},
        )
        ET.SubElement(
            parent,
            "column-instance",
            {
                "column": f"[{field}]",
                "derivation": "None",
                "name": _dim_instance(field),
                "pivot": "key",
                "type": "nominal",
            },
        )
    for field in measures:
        ET.SubElement(
            parent,
            "column",
            {"datatype": "real", "name": f"[{field}]", "role": "measure", "type": "quantitative"},
        )
        ET.SubElement(
            parent,
            "column-instance",
            {
                "column": f"[{field}]",
                "derivation": "Sum",
                "name": _measure_instance(field),
                "pivot": "key",
                "type": "quantitative",
            },
        )


def _ds_internal_name(content_key: str) -> str:
    """Internal workbook datasource id for a published (sqlproxy) connection."""
    safe = re.sub(r"[^A-Za-z0-9_]+", "", content_key) or "datasource"
    return f"sqlproxy.{safe}"


def _build_worksheet(sheet: dict[str, Any], ds_caption: str, ds_internal: str) -> ET.Element:
    title = str(sheet["title"])
    mark_type = str(sheet.get("mark_type", "bar")).lower()
    cols_dims = [str(c) for c in sheet.get("cols", [])]
    rows_dims = [str(r) for r in sheet.get("rows", [])]
    measures = [str(m) for m in sheet.get("measures", [])]

    worksheet = ET.Element("worksheet", {"name": title})
    table = ET.SubElement(worksheet, "table")
    view = ET.SubElement(table, "view")
    datasources = ET.SubElement(view, "datasources")
    ET.SubElement(
        datasources,
        "datasource",
        {"caption": ds_caption, "name": ds_internal},
    )

    deps = ET.SubElement(view, "datasource-dependencies", {"datasource": ds_internal})
    _add_dependency_columns(deps, cols_dims + rows_dims, measures)

    ds_ref = f"[{ds_internal}]"
    cols_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in cols_dims]
    rows_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in rows_dims]
    rows_exprs += [f"{ds_ref}.{_measure_instance(f)}" for f in measures]

    ET.SubElement(table, "rows").text = " / ".join(rows_exprs)
    ET.SubElement(table, "cols").text = " / ".join(cols_exprs)

    panes = ET.SubElement(table, "panes")
    pane = ET.SubElement(panes, "pane")
    pane_view = ET.SubElement(pane, "view")
    ET.SubElement(pane_view, "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": _MARK_CLASS.get(mark_type, "Automatic")})

    # For a text/table mark, place the first measure on the Text encoding so it renders.
    if mark_type == "text" and measures:
        encodings = ET.SubElement(pane, "encodings")
        ET.SubElement(encodings, "text", {"column": f"{ds_ref}.{_measure_instance(measures[0])}"})

    return worksheet


def _server_host(server_url: str) -> str:
    if not server_url:
        return ""
    if "://" in server_url:
        return server_url.split("://", 1)[1].split("/", 1)[0]
    return server_url.split("/", 1)[0]


def build_twb_xml(
    datasource_name: str,
    datasource_content_url: str,
    site: str,
    sheets: list[dict[str, Any]],
    server_url: str = "",
) -> str:
    slug = _slug(datasource_name)
    content_key = datasource_content_url or slug
    ds_internal = _ds_internal_name(content_key)
    server_host = _server_host(server_url)
    workbook = ET.Element(
        "workbook",
        {"source-build": SOURCE_BUILD, "version": TWB_VERSION},
    )

    # --- Published datasource reference -------------------------------------
    datasources = ET.SubElement(workbook, "datasources")
    datasource = ET.SubElement(
        datasources,
        "datasource",
        {
            "caption": datasource_name,
            "name": ds_internal,
            "version": TWB_VERSION,
            "inline": "true",
        },
    )
    repo_attrs: dict[str, str] = {
        "id": content_key,
        "path": _repository_path(site),
        "revision": "1.0",
    }
    if site:
        repo_attrs["site"] = site
    ET.SubElement(datasource, "repository-location", repo_attrs)
    conn_attrs: dict[str, str] = {
        "class": "sqlproxy",
        "dbname": content_key,
    }
    if server_host:
        conn_attrs.update(
            {
                "channel": "https",
                "directory": "/dataserver",
                "port": "443",
                "server": server_host,
            }
        )
    ET.SubElement(datasource, "connection", conn_attrs)

    # Declare every referenced field once at the datasource level.
    seen_dims: list[str] = []
    seen_measures: list[str] = []
    for sheet in sheets:
        for field in [*sheet.get("cols", []), *sheet.get("rows", [])]:
            if str(field) not in seen_dims:
                seen_dims.append(str(field))
        for field in sheet.get("measures", []):
            if str(field) not in seen_measures:
                seen_measures.append(str(field))
    for field in seen_dims:
        ET.SubElement(
            datasource,
            "column",
            {"datatype": "string", "name": f"[{field}]", "role": "dimension", "type": "nominal"},
        )
    for field in seen_measures:
        ET.SubElement(
            datasource,
            "column",
            {"datatype": "real", "name": f"[{field}]", "role": "measure", "type": "quantitative"},
        )

    # --- Worksheets + windows ----------------------------------------------
    worksheets = ET.SubElement(workbook, "worksheets")
    windows = ET.SubElement(workbook, "windows")
    for sheet in sheets:
        worksheets.append(_build_worksheet(sheet, datasource_name, ds_internal))
        ET.SubElement(windows, "window", {"class": "worksheet", "name": str(sheet["title"])})

    xml_body = ET.tostring(workbook, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"


def build_starter_twbx(
    datasource_name: str,
    datasource_content_url: str,
    site: str,
    sheets: list[dict[str, Any]],
    out_path: Path,
    server_url: str = "",
) -> Path:
    """Build a .twbx (zip containing the generated .twb) for a published datasource."""
    twb_xml = build_twb_xml(
        datasource_name, datasource_content_url, site, sheets, server_url=server_url
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.twb", twb_xml)
    return out_path
