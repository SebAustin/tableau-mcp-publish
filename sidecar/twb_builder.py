"""Generate a starter .twb / .twbx workbook bound to a *published* datasource.

The workbook references the datasource on the server (class='sqlproxy' +
repository-location), so it carries no local data — a plain .twb zipped into a
.twbx is sufficient. Each sheet spec becomes a worksheet with a Columns/Rows
shelf layout, a mark class, and a <datasource-dependencies> block whose field
names match the requested fields exactly.

Mark types bar/line/text are well supported; map is experimental.

Schema compliance
-----------------
Output is validated against the official TWB XSD (twb_2026.1.0.xsd from
tableau/tableau-document-schemas) in the test suite (test_twb_schema_validation.py).
Key structural requirements imposed by the XSD:

- ``<view>`` must contain ``<datasources>``, ``<datasource-dependencies>``,
  then ``<aggregation value="true"/>`` (required last child of view).
- ``<table>`` sequence: ``<view>``, ``<style/>``, ``<panes>``, ``<rows>``, ``<cols>``.
- ``<worksheet>`` must have ``<simple-id uuid="..."/>`` after ``<table>``.
- ``<windows>`` sequence: worksheets windows with ``<cards/>`` then ``<simple-id>``;
  dashboard windows with ``<viewpoints/>``, ``<active id="1"/>``, ``<simple-id>``.
- Workbook child ordering: ``<worksheets>``, ``<dashboards>``, ``<windows>``,
  then ``<explain-data>`` (required).
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


def _quuid(n: int) -> str:
    """Return a deterministic, schema-valid QUUID string for index *n*.

    Format: ``{XXXXXXXX-0000-0000-0000-000000000000}`` where the first 8 hex
    digits encode the integer *n*.  Determinism is required so the byte-identical
    self-comparison guard (``test_default_none_regression_byte_identical``) keeps
    passing: both sides of the equality produce the same UUIDs.
    """
    hex8 = f"{n:08x}"
    return "{" + f"{hex8}-0000-0000-0000-000000000000" + "}"


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


def _build_worksheet(
    sheet: dict[str, Any],
    ds_caption: str,
    ds_internal: str,
    sheet_index: int,
) -> ET.Element:
    """Build a schema-valid ``<worksheet>`` element.

    XSD-mandated structure (A2, re-baselined for schema-valid output):

    .. code-block:: text

        <worksheet name="...">
          <table>
            <view>
              <datasources>...</datasources>
              <datasource-dependencies>...</datasource-dependencies>
              <aggregation value="true"/>   ← required by XSD
            </view>
            <style/>                        ← required before <panes>/<rows>/<cols>
            <panes>...</panes>
            <rows>...</rows>
            <cols>...</cols>
          </table>
          <simple-id uuid="..."/>           ← required by XSD
        </worksheet>

    Args:
        sheet:        Sheet spec dict.
        ds_caption:   Human-readable datasource caption.
        ds_internal:  Internal datasource name (``sqlproxy.*``).
        sheet_index:  Zero-based index used to derive a deterministic UUID.
    """
    title = str(sheet["title"])
    mark_type = str(sheet.get("mark_type", "bar")).lower()
    cols_dims = [str(c) for c in sheet.get("cols", [])]
    rows_dims = [str(r) for r in sheet.get("rows", [])]
    measures = [str(m) for m in sheet.get("measures", [])]

    worksheet = ET.Element("worksheet", {"name": title})
    table = ET.SubElement(worksheet, "table")

    # --- <view> -------------------------------------------------------
    # Schema sequence: datasources → datasource-dependencies → aggregation
    view = ET.SubElement(table, "view")
    datasources = ET.SubElement(view, "datasources")
    ET.SubElement(
        datasources,
        "datasource",
        {"caption": ds_caption, "name": ds_internal},
    )
    deps = ET.SubElement(view, "datasource-dependencies", {"datasource": ds_internal})
    _add_dependency_columns(deps, cols_dims + rows_dims, measures)
    # <aggregation> is required by the XSD (last mandatory child of <view>)
    ET.SubElement(view, "aggregation", {"value": "true"})

    # --- <style> (required before <rows>/<cols> by XSD) ---------------
    ET.SubElement(table, "style")

    # --- <panes> ------------------------------------------------------
    panes = ET.SubElement(table, "panes")
    pane = ET.SubElement(panes, "pane")
    pane_view = ET.SubElement(pane, "view")
    ET.SubElement(pane_view, "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": _MARK_CLASS.get(mark_type, "Automatic")})

    # For a text/table mark, place the first measure on the Text encoding so it renders.
    ds_ref = f"[{ds_internal}]"
    if mark_type == "text" and measures:
        encodings = ET.SubElement(pane, "encodings")
        ET.SubElement(encodings, "text", {"column": f"{ds_ref}.{_measure_instance(measures[0])}"})

    # --- <rows> / <cols> (after <style> per XSD) ----------------------
    cols_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in cols_dims]
    rows_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in rows_dims]
    rows_exprs += [f"{ds_ref}.{_measure_instance(f)}" for f in measures]
    ET.SubElement(table, "rows").text = " / ".join(rows_exprs)
    ET.SubElement(table, "cols").text = " / ".join(cols_exprs)

    # --- <simple-id> (required by XSD, must follow <table>) -----------
    # Index offset 1 so sheet_index=0 → "00000001-..." (UUID pattern \{[0-9A-Fa-f]{8}-...\})
    ET.SubElement(worksheet, "simple-id", {"uuid": _quuid(sheet_index + 1)})

    return worksheet


def _server_host(server_url: str) -> str:
    if not server_url:
        return ""
    if "://" in server_url:
        return server_url.split("://", 1)[1].split("/", 1)[0]
    return server_url.split("/", 1)[0]


def _tile_zones(titles: list[str], layout: str) -> list[dict[str, int]]:
    """Return (x, y, w, h) quad per worksheet title on the 0–100000 Tableau zone grid.

    The last zone absorbs any rounding remainder so that the sum of heights (vertical)
    or widths (horizontal) equals exactly 100000.

    Args:
        titles:  Ordered list of worksheet titles.
        layout:  ``"tiled_vertical"`` or ``"tiled_horizontal"``.

    Returns:
        A list of dicts with keys ``x``, ``y``, ``w``, ``h`` — one per title.
    """
    n = len(titles)
    if n == 0:
        return []

    GRID = 100000
    quads: list[dict[str, int]] = []

    if layout == "tiled_horizontal":
        unit_w = GRID // n
        for i in range(n):
            w = unit_w if i < n - 1 else GRID - (n - 1) * unit_w
            quads.append({"x": i * unit_w, "y": 0, "w": w, "h": GRID})
    else:  # tiled_vertical (default)
        unit_h = GRID // n
        for i in range(n):
            h = unit_h if i < n - 1 else GRID - (n - 1) * unit_h
            quads.append({"x": 0, "y": i * unit_h, "w": GRID, "h": h})

    return quads


def _build_dashboard(
    name: str,
    layout: str,
    titles: list[str],
    canvas_width: int,
    canvas_height: int,
    dashboard_index: int,
) -> ET.Element:
    """Return the ``<dashboards>`` ET.Element for one dashboard.

    Zone coordinates use the 0–100000 Tableau grid; ``canvas_width``/``canvas_height``
    are the audience-derived pixel dimensions written into ``<size>``.

    Args:
        name:            Dashboard name.
        layout:          Zone layout (``"tiled_vertical"`` or ``"tiled_horizontal"``).
        titles:          Ordered worksheet titles to include.
        canvas_width:    Dashboard pixel width.
        canvas_height:   Dashboard pixel height.
        dashboard_index: Zero-based index for deterministic simple-id UUID.
    """
    dashboards_el = ET.Element("dashboards")
    dashboard = ET.SubElement(dashboards_el, "dashboard", {"name": name})

    ET.SubElement(
        dashboard,
        "size",
        {
            "maxheight": str(canvas_height),
            "maxwidth": str(canvas_width),
            "minheight": str(canvas_height),
            "minwidth": str(canvas_width),
        },
    )

    zones_el = ET.SubElement(dashboard, "zones")
    container = ET.SubElement(
        zones_el,
        "zone",
        {"h": "100000", "id": "1", "type": "layout-basic", "w": "100000", "x": "0", "y": "0"},
    )

    quads = _tile_zones(titles, layout)
    for i, (title, quad) in enumerate(zip(titles, quads, strict=True)):
        ET.SubElement(
            container,
            "zone",
            {
                "h": str(quad["h"]),
                "id": str(i + 2),
                "name": title,
                "type": "worksheet",
                "w": str(quad["w"]),
                "x": str(quad["x"]),
                "y": str(quad["y"]),
            },
        )

    # <simple-id> is required for dashboard elements by the XSD.
    # Offset by 10000 to avoid collision with worksheet UUIDs.
    ET.SubElement(dashboard, "simple-id", {"uuid": _quuid(10000 + dashboard_index + 1)})

    return dashboards_el


def build_twb_xml(
    datasource_name: str,
    datasource_content_url: str,
    site: str,
    sheets: list[dict[str, Any]],
    server_url: str = "",
    dashboards: list[dict[str, Any]] | None = None,
    dashboard_layout: str = "tiled_vertical",
    canvas_width: int = 1000,
    canvas_height: int = 800,
) -> str:
    """Build a schema-valid TWB XML string.

    Workbook child ordering (required by XSD):
    ``<datasources>`` → ``<worksheets>`` → ``<dashboards>`` (if any)
    → ``<windows>`` → ``<explain-data>`` (required).

    When ``dashboards`` is ``None`` (default), the output is byte-identical to
    the pre-feature version with respect to determinism: both calls with no
    ``dashboards`` kwarg and with ``dashboards=None`` produce identical XML.
    """
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

    # --- Worksheets ---------------------------------------------------------
    # re-baselined for schema-valid output (XSD A2)
    worksheets = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets.append(_build_worksheet(sheet, datasource_name, ds_internal, i))

    # --- Optional dashboard block (BEFORE <windows> per XSD) ----------------
    # XSD workbook sequence: Worksheets → Dashboards → Windows → explain-data
    # When ``dashboards`` is None (default), the output is byte-identical to the
    # pre-feature version — existing twb tests continue to pass unchanged.
    if dashboards:
        for db_index, db in enumerate(dashboards):
            db_name = str(db.get("name", "Dashboard 1"))
            db_titles: list[str] = [str(t) for t in db.get("titles", [])]
            if not db_titles:
                db_titles = [str(s["title"]) for s in sheets]
            dashboards_el = _build_dashboard(
                db_name,
                dashboard_layout,
                db_titles,
                canvas_width,
                canvas_height,
                db_index,
            )
            workbook.append(dashboards_el)

    # --- Windows (after dashboards per XSD) ---------------------------------
    # re-baselined for schema-valid output (XSD A2)
    windows = ET.SubElement(workbook, "windows")
    for i, sheet in enumerate(sheets):
        # Worksheet window: requires <cards/> then <simple-id>
        win = ET.SubElement(windows, "window", {"class": "worksheet", "name": str(sheet["title"])})
        ET.SubElement(win, "cards")
        # Window simple-id offset: 20000 + sheet index to avoid collision
        ET.SubElement(win, "simple-id", {"uuid": _quuid(20000 + i + 1)})

    if dashboards:
        for db_index, db in enumerate(dashboards):
            db_name = str(db.get("name", "Dashboard 1"))
            # Dashboard window: requires <viewpoints/>, <active id="1"/>, <simple-id>
            win = ET.SubElement(windows, "window", {"class": "dashboard", "name": db_name})
            ET.SubElement(win, "viewpoints")
            ET.SubElement(win, "active", {"id": "1"})
            # Window simple-id offset: 30000 + dashboard index to avoid collision
            ET.SubElement(win, "simple-id", {"uuid": _quuid(30000 + db_index + 1)})

    # --- explain-data (required by XSD after <windows>) ---------------------
    # re-baselined for schema-valid output (XSD A2)
    explain = ET.SubElement(
        workbook,
        "explain-data",
        {"enabled-for-viewer": "false", "extreme-values-enabled-for-all": "false"},
    )
    ET.SubElement(explain, "explanation-types")

    xml_body = ET.tostring(workbook, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"


def build_starter_twbx(
    datasource_name: str,
    datasource_content_url: str,
    site: str,
    sheets: list[dict[str, Any]],
    out_path: Path,
    server_url: str = "",
    dashboards: list[dict[str, Any]] | None = None,
    dashboard_layout: str = "tiled_vertical",
    canvas_width: int = 1000,
    canvas_height: int = 800,
) -> Path:
    """Build a .twbx (zip containing the generated .twb) for a published datasource.

    When ``dashboards`` is ``None`` (default) the output is identical to the
    pre-feature version.  Pass a non-None list to include a ``<dashboards>``
    block and a dashboard window entry.
    """
    twb_xml = build_twb_xml(
        datasource_name,
        datasource_content_url,
        site,
        sheets,
        server_url=server_url,
        dashboards=dashboards,
        dashboard_layout=dashboard_layout,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.twb", twb_xml)
    return out_path
