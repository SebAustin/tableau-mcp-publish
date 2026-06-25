"""Generate a starter .twb / .twbx workbook.

Two workbook styles are supported:

1. **Published-datasource (sqlproxy)** — ``build_twb_xml`` / ``build_starter_twbx``.
   The workbook references a datasource already published on the server
   (``class='sqlproxy'``), so it carries no local data.  This is the original
   path and remains unchanged.

2. **Embedded-extract (federated/hyper)** — ``build_embedded_twbx``.
   The workbook embeds a ``.hyper`` extract directly (``class='federated'`` →
   named-connection ``class='hyper'``), making it self-contained and directly
   renderable on Tableau Cloud without a separate published datasource binding.
   This matches the structure of all 7 reference workbooks (wb1–wb7) that ship
   with embedded data and render reliably.

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
- Workbook child ordering: ``<datasources>``, ``<worksheets>``, ``<dashboards>``,
  ``<windows>``, then ``<explain-data>`` (required).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from hyper_builder import EXTRACT_SCHEMA, EXTRACT_TABLE, ColumnSpec, read_hyper_columns

TWB_VERSION = "18.1"
SOURCE_BUILD = "2024.1.0"

# Tableau remote-type (ODBC) codes used in federated metadata-records.
# Kept in sync with tds_builder._REMOTE_TYPE — same mapping, duplicated to
# avoid coupling to a private name across modules.
_REMOTE_TYPE: dict[str, str] = {
    "integer": "20",
    "real": "5",
    "string": "129",
    "boolean": "11",
    "date": "133",
    "datetime": "135",
}

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


def _build_worksheet_cards(parent: ET.Element) -> None:
    """Populate ``<cards>`` then ``<viewpoint/>`` for a worksheet window.

    Tableau Cloud's render engine checks for "visual representation" in two
    steps:

    1.  The ``columns``, ``rows``, and ``marks`` shelf cards must be present
        inside ``<cards>`` (an empty ``<cards/>`` causes error 400011).
    2.  A ``<viewpoint/>`` element must follow ``<cards>`` and precede
        ``<simple-id>``.  Every real Tableau workbook (confirmed across 7
        reference workbooks, wb1–wb7) carries this element; its absence is the
        primary cause of error 400011 "has no visual representation" even when
        the cards are correct.

    The XSD ``Window-WorksheetWindow-G`` sequence is:
    ``Cards-G → VisualDoc-G → SimpleIdentifierForThisWindow-G``.
    ``VisualDoc-G`` wraps ``<viewpoint minOccurs="0">``, so the XSD allows
    omitting it, but Tableau Cloud's render engine requires it at runtime.

    Canonical structure (mirrors Tableau Desktop output, confirmed wb5/wb7):

    .. code-block:: xml

        <cards>
          <edge name="left">
            <strip size="160">
              <card type="pages" />
              <card type="filters" />
              <card type="marks" />
            </strip>
          </edge>
          <edge name="top">
            <strip size="2147483647">
              <card type="columns" />
            </strip>
            <strip size="2147483647">
              <card type="rows" />
            </strip>
          </edge>
        </cards>
        <viewpoint />    ← REQUIRED by Tableau Cloud render engine

    The ``size`` attribute on ``<strip>`` is required by the XSD (``Strip-G``
    mandates ``size: xs:int use="required"``).  ``2147483647`` (INT_MAX) is the
    value Tableau Desktop writes for the Columns/Rows shelf strips, and ``160``
    for the left-edge strip.
    """
    cards = ET.SubElement(parent, "cards")

    # Left edge: pages, filters, marks cards
    left = ET.SubElement(cards, "edge", {"name": "left"})
    left_strip = ET.SubElement(left, "strip", {"size": "160"})
    ET.SubElement(left_strip, "card", {"type": "pages"})
    ET.SubElement(left_strip, "card", {"type": "filters"})
    ET.SubElement(left_strip, "card", {"type": "marks"})

    # Top edge: columns shelf, then rows shelf (separate strips per Tableau Desktop output)
    top = ET.SubElement(cards, "edge", {"name": "top"})
    cols_strip = ET.SubElement(top, "strip", {"size": "2147483647"})
    ET.SubElement(cols_strip, "card", {"type": "columns"})
    rows_strip = ET.SubElement(top, "strip", {"size": "2147483647"})
    ET.SubElement(rows_strip, "card", {"type": "rows"})

    # <viewpoint/> is required by Tableau Cloud's render engine to recognise this
    # window as having a visual representation.  It follows <cards> per the XSD
    # Window-WorksheetWindow-G sequence (Cards-G → VisualDoc-G → SimpleIdentifier).
    # All 7 reference workbooks carry this element; its absence is the root cause
    # of error 400011 "has no visual representation".
    ET.SubElement(parent, "viewpoint")


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

    # Real dashboards (wb1–wb7) carry a <style/> child before <size>.
    ET.SubElement(dashboard, "style")

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
    # Outermost canvas zone (type-v2="layout-basic").
    container = ET.SubElement(
        zones_el,
        "zone",
        {"h": "100000", "id": "1", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"},
    )
    # Worksheet zones MUST sit inside a `layout-flow` container, not directly in
    # the layout-basic canvas — verified against every real reference dashboard
    # (e.g. Threshold Analysis). A worksheet zone placed directly in layout-basic
    # makes Tableau Cloud reject the dashboard with 400011 ("sheet has no visual
    # representation"), even though the worksheet renders fine on its own.
    flow_param = "horz" if layout == "tiled_horizontal" else "vert"
    flow = ET.SubElement(
        container,
        "zone",
        {
            "h": "100000",
            "id": "2",
            "param": flow_param,
            "type-v2": "layout-flow",
            "w": "100000",
            "x": "0",
            "y": "0",
        },
    )

    quads = _tile_zones(titles, layout)
    for i, (title, quad) in enumerate(zip(titles, quads, strict=True)):
        # A worksheet zone is identified by `name` ALONE with NO `type`/`type-v2`.
        ws_zone = ET.SubElement(
            flow,
            "zone",
            {
                "h": str(quad["h"]),
                "id": str(i + 3),
                "name": title,
                "w": str(quad["w"]),
                "x": str(quad["x"]),
                "y": str(quad["y"]),
            },
        )
        # Real worksheet zones carry a <layout-cache> child.
        ET.SubElement(
            ws_zone,
            "layout-cache",
            {"cell-count-h": "1", "cell-count-w": "1", "type-h": "cell", "type-w": "cell"},
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
        # Worksheet window: populated <cards> is required by Tableau Cloud's
        # render engine (empty <cards/> causes error 400011 "no visual
        # representation").  See _build_worksheet_cards() for the canonical
        # structure.  XSD requires Cards-G → VisualDoc-G → SimpleIdentifierForThisWindow-G.
        win = ET.SubElement(windows, "window", {"class": "worksheet", "name": str(sheet["title"])})
        _build_worksheet_cards(win)
        # Window simple-id offset: 20000 + sheet index to avoid collision
        ET.SubElement(win, "simple-id", {"uuid": _quuid(20000 + i + 1)})

    if dashboards:
        for db_index, db in enumerate(dashboards):
            db_name = str(db.get("name", "Dashboard 1"))
            db_titles = [str(t) for t in db.get("titles", [])] or [
                str(s["title"]) for s in sheets
            ]
            # Dashboard window: <viewpoints> MUST contain a <viewpoint name="..."/>
            # for EVERY worksheet on the dashboard. An empty <viewpoints/> makes
            # Tableau Cloud reject the dashboard with 400011 ("sheet has no visual
            # representation") — "viewpoint" is Tableau's term for a sheet's visual
            # on a dashboard. Verified against every reference dashboard (wb6/wb7).
            win = ET.SubElement(windows, "window", {"class": "dashboard", "name": db_name})
            viewpoints = ET.SubElement(win, "viewpoints")
            for title in db_titles:
                vp = ET.SubElement(viewpoints, "viewpoint", {"name": title})
                ET.SubElement(vp, "zoom", {"type": "entire-view"})
            # id="-1" is the Tableau standard for "no sheet currently active".
            ET.SubElement(win, "active", {"id": "-1"})
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


# ---------------------------------------------------------------------------
# Embedded-extract (federated / hyper) workbook builder
# ---------------------------------------------------------------------------


def _build_federated_datasource(
    datasource_name: str,
    hyper_filename: str,
    columns: list[ColumnSpec],
) -> ET.Element:
    """Return a ``<datasource>`` ET.Element using a federated hyper connection.

    The structure mirrors what Tableau Desktop emits for an embedded extract
    (confirmed against wb7 reference workbook):

    .. code-block:: xml

        <datasource inline="true" name="federated.{slug}" version="18.1">
          <connection class="federated">
            <named-connections>
              <named-connection caption="{name}" name="hyper.{slug}">
                <connection class="hyper" dbname="Data/{file}.hyper"
                            schema="Extract" tablename="Extract" />
              </named-connection>
            </named-connections>
            <relation connection="hyper.{slug}" name="Extract"
                      table="[Extract].[Extract]" type="table" />
            <metadata-records>
              <metadata-record class="column"> ... </metadata-record>
            </metadata-records>
          </connection>
          <column datatype="..." name="[Field]" role="..." type="..." />
          ...
          <extract count="-1" enabled="true" units="records">
            <connection class="hyper" dbname="Data/{file}.hyper"
                        schema="Extract" tablename="Extract">
              <relation name="Extract" table="[Extract].[Extract]" type="table" />
            </connection>
          </extract>
        </datasource>

    Args:
        datasource_name:  Human-readable name (caption / logical name).
        hyper_filename:   Basename of the .hyper file as stored in the zip
                          (e.g. ``"abc123.hyper"``).
        columns:          Column specs read back from the .hyper file.

    Returns:
        An ET.Element for the ``<datasource>`` node.
    """
    slug = _slug(datasource_name)
    conn_id = f"hyper.{slug}"
    dbname = f"Data/{hyper_filename}"
    relation_table = f"[{EXTRACT_SCHEMA}].[{EXTRACT_TABLE}]"

    ds = ET.Element(
        "datasource",
        {
            "inline": "true",
            "name": f"federated.{slug}",
            "version": TWB_VERSION,
        },
    )

    # --- federated connection block -----------------------------------------
    federated = ET.SubElement(ds, "connection", {"class": "federated"})
    named_conns = ET.SubElement(federated, "named-connections")
    named_conn = ET.SubElement(
        named_conns,
        "named-connection",
        {"caption": datasource_name, "name": conn_id},
    )
    ET.SubElement(
        named_conn,
        "connection",
        {
            "class": "hyper",
            "dbname": dbname,
            "schema": EXTRACT_SCHEMA,
            "tablename": EXTRACT_TABLE,
        },
    )
    ET.SubElement(
        federated,
        "relation",
        {
            "connection": conn_id,
            "name": EXTRACT_TABLE,
            "table": relation_table,
            "type": "table",
        },
    )

    # --- metadata-records (one per column) ----------------------------------
    metadata = ET.SubElement(federated, "metadata-records")
    for ordinal, col in enumerate(columns):
        record = ET.SubElement(metadata, "metadata-record", {"class": "column"})
        ET.SubElement(record, "remote-name").text = col.name
        ET.SubElement(record, "remote-type").text = _REMOTE_TYPE.get(col.datatype, "129")
        ET.SubElement(record, "local-name").text = f"[{col.name}]"
        ET.SubElement(record, "parent-name").text = f"[{EXTRACT_TABLE}]"
        ET.SubElement(record, "remote-alias").text = col.name
        ET.SubElement(record, "ordinal").text = str(ordinal)
        ET.SubElement(record, "local-type").text = col.datatype

    # --- top-level <column> declarations ------------------------------------
    for col in columns:
        ET.SubElement(
            ds,
            "column",
            {
                "datatype": col.datatype,
                "name": f"[{col.name}]",
                "role": col.role,
                "type": col.type,
            },
        )

    # --- <extract> block (marks the datasource as an embedded extract) ------
    # object-id is required by the XSD (xs:string use="required"); empty string
    # is the value Tableau Desktop writes (confirmed against wb7 reference).
    extract_el = ET.SubElement(
        ds,
        "extract",
        {"count": "-1", "enabled": "true", "object-id": "", "units": "records"},
    )
    extract_conn = ET.SubElement(
        extract_el,
        "connection",
        {
            "class": "hyper",
            "dbname": dbname,
            "schema": EXTRACT_SCHEMA,
            "tablename": EXTRACT_TABLE,
        },
    )
    ET.SubElement(
        extract_conn,
        "relation",
        {"name": EXTRACT_TABLE, "table": relation_table, "type": "table"},
    )

    return ds


def build_embedded_twb_xml(
    datasource_name: str,
    hyper_filename: str,
    columns: list[ColumnSpec],
    sheets: list[dict[str, Any]],
    dashboards: list[dict[str, Any]] | None = None,
    dashboard_layout: str = "tiled_vertical",
    canvas_width: int = 1000,
    canvas_height: int = 800,
) -> str:
    """Build a TWB XML string that embeds a .hyper extract via a federated connection.

    The datasource internal name is ``federated.{slug}`` so worksheet field
    references take the form ``[federated.{slug}].[sum:Revenue:qk]`` — matching
    the exact pattern in all 7 reference workbooks (wb1–wb7).

    The workbook carries no ``<repository-location>`` and no ``sqlproxy``
    connection; Tableau Cloud reads the embedded .hyper from the .twbx zip
    and renders the worksheets without any published datasource on the server.

    Args:
        datasource_name:  Human-readable name (caption / logical name).
        hyper_filename:   Basename of the .hyper file stored in the zip
                          (e.g. ``"abc123.hyper"``).
        columns:          Column specs from :func:`hyper_builder.read_hyper_columns`.
        sheets:           Sheet spec dicts (same schema as :func:`build_twb_xml`).
        dashboards:       Optional list of dashboard specs.
        dashboard_layout: Zone tiling direction.
        canvas_width:     Dashboard canvas width in pixels.
        canvas_height:    Dashboard canvas height in pixels.

    Returns:
        A UTF-8 TWB XML string with an XML declaration header.
    """
    slug = _slug(datasource_name)
    ds_internal = f"federated.{slug}"

    workbook = ET.Element(
        "workbook",
        {"source-build": SOURCE_BUILD, "version": TWB_VERSION},
    )

    # --- Datasource (federated / embedded extract) --------------------------
    datasources_el = ET.SubElement(workbook, "datasources")
    ds_el = _build_federated_datasource(datasource_name, hyper_filename, columns)
    datasources_el.append(ds_el)

    # --- Worksheets ---------------------------------------------------------
    worksheets_el = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets_el.append(
            _build_worksheet(sheet, datasource_name, ds_internal, i)
        )

    # --- Optional dashboards ------------------------------------------------
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

    # --- Windows ------------------------------------------------------------
    windows_el = ET.SubElement(workbook, "windows")
    for i, sheet in enumerate(sheets):
        win = ET.SubElement(
            windows_el, "window", {"class": "worksheet", "name": str(sheet["title"])}
        )
        _build_worksheet_cards(win)
        ET.SubElement(win, "simple-id", {"uuid": _quuid(20000 + i + 1)})

    if dashboards:
        for db_index, db in enumerate(dashboards):
            db_name = str(db.get("name", "Dashboard 1"))
            db_titles = [str(t) for t in db.get("titles", [])] or [
                str(s["title"]) for s in sheets
            ]
            # <viewpoints> MUST contain a <viewpoint name="..."/> for EVERY
            # worksheet on the dashboard — an empty <viewpoints/> makes Tableau
            # Cloud reject the dashboard with 400011 ("sheet has no visual
            # representation"). Verified against reference dashboards (wb6/wb7).
            win = ET.SubElement(windows_el, "window", {"class": "dashboard", "name": db_name})
            viewpoints = ET.SubElement(win, "viewpoints")
            for title in db_titles:
                vp = ET.SubElement(viewpoints, "viewpoint", {"name": title})
                ET.SubElement(vp, "zoom", {"type": "entire-view"})
            ET.SubElement(win, "active", {"id": "-1"})
            ET.SubElement(win, "simple-id", {"uuid": _quuid(30000 + db_index + 1)})

    # --- explain-data (required by XSD) ------------------------------------
    explain = ET.SubElement(
        workbook,
        "explain-data",
        {"enabled-for-viewer": "false", "extreme-values-enabled-for-all": "false"},
    )
    ET.SubElement(explain, "explanation-types")

    xml_body = ET.tostring(workbook, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"


def build_embedded_twbx(
    datasource_name: str,
    hyper_path: Path,
    sheets: list[dict[str, Any]],
    out_path: Path,
    dashboards: list[dict[str, Any]] | None = None,
    dashboard_layout: str = "tiled_vertical",
    canvas_width: int = 1000,
    canvas_height: int = 800,
) -> Path:
    """Build a self-contained .twbx that embeds the .hyper extract.

    The zip archive contains:
    - ``{slug}.twb``              — the workbook XML (federated connection)
    - ``Data/{hyper_filename}``   — the .hyper extract Tableau Cloud reads

    This is the structure all reference workbooks (wb1–wb7) use.  The workbook
    does NOT reference any published datasource on the server, so it renders
    without a prior ``publish_datasource`` call.

    Args:
        datasource_name:  Human-readable name (caption / logical name).
        hyper_path:       Absolute path to the .hyper extract on disk.
                          Validated: must exist and be a file.
        sheets:           Sheet spec dicts.
        out_path:         Destination .twbx path.
        dashboards:       Optional list of dashboard specs.
        dashboard_layout: Zone tiling direction.
        canvas_width:     Dashboard canvas width in pixels.
        canvas_height:    Dashboard canvas height in pixels.

    Returns:
        The resolved ``out_path``.

    Raises:
        FileNotFoundError: If ``hyper_path`` does not exist.
    """
    if not hyper_path.is_file():
        raise FileNotFoundError(
            f"hyper extract not found: {hyper_path!r}. "
            "Build the extract first via buildDatasourceFromFile."
        )

    columns = read_hyper_columns(hyper_path)
    hyper_filename = hyper_path.name

    twb_xml = build_embedded_twb_xml(
        datasource_name=datasource_name,
        hyper_filename=hyper_filename,
        columns=columns,
        sheets=sheets,
        dashboards=dashboards,
        dashboard_layout=dashboard_layout,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.twb", twb_xml)
        archive.write(hyper_path, arcname=f"Data/{hyper_filename}")

    return out_path
