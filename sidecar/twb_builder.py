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

Stories (Phase E4)
------------------
Both ``build_twb_xml`` and ``build_embedded_twb_xml`` accept an optional
``stories`` param: a list of ``{name, nav_type?, points: [{caption,
captured_sheet}]}`` dicts. Each story is emitted as a ``<dashboard
type='storyboard'>`` (see ``_build_story``) — a peer of regular dashboards
inside the SAME shared ``<dashboards>`` container, appended after every
regular dashboard. Every ``captured_sheet`` must name an existing worksheet or
regular-dashboard in the workbook; an unknown reference raises ``ValueError``
loudly rather than shipping a broken story.

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
  dashboard windows with ``<viewpoints/>``, ``<active id="-1"/>``, ``<simple-id>``.
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

_MARK_CLASS = {
    "bar": "Bar",
    "line": "Line",
    "text": "Text",
    "map": "Map",
    "scatter": "Circle",
    "map_filled": "Automatic",
}

# Semantic-role strings for Tableau geo columns.  Mirrors wb1 ~2804 and wb1 ~542-560.
_GEO_SEMANTIC_ROLE: dict[str, str] = {
    "state": "[State].[Name]",
    "country": "[Country].[ISO3166_2]",
    "city": "[City].[Name]",
    "zipcode": "[ZipCode].[Name]",
}

# ---------------------------------------------------------------------------
# Brand application helpers (Phase E1, Slice B — "Builder applies branding")
#
# Every function here is a pure, independently-testable helper.  Every call
# site that consumes them is guarded by ``if brand:`` (or equivalent) so that
# a request with NO brand block produces byte-identical output to before this
# slice — the existing determinism guards
# (``test_default_path_byte_identical_with_and_without_extra_keys`` et al.)
# stay green untouched.
# ---------------------------------------------------------------------------

_HEX_SHORT_RE = re.compile(r"^#([0-9A-Fa-f])([0-9A-Fa-f])([0-9A-Fa-f])$")


def _normalize_hex_color(color: str) -> str:
    """Expand a ``#rgb`` shorthand to ``#rrggbb``; pass through longer forms.

    The TWB XSD's ``ColorObject-ST`` (used by ``<color>``, ``<run
    fontcolor=...>``, etc.) only accepts 6 or 8 hex digits
    (``#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{8}``). ``brand.yaml``'s ``HexColorSchema``
    additionally allows the 3-digit CSS shorthand (e.g. ``#5af``), so every
    color read from a brand block is normalised through this helper before
    being written into workbook XML.
    """
    match = _HEX_SHORT_RE.match(color)
    if match:
        r, g, b = match.groups()
        return f"#{r}{r}{g}{g}{b}{b}"
    return color


# Word-level (not substring) hints for classify_measure_format — see its
# docstring for why word-splitting is used instead of naive substring search.
_CURRENCY_MEASURE_HINTS = frozenset({"sales", "revenue", "profit", "price", "cost", "amount"})
_PERCENT_MEASURE_HINTS = frozenset({"ratio", "percent", "rate", "discount"})

_DEFAULT_CURRENCY_FORMAT = "$#,##0"
_DEFAULT_PERCENT_FORMAT = "0.0%"
_DEFAULT_NUMBER_FORMAT = "#,##0"


def classify_measure_format(field_name: str, formats: dict[str, Any]) -> str:
    """Return the ``default-format`` value for a measure column, by name heuristics.

    Mirrors the ``default-format`` attribute seen on reference-workbook
    ``<column>`` elements (e.g. ``default-format='p0.00%'`` on a percent
    parameter, ``default-format='n#,##0;-#,##0'`` on a currency measure) —
    the *grammar* of those format strings is opaque to us (Tableau-internal),
    so we pass through whatever the brand file's ``formats.currency`` /
    ``formats.percent`` / ``formats.number`` strings already contain rather
    than inventing new format-string syntax.

    Classification is by whole-word match against the field name (split on
    non-letter characters, lower-cased) — NOT substring match — so a field
    like "Corporate Sales" is not misclassified as percent just because
    "Corporate" contains the substring "rate". Percent-like hints are checked
    before currency-like hints so an ambiguous name like "Profit Margin"
    (which has no currency hint word anyway) or "Discount" (a 0-1 ratio, not
    a dollar amount) resolves to percent.

    Args:
        field_name: The measure's field name (e.g. "Sales", "Discount").
        formats:    A ``formats`` dict with optional ``currency``/``percent``/
                    ``number`` keys (snake_case-agnostic — all three are
                    single words). Missing keys fall back to the same
                    defaults as ``branding/schema.ts``'s ``FormatsSchema``.

    Returns:
        The format string to stamp on ``default-format``.
    """
    words = set(re.findall(r"[a-z]+", field_name.lower()))
    if words & _PERCENT_MEASURE_HINTS:
        return str(formats.get("percent") or _DEFAULT_PERCENT_FORMAT)
    if words & _CURRENCY_MEASURE_HINTS:
        return str(formats.get("currency") or _DEFAULT_CURRENCY_FORMAT)
    return str(formats.get("number") or _DEFAULT_NUMBER_FORMAT)


def _build_preferences_element(brand: dict[str, Any]) -> ET.Element:
    """Return the workbook-level ``<preferences><color-palette>`` element.

    Mirrors "Visualize Quota Attainment for Executives in Multiple Ways" ~26-41::

        <preferences>
          <color-palette custom='true' name='Barkbus Secondary Light' type='regular'>
            <color>#84c8b4</color>
            ...
          </color-palette>
        </preferences>

    Placed as the FIRST child of ``<workbook>`` (before ``<datasources>``),
    matching the reference's position and the XSD's ``WorkbookFile-CT``
    sequence (``Workbook-Preferences-G`` precedes ``Workbook-DataSources-G``).
    Only called when a brand block is present — see module-level note.
    """
    palette = brand.get("palette") or {}
    brand_name = str(brand.get("brand_name") or "Brand")
    categorical = [str(c) for c in (palette.get("categorical") or [])]

    preferences_el = ET.Element("preferences")
    palette_el = ET.SubElement(
        preferences_el,
        "color-palette",
        {"custom": "true", "name": f"{brand_name} Palette", "type": "regular"},
    )
    for color in categorical:
        ET.SubElement(palette_el, "color").text = _normalize_hex_color(color)
    return preferences_el


def _resolve_font_spec(brand: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    """Return ``brand['typography'][kind]`` (e.g. ``"title"``/``"body"``), or ``None``.

    ``None`` is returned both when ``brand`` itself is absent and when the
    ``typography`` section omits ``kind`` — callers use this to fall back to
    the pre-brand hardcoded defaults.
    """
    if not brand:
        return None
    typography = brand.get("typography") or {}
    spec = typography.get(kind)
    return spec if spec else None


def _title_or_subtitle_run_attrs(
    font_spec: dict[str, Any] | None,
    *,
    default_bold: bool,
    default_fontsize: int,
) -> tuple[bool, int, str | None, str | None]:
    """Return ``(bold, fontsize, fontcolor, fontname)`` for a title/subtitle run.

    No brand (``font_spec is None``): reproduces the pre-brand hardcoded
    values exactly — ``bold=True, fontsize=20`` for the title (mirrors
    "Threshold Analysis Two Way" ~1664: ``<run bold='true' fontsize='20'>``)
    and ``bold=False, fontsize=14`` for the subtitle — with no fontcolor/
    fontname attribute (byte-identical determinism guard).

    Brand present: mirrors "Visualize Quota Attainment..." ~4478/~4488 —
    ``<run fontcolor='#0088ff' fontname='Tableau Bold' fontsize='36'>`` — a
    named Bold font carries the boldness instead of a ``bold`` attribute, so
    ``bold`` is always ``False`` on this path.
    """
    if font_spec is None:
        return default_bold, default_fontsize, None, None
    fontsize = int(font_spec.get("size") or default_fontsize)
    fontcolor = font_spec.get("color")
    fontcolor = _normalize_hex_color(str(fontcolor)) if fontcolor else None
    fontname = font_spec.get("font")
    fontname = str(fontname) if fontname else None
    return False, fontsize, fontcolor, fontname


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


def _color_column_instance(ds_internal: str, color: dict[str, Any]) -> str:
    """Return the fully-qualified column reference for a color encoding.

    Mirrors wb1 ~3538-3540 and ~3776 for three ``kind`` variants:

    - ``"measure_names"`` → ``[:Measure Names]`` (Tableau virtual field)
    - ``"dimension"``     → ``_dim_instance(field)``
    - ``"measure"``       → ``_measure_instance(field)``
    """
    field = str(color["field"])
    kind = str(color.get("kind", "dimension"))
    ds_ref = f"[{ds_internal}]"
    if kind == "measure_names":
        instance = "[:Measure Names]"
    elif kind == "measure":
        instance = _measure_instance(field)
    else:  # "dimension"
        instance = _dim_instance(field)
    return f"{ds_ref}.{instance}"


def _build_worksheet(
    sheet: dict[str, Any],
    ds_caption: str,
    ds_internal: str,
    sheet_index: int,
) -> ET.Element:  # noqa: C901 – intentionally long: one function per worksheet type
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

    Rich encodings (Slice 3A)
    -------------------------
    color
        When ``sheet["color"]`` is present (``{field, kind}``), a
        ``<color column='...'>`` element is emitted inside ``<encodings>``
        in the pane.  Mirrors wb1 ~3538-3540.

    scatter
        When ``mark_type == "scatter"`` (or ``sheet["scatter"]`` is present),
        the mark class is ``Circle``; ``scatter.x`` is placed on ``<cols>``
        and ``scatter.y`` on ``<rows>`` via ``_measure_instance``.  Optional
        ``scatter.breakdown`` dimension is added as a color encoding.
        Mirrors wb1 ~3815.

    kpi_tile
        When ``sheet["kind"] == "kpi_tile"``, a ``Text`` mark (class
        ``Automatic`` per wb1 KPI sheets) is emitted with one ``<text>``
        encoding per measure listed in ``kpi.primary_measure`` (required),
        ``kpi.comparison_measure`` (optional), and ``kpi.delta_measure``
        (optional).  Mirrors wb1 ~3388-3398.

    map_filled (choropleth) — Slice 3C
        When ``mark_type == "map_filled"`` or ``sheet["geo"]`` is present:

        - ``<mapsources><mapsource name='Tableau'/></mapsources>`` is added
          to ``<view>`` before ``<datasource-dependencies>`` (mirrors wb1
          ~4614-4616 "Sales Distribution by State").
        - ``<rows>`` carries ``[{ds_internal}].[Latitude (generated)]``
          and ``<cols>`` carries ``[{ds_internal}].[Longitude (generated)]``
          — these are Tableau-generated virtual columns that appear on every
          map worksheet (wb1 ~4850-4851); they are NOT column-instance
          expressions and carry no prefix (``none:``/``sum:``).
        - Two ``<pane>`` elements are emitted (mirrors wb1 ~4786-4821):

          *Pane id='0'* (filled polygon layer):
            - ``<mark class='Automatic' />``
            - ``<encodings><lod .../><color .../><geometry .../></encodings>``
              where ``lod`` and ``color`` reference the geo dimension
              instance and the color-measure instance respectively, and
              ``geometry`` references ``[{ds_internal}].[Geometry
              (generated)]``.

          *Pane id='1'* (LOD shadow layer):
            - ``<mark class='Automatic' />``
            - ``<encodings><lod column='...geo dim instance...'/></encodings>``

        - The geo dimension and color measure are added to
          ``<datasource-dependencies>``.

    Args:
        sheet:        Sheet spec dict.
        ds_caption:   Human-readable datasource caption.
        ds_internal:  Internal datasource name (``sqlproxy.*``).
        sheet_index:  Zero-based index used to derive a deterministic UUID.
    """
    title = str(sheet["title"])
    mark_type = str(sheet.get("mark_type", "bar")).lower()
    sheet_kind = str(sheet.get("kind", "chart"))
    cols_dims = [str(c) for c in sheet.get("cols", [])]
    rows_dims = [str(r) for r in sheet.get("rows", [])]
    measures = [str(m) for m in sheet.get("measures", [])]

    # --- Scatter spec ------------------------------------------------------
    scatter = sheet.get("scatter")  # optional {x, y, breakdown?}
    is_scatter = mark_type == "scatter" or scatter is not None

    # --- Color spec --------------------------------------------------------
    color_spec = sheet.get("color")  # optional {field, kind}

    # --- KPI tile spec -----------------------------------------------------
    kpi_spec = sheet.get("kpi")  # optional {primary_measure, comparison_measure?, delta_measure?}
    is_kpi_tile = sheet_kind == "kpi_tile"

    # --- Filled map (choropleth) spec --------------------------------------
    geo_spec = sheet.get("geo")  # optional {geo_field, geo_role, color_measure?}
    is_map_filled = mark_type == "map_filled" or geo_spec is not None

    worksheet = ET.Element("worksheet", {"name": title})
    table = ET.SubElement(worksheet, "table")

    # --- <view> -------------------------------------------------------
    # Schema sequence: datasources → (mapsources?) → datasource-dependencies → aggregation
    view = ET.SubElement(table, "view")
    datasources_el = ET.SubElement(view, "datasources")
    ET.SubElement(
        datasources_el,
        "datasource",
        {"caption": ds_caption, "name": ds_internal},
    )

    # Filled map requires <mapsources> in <view> (mirrors wb1 ~4614-4616).
    if is_map_filled:
        mapsources_el = ET.SubElement(view, "mapsources")
        ET.SubElement(mapsources_el, "mapsource", {"name": "Tableau"})

    deps = ET.SubElement(view, "datasource-dependencies", {"datasource": ds_internal})

    # Collect dimension and measure fields for dependency declarations.
    # Scatter: x/y measures on their respective shelves; breakdown (if any) as dimension.
    dep_dims = list(cols_dims) + list(rows_dims)
    dep_measures = list(measures)

    if is_scatter and scatter:
        scatter_x = str(scatter["x"])
        scatter_y = str(scatter["y"])
        scatter_breakdown = scatter.get("breakdown")
        if scatter_x not in dep_measures:
            dep_measures.append(scatter_x)
        if scatter_y not in dep_measures:
            dep_measures.append(scatter_y)
        if scatter_breakdown and str(scatter_breakdown) not in dep_dims:
            dep_dims.append(str(scatter_breakdown))
    else:
        scatter_x = ""
        scatter_y = ""
        scatter_breakdown = None

    # Color field must appear in dependency declarations so Tableau resolves it.
    if color_spec:
        color_field = str(color_spec["field"])
        color_kind = str(color_spec.get("kind", "dimension"))
        if color_kind == "measure" and color_field not in dep_measures:
            dep_measures.append(color_field)
        elif color_kind == "dimension" and color_field not in dep_dims:
            dep_dims.append(color_field)
        # measure_names is a Tableau virtual field — no column declaration needed

    # KPI tile: all measures go into dependency declarations.
    if is_kpi_tile and kpi_spec:
        for kpi_field_key in ("primary_measure", "comparison_measure", "delta_measure"):
            kpi_field = kpi_spec.get(kpi_field_key)
            if kpi_field and str(kpi_field) not in dep_measures:
                dep_measures.append(str(kpi_field))

    # Filled map: geo dimension + color measure go into dependency declarations.
    if is_map_filled and geo_spec:
        geo_field = str(geo_spec["geo_field"])
        geo_color = geo_spec.get("color_measure")
        if geo_field not in dep_dims:
            dep_dims.append(geo_field)
        if geo_color and str(geo_color) not in dep_measures:
            dep_measures.append(str(geo_color))

    _add_dependency_columns(deps, dep_dims, dep_measures)
    # <aggregation> is required by the XSD (last mandatory child of <view>)
    ET.SubElement(view, "aggregation", {"value": "true"})

    # --- <style> (required before <rows>/<cols> by XSD) ---------------
    ET.SubElement(table, "style")

    # --- <panes> ------------------------------------------------------
    panes = ET.SubElement(table, "panes")
    ds_ref = f"[{ds_internal}]"

    if is_map_filled and geo_spec:
        # Filled-map (choropleth) pane structure — mirrors wb1 ~4786-4848:
        #
        #   <pane id='1' ...>           ← LOD-only shadow layer
        #     <view><breakdown/></view>
        #     <mark class='Automatic'/>
        #     <encodings><lod column='...geo dim instance...'/></encodings>
        #   </pane>
        #   <pane generated-title='...' id='0' ...>  ← filled polygon layer
        #     <view><breakdown/></view>
        #     <mark class='Automatic'/>
        #     <encodings>
        #       <lod    column='...geo dim instance...'/>
        #       <color  column='...color measure instance...'/>   (if colorMeasure)
        #       <geometry column='[ds].[Geometry (generated)]'/>
        #     </encodings>
        #   </pane>
        #
        # NOTE: wb1 emits pane id='1' before id='0', then a third pane id='2'
        # for a Shape overlay.  We emit only the two core panes (id='1' and id='0')
        # — the XSD does not mandate the overlay pane and omitting it keeps the
        # structure minimal.
        geo_field_name = str(geo_spec["geo_field"])
        geo_dim_col = f"{ds_ref}.{_dim_instance(geo_field_name)}"
        geo_color_measure = geo_spec.get("color_measure")
        geometry_col = f"{ds_ref}.[Geometry (generated)]"

        # Pane id='1': LOD shadow (wb1 ~4786-4795)
        pane_lod = ET.SubElement(
            panes,
            "pane",
            {"id": "1", "selection-relaxation-option": "selection-relaxation-allow"},
        )
        pane_lod_view = ET.SubElement(pane_lod, "view")
        ET.SubElement(pane_lod_view, "breakdown", {"value": "auto"})
        ET.SubElement(pane_lod, "mark", {"class": "Automatic"})
        enc_lod = ET.SubElement(pane_lod, "encodings")
        ET.SubElement(enc_lod, "lod", {"column": geo_dim_col})

        # Pane id='0': filled polygon layer (wb1 ~4796-4821)
        pane_fill = ET.SubElement(
            panes,
            "pane",
            {
                "generated-title": geo_field_name,
                "id": "0",
                "selection-relaxation-option": "selection-relaxation-allow",
            },
        )
        pane_fill_view = ET.SubElement(pane_fill, "view")
        ET.SubElement(pane_fill_view, "breakdown", {"value": "auto"})
        ET.SubElement(pane_fill, "mark", {"class": "Automatic"})
        enc_fill = ET.SubElement(pane_fill, "encodings")
        ET.SubElement(enc_fill, "lod", {"column": geo_dim_col})
        if geo_color_measure:
            color_col = f"{ds_ref}.{_measure_instance(str(geo_color_measure))}"
            ET.SubElement(enc_fill, "color", {"column": color_col})
        ET.SubElement(enc_fill, "geometry", {"column": geometry_col})

    else:
        pane = ET.SubElement(panes, "pane")
        pane_view = ET.SubElement(pane, "view")
        ET.SubElement(pane_view, "breakdown", {"value": "auto"})

        # Determine mark class.
        # KPI tiles use "Automatic" (mirrors wb1 Sales KPI / Customer KPI sheets
        # at lines ~4946, ~3392 which have <mark class='Automatic'/>).
        # Scatter maps to "Circle" via _MARK_CLASS.
        if is_kpi_tile:
            mark_class = "Automatic"
        elif is_scatter:
            mark_class = "Circle"
        else:
            mark_class = _MARK_CLASS.get(mark_type, "Automatic")
        ET.SubElement(pane, "mark", {"class": mark_class})

        # --- Encodings -------------------------------------------------
        # Collect all encoding elements to decide whether to emit <encodings>.
        # Order: color first, then text entries (mirrors wb1 Pie pane at ~3775-3780).
        encoding_elements: list[tuple[str, str]] = []  # (tag, column_value)

        # Color encoding (G-01)
        # Emitted for: explicit color_spec, or scatter breakdown as color.
        effective_color_spec = color_spec
        if is_scatter and scatter_breakdown and not effective_color_spec:
            effective_color_spec = {"field": str(scatter_breakdown), "kind": "dimension"}

        if effective_color_spec:
            col_val = _color_column_instance(ds_internal, effective_color_spec)
            encoding_elements.append(("color", col_val))

        # Text encoding(s)
        if is_kpi_tile and kpi_spec:
            # Multi-measure text: primary, comparison, delta (mirrors wb1 ~3394-3397)
            for kpi_field_key in ("primary_measure", "comparison_measure", "delta_measure"):
                kpi_field = kpi_spec.get(kpi_field_key)
                if kpi_field:
                    encoding_elements.append(
                        ("text", f"{ds_ref}.{_measure_instance(str(kpi_field))}")
                    )
        elif mark_type == "text" and measures and not is_kpi_tile:
            # Plain text mark: single-measure text encoding (original behaviour)
            encoding_elements.append(("text", f"{ds_ref}.{_measure_instance(measures[0])}"))

        if encoding_elements:
            encodings_el = ET.SubElement(pane, "encodings")
            for tag, col_val in encoding_elements:
                ET.SubElement(encodings_el, tag, {"column": col_val})

    # --- <rows> / <cols> (after <style> per XSD) ----------------------
    if is_map_filled:
        # Filled map: generated lat/long on rows/cols (mirrors wb1 ~4850-4851).
        # These are Tableau virtual columns — NOT column-instance expressions;
        # they carry no "none:" or "sum:" prefix and brackets are NOT doubled.
        rows_exprs = [f"{ds_ref}.[Latitude (generated)]"]
        cols_exprs = [f"{ds_ref}.[Longitude (generated)]"]
    elif is_scatter and scatter:
        # Scatter: x measure on cols, y measure on rows (mirrors wb1 ~3848-3849
        # which show a dual-measure axis expression on cols/rows).
        # For a simple scatter: single x measure on cols, single y measure on rows.
        cols_exprs = [f"{ds_ref}.{_measure_instance(scatter_x)}"]
        rows_exprs = [f"{ds_ref}.{_measure_instance(scatter_y)}"]
        # If there are explicit dim shelves too, prepend them.
        for f in cols_dims:
            expr = f"{ds_ref}.{_dim_instance(f)}"
            if expr not in cols_exprs:
                cols_exprs.insert(0, expr)
        for f in rows_dims:
            expr = f"{ds_ref}.{_dim_instance(f)}"
            if expr not in rows_exprs:
                rows_exprs.insert(0, expr)
    else:
        cols_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in cols_dims]
        rows_exprs = [f"{ds_ref}.{_dim_instance(f)}" for f in rows_dims]
        if not is_kpi_tile:
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


def _build_text_zone(
    text: str,
    zone_id: int,
    bold: bool = False,
    fontsize: int = 14,
    fontcolor: str | None = None,
    fontname: str | None = None,
    h: int = 5000,
) -> ET.Element:
    """Return a ``<zone type-v2='text'>`` element carrying ``<formatted-text><run>``.

    Mirrors wb6 ~1664 (title: bold=true, fontsize=20) and wb7 ~4476–4487
    (title/subtitle with fontcolor and fontname).  The ``forceUpdate='true'``
    attribute is written by Tableau Desktop on text zones; it is allowed via the
    XSD's ``anyAttribute namespace='##local'`` clause.

    ``<run>`` attributes are inserted in alphabetical order
    (``bold``, ``fontcolor``, ``fontname``, ``fontsize``) — matching the
    attribute order Tableau Desktop itself writes in both reference cases
    above (``bold`` + ``fontsize``; ``fontcolor`` + ``fontname`` + ``fontsize``).

    Args:
        text:       The text to display.
        zone_id:    Deterministic zone id (must be unique across the dashboard).
        bold:       Emit ``bold='true'`` on the ``<run>`` (mirrors wb6 title).
        fontsize:   Font size in points (unsigned int required by XSD).
        fontcolor:  Optional hex color string (e.g. ``"#0088ff"``).
        fontname:   Optional font family name (e.g. ``"Tableau Bold"``).
        h:          Zone height on the 0–100000 grid.  Default 5000 (~5 %).

    Returns:
        A ``<zone>`` ET.Element with ``type-v2='text'``.
    """
    zone = ET.Element(
        "zone",
        {
            "forceUpdate": "true",
            "h": str(h),
            "id": str(zone_id),
            "type-v2": "text",
            "w": "100000",
            "x": "0",
            "y": "0",
        },
    )
    ft = ET.SubElement(zone, "formatted-text")
    run_attrs: dict[str, str] = {}
    if bold:
        run_attrs["bold"] = "true"
    if fontcolor:
        run_attrs["fontcolor"] = fontcolor
    if fontname:
        run_attrs["fontname"] = fontname
    run_attrs["fontsize"] = str(fontsize)
    run_el = ET.SubElement(ft, "run", run_attrs)
    run_el.text = text
    return zone


def _append_zone_style(zone: ET.Element, formats: dict[str, str]) -> None:
    """Append a ``<zone-style>`` as the LAST child of *zone*, if *formats* is non-empty.

    Design Excellence, Slice D2. Mirrors the mined vocabulary in
    ``design/corpus/recipes/zone_styles.yaml``: plain
    ``<format attr='...' value='...'/>`` children only — NEVER the reference
    workbooks' XSD-illegal ``_.fcp.DashboardRoundedCorners...`` *element* form
    for corner radii (confirmed against the XSD: ``corner-radius`` is a legal
    plain ``StyleAttribute-ST`` enum value, so the plain ``<format>`` form is
    both schema-valid and the only form this builder ever emits).

    The XSD's ``Zone-ZoneStyle-G`` group places ``zone-style`` LAST in a
    zone's content model. Callers MUST only invoke this helper after every
    other child of *zone* has already been appended.

    ``formats`` keys are emitted in sorted (alphabetical) order so repeated
    builds of the same theme produce byte-identical XML. An empty/falsy
    ``formats`` is a no-op — no empty ``<zone-style/>`` is ever emitted (a
    themed build must never add a construct with nothing in it).

    Args:
        zone:    The ``<zone>`` element to append ``<zone-style>`` to.
        formats: Mapping of ``StyleAttribute-ST`` attr name (e.g.
                 ``"background-color"``) to its string value.
    """
    if not formats:
        return
    zone_style = ET.SubElement(zone, "zone-style")
    for attr in sorted(formats):
        ET.SubElement(zone_style, "format", {"attr": attr, "value": formats[attr]})


def _chart_card_zone_style_formats(
    chart_card: dict[str, Any] | None, gutter: int | None
) -> dict[str, str]:
    """Map ``design_theme["chart_card"]`` onto the mined zone-style vocabulary.

    Design Excellence, Slice D2. ``chart_card`` is
    ``ThemeChartCardModel.model_dump()`` (snake_case — see
    ``server.ThemeChartCardModel``): ``background`` -> ``background-color``,
    ``border.{color,style,width}`` -> ``border-{color,style,width}``,
    ``padding`` -> ``padding``, ``margin`` -> ``margin``, ``corner_radius`` ->
    ``corner-radius``. Unset keys are omitted entirely — never written as an
    empty-string or zero placeholder.

    ``gutter`` (``design_theme["spacing"]["gutter"]``, Slice D2 PLAN.md)
    supplies the zone's ``margin`` only when ``chart_card`` does not set one
    of its own; an explicit ``chart_card["margin"]`` always wins over the
    gutter.

    Args:
        chart_card: ``design_theme["chart_card"]`` dict, or ``None``.
        gutter:     ``design_theme["spacing"]["gutter"]``, or ``None``.

    Returns:
        A ``{StyleAttribute-ST attr: value}`` dict (may be empty).
    """
    formats: dict[str, str] = {}
    card = chart_card or {}

    background = card.get("background")
    if background:
        formats["background-color"] = str(background)

    border = card.get("border") or {}
    if border.get("color"):
        formats["border-color"] = str(border["color"])
    if border.get("style"):
        formats["border-style"] = str(border["style"])
    if border.get("width") is not None:
        formats["border-width"] = str(border["width"])

    if card.get("padding") is not None:
        formats["padding"] = str(card["padding"])

    if card.get("margin") is not None:
        formats["margin"] = str(card["margin"])
    elif gutter is not None:
        formats["margin"] = str(gutter)

    if card.get("corner_radius") is not None:
        formats["corner-radius"] = str(card["corner_radius"])

    return formats


def _append_worksheet_zones(
    parent: ET.Element,
    titles: list[str],
    id_start: int,
    zone_style_formats: dict[str, str] | None = None,
) -> None:
    """Append equal-sized horizontal worksheet zones to *parent*.

    Each zone is identified by ``name`` alone with NO ``type``/``type-v2``
    (the DB-1 invariant), and carries a ``<layout-cache>`` child.

    Args:
        parent:             The ``<zone type-v2='layout-flow'>`` container to
                             append into.
        titles:              Ordered worksheet titles.
        id_start:            First zone id to use; ids are allocated sequentially.
        zone_style_formats:  Optional zone-style format dict (Slice D2) applied
                              to EVERY zone appended here, as the LAST child
                              (after ``<layout-cache>``). ``None``/empty (the
                              default): no ``<zone-style>`` is emitted — callers
                              that style KPI-tile bands must omit this (KPI
                              tiles are not themed until Slice D4).
    """
    n = len(titles)
    if n == 0:
        return
    GRID = 100000
    unit_w = GRID // n
    for i, title in enumerate(titles):
        w = unit_w if i < n - 1 else GRID - (n - 1) * unit_w
        ws_zone = ET.SubElement(
            parent,
            "zone",
            {
                "h": "100000",
                "id": str(id_start + i),
                "name": title,
                "w": str(w),
                "x": str(i * unit_w),
                "y": "0",
            },
        )
        ET.SubElement(
            ws_zone,
            "layout-cache",
            {"cell-count-h": "1", "cell-count-w": "1", "type-h": "cell", "type-w": "cell"},
        )
        if zone_style_formats:
            _append_zone_style(ws_zone, zone_style_formats)


def _build_dashboard(
    name: str,
    layout: str,
    titles: list[str],
    canvas_width: int,
    canvas_height: int,
    dashboard_index: int,
    title: str | None = None,
    subtitle: str | None = None,
    text_zones: list[dict[str, str]] | None = None,
    layout_grammar: dict[str, Any] | None = None,
    brand: dict[str, Any] | None = None,
    design_theme: dict[str, Any] | None = None,
) -> ET.Element:
    """Return the ``<dashboard>`` ET.Element for one dashboard.

    NOTE: this returns the bare ``<dashboard>`` element, NOT a ``<dashboards>``
    wrapper. The XSD's ``Workbook-Dashboards-G`` allows exactly ONE
    ``<dashboards>`` element per workbook (holding ``maxOccurs="unbounded"``
    ``<dashboard>`` children); callers append this return value into a single,
    shared ``<dashboards>`` container alongside any other regular dashboards
    AND any stories (also ``<dashboard type='storyboard'>`` elements — Phase
    E4, see :func:`_build_story`) — see ``build_twb_xml``'s dashboard-building
    block.

    Zone coordinates use the 0–100000 Tableau grid; ``canvas_width``/``canvas_height``
    are the audience-derived pixel dimensions written into ``<size>``.

    Default path (no title/grammar)
    --------------------------------
    When ``title``, ``subtitle``, ``text_zones``, and ``layout_grammar`` are all
    absent/empty, the output is byte-identical to the pre-3B version.  The
    existing ``test_default_none_regression_byte_identical`` guard proves this.

    Title / subtitle / text zones (Slice 3B, DL-1/DL-2)
    -----------------------------------------------------
    When ``title`` is provided a ``<zone type-v2='text'>`` is emitted at the top
    of the outer layout-flow, carrying::

        <formatted-text>
          <run bold="true" fontsize="20">{title}</run>
        </formatted-text>

    This mirrors wb6 ~1664 and wb7 ~4476 exactly.  An optional ``subtitle`` adds
    a second text zone with a smaller font (fontsize="14").  Extra ``text_zones``
    with ``position="header"`` are inserted before the chart flow; those with
    ``position="footer"`` are appended after.

    KPI-band-over-charts layout (Slice 3B, DL-3)
    ---------------------------------------------
    When ``layout_grammar.kind == "kpi_band_over_charts"`` the ``<zones>``
    structure is::

        layout-basic (canvas)
          layout-flow param='vert'       ← outer vertical stack
            [text zones: title, subtitle, header textZones]
            layout-flow param='horz'     ← KPI tile band
              worksheet zones (kpi tiles)
            layout-flow param='horz'     ← chart band
              worksheet zones (charts)
            [text zones: footer textZones]

    KPI tiles are identified via ``layout_grammar.kpi_tile_titles``; charts via
    ``layout_grammar.chart_titles``.  Falls back: sheets whose ``kind=="kpi_tile"``
    go to the band; others go to charts.

    Zone id allocation (disjoint ranges, ``_quuid`` determinism guard stays green):
    - layout-basic canvas:       id=1
    - outer flow (id=2):         id=2
    - worksheet zones (default): id=3 … 3+N-1
    - text zones (new):          id=40001 … 40099
    - sub-flow containers (new): id=40100 … 40199

    Args:
        name:            Dashboard name.
        layout:          Default zone layout for the tiled path
                         (``"tiled_vertical"`` or ``"tiled_horizontal"``).
        titles:          Ordered worksheet titles to include.
        canvas_width:    Dashboard pixel width.
        canvas_height:   Dashboard pixel height.
        dashboard_index: Zero-based index for deterministic simple-id UUID.
        title:           Optional dashboard title → emits a bold text zone.
        subtitle:        Optional subtitle → emits a second smaller text zone.
        text_zones:      Optional list of ``{"text":…, "position":"header"|"footer"}``
                         dicts for arbitrary header/footer annotations.
        layout_grammar:  Optional layout descriptor.  Supports
                         ``{"kind": "kpi_band_over_charts", "kpi_tile_titles": […],
                         "chart_titles": […]}``.
        brand:           Optional brand block (Phase E1, Slice B; ``model_dump()``
                         snake_case dict — see ``server.BrandModel``).  When
                         present, the title run uses
                         ``brand["typography"]["title"]`` (font/size/color) and
                         the subtitle run uses ``brand["typography"]["body"]``,
                         mirroring "Visualize Quota Attainment..." ~4478/~4488
                         exactly.  Absent (``None``, the default): the title/
                         subtitle runs keep the pre-brand hardcoded values
                         (byte-identical determinism guard).
        design_theme:    Optional resolved design-theme block (Design Excellence,
                         Slice D2; ``model_dump()`` snake_case dict — see
                         ``server.DesignThemeModel``).  When present:
                         ``design_theme["dashboard_background"]`` becomes a
                         ``background-color`` ``<zone-style>`` on the canvas
                         (``layout-basic``) zone; ``design_theme["chart_card"]``
                         becomes a ``<zone-style>`` on every chart/worksheet
                         zone EXCEPT kpi-tile zones in a
                         ``kpi_band_over_charts`` layout (KPI tiles are styled
                         starting Slice D4); ``design_theme["spacing"]["gutter"]``
                         supplies each chart zone's ``margin`` when
                         ``chart_card["margin"]`` is unset (an explicit
                         ``chart_card["margin"]`` always wins); and
                         ``design_theme["spacing"]["outer_margin"]`` becomes a
                         ``margin`` ``<zone-style>`` on the outer
                         ``layout-flow`` zone.  Absent (``None``, the default):
                         no ``<zone-style>`` is ever emitted (byte-identical
                         determinism guard, same discipline as ``brand``).
    """
    dashboard = ET.Element("dashboard", {"name": name})

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

    # -----------------------------------------------------------------------
    # Design Excellence, Slice D2: derive zone-style inputs from design_theme.
    # ``design_theme`` absent/None -> every value here is None/empty -> no
    # <zone-style> is ever appended below (the byte-identical guard).
    # -----------------------------------------------------------------------
    theme_dashboard_background = design_theme.get("dashboard_background") if design_theme else None
    theme_spacing: dict[str, Any] = (design_theme.get("spacing") or {}) if design_theme else {}
    theme_outer_margin = theme_spacing.get("outer_margin")
    theme_gutter = theme_spacing.get("gutter")
    theme_chart_card = design_theme.get("chart_card") if design_theme else None
    chart_zone_style_formats = _chart_card_zone_style_formats(theme_chart_card, theme_gutter)

    # -----------------------------------------------------------------------
    # Determine whether we are using the extended path (title/grammar) or the
    # default single-flow path.  Only the default path must be byte-identical
    # to the pre-3B version; the extended path adds new zone structure.
    # -----------------------------------------------------------------------

    # Normalise optional inputs so the comparison below is safe.
    header_text_zones: list[dict[str, str]] = []
    footer_text_zones: list[dict[str, str]] = []
    for tz in text_zones or []:
        if tz.get("position") == "footer":
            footer_text_zones.append(tz)
        else:
            header_text_zones.append(tz)

    grammar_kind = ""
    kpi_tile_titles: list[str] = []
    chart_titles: list[str] = []
    if layout_grammar:
        grammar_kind = str(layout_grammar.get("kind", ""))
        kpi_tile_titles = [str(t) for t in (layout_grammar.get("kpi_tile_titles") or [])]
        chart_titles = [str(t) for t in (layout_grammar.get("chart_titles") or [])]

    has_extras = bool(title or subtitle or header_text_zones or footer_text_zones or grammar_kind)

    if not has_extras:
        # --- DEFAULT PATH (byte-identical to pre-3B) ------------------------
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
        for i, (ws_title, quad) in enumerate(zip(titles, quads, strict=True)):
            # A worksheet zone is identified by `name` ALONE with NO `type`/`type-v2`.
            ws_zone = ET.SubElement(
                flow,
                "zone",
                {
                    "h": str(quad["h"]),
                    "id": str(i + 3),
                    "name": ws_title,
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
            # Slice D2: themed chart-card zone-style (last child, no-op if unset).
            _append_zone_style(ws_zone, chart_zone_style_formats)

        # Slice D2: outer_margin -> margin on the outer layout-flow zone.
        if theme_outer_margin is not None:
            _append_zone_style(flow, {"margin": str(theme_outer_margin)})

    else:
        # --- EXTENDED PATH: title / text zones / kpi_band_over_charts -------
        # Zone id counter for new elements (text zones and sub-flows).
        # Disjoint ranges:
        #   1          → layout-basic canvas
        #   2          → outer layout-flow
        #   3 … 3+N-1  → worksheet zones (default path only)
        # In the extended path worksheet zones are allocated from 3 upward too
        # (via _append_worksheet_zones), but we need text/sub-flow ids that
        # do NOT overlap.  We use id >= 40001 for all new zone types.
        next_id = [40001]  # mutable counter via list

        def _next_id() -> int:
            nid = next_id[0]
            next_id[0] += 1
            return nid

        # Outer layout-flow (param='vert' for the extended path: text on top,
        # content below, optional footer at the bottom).
        outer_flow = ET.SubElement(
            container,
            "zone",
            {
                "h": "100000",
                "id": "2",
                "param": "vert",
                "type-v2": "layout-flow",
                "w": "100000",
                "x": "0",
                "y": "0",
            },
        )

        # --- Header text zones (title, subtitle, explicit header textZones) --
        # Title: no brand → bold=true, fontsize=20 (mirrors wb6 ~1664).
        # With brand → typography.title drives fontcolor/fontname/fontsize,
        # no bold attribute (mirrors wb7 ~4478: the "Tableau Bold" font name
        # itself carries the boldness). See _title_or_subtitle_run_attrs.
        if title:
            t_bold, t_fontsize, t_fontcolor, t_fontname = _title_or_subtitle_run_attrs(
                _resolve_font_spec(brand, "title"), default_bold=True, default_fontsize=20
            )
            title_zone: ET.Element = _build_text_zone(
                title,
                _next_id(),
                bold=t_bold,
                fontsize=t_fontsize,
                fontcolor=t_fontcolor,
                fontname=t_fontname,
                h=6000,
            )
            outer_flow.append(title_zone)

        # Subtitle: no brand → fontsize=14, no bold (mirrors wb7 ~4487 shape).
        # With brand → typography.body drives fontcolor/fontname/fontsize
        # (mirrors wb7 ~4488 exactly).
        if subtitle:
            s_bold, s_fontsize, s_fontcolor, s_fontname = _title_or_subtitle_run_attrs(
                _resolve_font_spec(brand, "body"), default_bold=False, default_fontsize=14
            )
            subtitle_zone: ET.Element = _build_text_zone(
                subtitle,
                _next_id(),
                bold=s_bold,
                fontsize=s_fontsize,
                fontcolor=s_fontcolor,
                fontname=s_fontname,
                h=4000,
            )
            outer_flow.append(subtitle_zone)

        # Explicit header text zones.
        for htz in header_text_zones:
            htz_zone: ET.Element = _build_text_zone(
                str(htz["text"]), _next_id(), bold=False, fontsize=12, h=4000
            )
            outer_flow.append(htz_zone)

        # --- Content zone(s) ------------------------------------------------
        if grammar_kind == "kpi_band_over_charts":
            # Split titles into KPI-tile and chart groups.
            # If kpi_tile_titles/chart_titles were not supplied by the caller,
            # fall back: all titles = charts (safe default, no band).
            effective_kpi = kpi_tile_titles if kpi_tile_titles else []
            effective_charts = chart_titles if chart_titles else [
                t for t in titles if t not in set(effective_kpi)
            ]

            # KPI band (param='horz', one zone per KPI tile).
            if effective_kpi:
                kpi_flow = ET.SubElement(
                    outer_flow,
                    "zone",
                    {
                        "h": "20000",
                        "id": str(_next_id()),
                        "param": "horz",
                        "type-v2": "layout-flow",
                        "w": "100000",
                        "x": "0",
                        "y": "0",
                    },
                )
                # Slice D2: KPI tiles are NOT themed here (deferred to Slice D4)
                # — no zone_style_formats passed.
                _append_worksheet_zones(kpi_flow, effective_kpi, id_start=3)

            # Charts band (param='horz', one zone per chart).
            if effective_charts:
                chart_flow = ET.SubElement(
                    outer_flow,
                    "zone",
                    {
                        "h": "80000",
                        "id": str(_next_id()),
                        "param": "horz",
                        "type-v2": "layout-flow",
                        "w": "100000",
                        "x": "0",
                        "y": "0",
                    },
                )
                # Slice D2: themed chart-card zone-style on every chart zone.
                _append_worksheet_zones(
                    chart_flow,
                    effective_charts,
                    id_start=3 + len(effective_kpi),
                    zone_style_formats=chart_zone_style_formats,
                )

        else:
            # Non-kpi_band grammar (or no grammar specified): use a single
            # layout-flow with the default tiling direction.
            flow_param = "horz" if layout == "tiled_horizontal" else "vert"
            inner_flow = ET.SubElement(
                outer_flow,
                "zone",
                {
                    "h": "100000",
                    "id": str(_next_id()),
                    "param": flow_param,
                    "type-v2": "layout-flow",
                    "w": "100000",
                    "x": "0",
                    "y": "0",
                },
            )
            quads = _tile_zones(titles, layout)
            for i, (ws_title, quad) in enumerate(zip(titles, quads, strict=True)):
                ws_zone = ET.SubElement(
                    inner_flow,
                    "zone",
                    {
                        "h": str(quad["h"]),
                        "id": str(i + 3),
                        "name": ws_title,
                        "w": str(quad["w"]),
                        "x": str(quad["x"]),
                        "y": str(quad["y"]),
                    },
                )
                ET.SubElement(
                    ws_zone,
                    "layout-cache",
                    {"cell-count-h": "1", "cell-count-w": "1", "type-h": "cell", "type-w": "cell"},
                )
                # Slice D2: themed chart-card zone-style (last child, no-op if unset).
                _append_zone_style(ws_zone, chart_zone_style_formats)

        # --- Footer text zones ----------------------------------------------
        for ftz in footer_text_zones:
            ftz_zone: ET.Element = _build_text_zone(
                str(ftz["text"]), _next_id(), bold=False, fontsize=12, h=4000
            )
            outer_flow.append(ftz_zone)

        # Slice D2: outer_margin -> margin on the outer layout-flow zone. Must
        # be appended LAST — after every text/content zone above — so it
        # satisfies the XSD's Zone-ZoneStyle-G "last child" ordering.
        if theme_outer_margin is not None:
            _append_zone_style(outer_flow, {"margin": str(theme_outer_margin)})

    # Slice D2: dashboard_background -> background-color on the canvas
    # (layout-basic) zone. Appended last, after the single flow/outer_flow
    # child built above (both branches), satisfying the "last child" rule.
    if theme_dashboard_background:
        _append_zone_style(container, {"background-color": str(theme_dashboard_background)})

    # <simple-id> is required for dashboard elements by the XSD.
    # Offset by 10000 to avoid collision with worksheet UUIDs.
    ET.SubElement(dashboard, "simple-id", {"uuid": _quuid(10000 + dashboard_index + 1)})

    return dashboard


# ---------------------------------------------------------------------------
# Story (storyboard) builder — Phase E4, Pillar E
# ---------------------------------------------------------------------------


def _build_story(
    name: str,
    story_points: list[dict[str, Any]],
    dashboard_index: int,
    canvas_width: int,
    canvas_height: int,
    valid_sheet_names: set[str],
    nav_type: str = "caption",
) -> ET.Element:
    """Return a ``<dashboard type='storyboard'>`` ET.Element (Phase E4 — Stories).

    Verified structure (mirrors a real Tableau-authored story .twb; the shape
    below is gated against the official XSD's ``FlipboardDoc-G`` /
    ``StoryPoint-G`` groups in ``test_twb_story.py``)::

        <dashboard name='Story: ...' type='storyboard'>
          <style/>
          <size .../>
          <zones>
            <zone h='100000' id='1' type-v2='layout-basic' w='100000' x='0' y='0'>
              <zone h='100000' id='2' param='vert' type-v2='layout-flow' w='100000' x='0' y='0'>
                <zone h='7500' id='3' type='title' w='100000' x='0' y='0'/>
                <zone h='8000' id='4' is-fixed='true' paired-zone-id='5' type='flipboard-nav'
                      w='100000' x='0' y='7500'/>
                <zone h='84500' id='5' paired-zone-id='4' type='flipboard'
                      w='100000' x='0' y='15500'>
                  <flipboard active-id='1' nav-type='caption' show-nav-arrows='true'>
                    <story-points>
                      <story-point caption='...' captured-sheet='...' id='1'/>
                      ...
                    </story-points>
                  </flipboard>
                </zone>
              </zone>
            </zone>
          </zones>
          <simple-id uuid='...'/>
        </dashboard>

    The title / flipboard-nav / flipboard zones carry a bare ``type='...'``
    attribute — NOT ``type-v2`` (every other zone kind in this module uses
    ``type-v2``). This is the attribute spelling a real Tableau-authored story
    workbook uses for these three zone kinds. The official XSD does not
    declare a ``type`` attribute on ``Zone-G`` at all (only ``type-v2``); it
    validates via the ``anyAttribute namespace='##local'
    processContents='skip'`` wildcard the XSD puts on every zone, which
    accepts any unqualified attribute without checking its value — so
    ``type='title'`` etc. are simultaneously schema-valid AND byte-for-byte
    faithful to the reference spelling (no compromise between the two was
    needed).

    Args:
        name:               Story dashboard name (e.g. ``"Story: Q4 Review"``).
        story_points:       Ordered list of ``{caption, captured_sheet}`` dicts
                             (snake_case keys — post ``model_dump()``).
        dashboard_index:    Zero-based index for deterministic simple-id UUID
                             allocation (disjoint ``_quuid`` range — see below).
        canvas_width:       Story canvas pixel width (same ``<size>`` semantics
                             as a regular dashboard).
        canvas_height:      Story canvas pixel height.
        valid_sheet_names:  The set of every worksheet + regular-dashboard name
                             already defined in this workbook. Every
                             ``captured_sheet`` MUST be a member of this set —
                             enforced here with a loud ``ValueError`` (never a
                             silently-broken reference left for Tableau Cloud
                             to reject at render time).
        nav_type:           Flipboard navigator style: ``"caption"`` (default),
                             ``"number"``, ``"dot"``, or ``"arrowonly"``.

    Returns:
        A ``<dashboard>`` ET.Element. The caller appends it into the SAME
        shared ``<dashboards>`` container as regular dashboards (see
        ``_build_dashboard``'s docstring for why there is only ever one).

    Raises:
        ValueError: if ``story_points`` is empty, or any ``captured_sheet`` is
            not in ``valid_sheet_names``.
    """
    if not story_points:
        raise ValueError(f"Story {name!r} must have at least one story point.")

    unknown = sorted(
        {
            str(p.get("captured_sheet"))
            for p in story_points
            if str(p.get("captured_sheet")) not in valid_sheet_names
        }
    )
    if unknown:
        raise ValueError(
            f"Story {name!r} references unknown captured_sheet(s) {unknown!r}. "
            f"Valid worksheet/dashboard names: {sorted(valid_sheet_names)!r}."
        )

    dashboard = ET.Element("dashboard", {"name": name, "type": "storyboard"})
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
    container = ET.SubElement(
        zones_el,
        "zone",
        {"h": "100000", "id": "1", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"},
    )
    outer_flow = ET.SubElement(
        container,
        "zone",
        {
            "h": "100000",
            "id": "2",
            "param": "vert",
            "type-v2": "layout-flow",
            "w": "100000",
            "x": "0",
            "y": "0",
        },
    )

    # Fixed vertical split: title / nav bar / flipboard content. Proportions
    # mirror typical Tableau Desktop story output (title ~7.5%, nav ~8%,
    # content absorbs the remainder) — the XSD does not mandate exact values.
    title_h = 7500
    nav_h = 8000
    flip_h = 100000 - title_h - nav_h

    ET.SubElement(
        outer_flow,
        "zone",
        {"h": str(title_h), "id": "3", "type": "title", "w": "100000", "x": "0", "y": "0"},
    )
    ET.SubElement(
        outer_flow,
        "zone",
        {
            "h": str(nav_h),
            "id": "4",
            "is-fixed": "true",
            "paired-zone-id": "5",
            "type": "flipboard-nav",
            "w": "100000",
            "x": "0",
            "y": str(title_h),
        },
    )
    flip_zone = ET.SubElement(
        outer_flow,
        "zone",
        {
            "h": str(flip_h),
            "id": "5",
            "paired-zone-id": "4",
            "type": "flipboard",
            "w": "100000",
            "x": "0",
            "y": str(title_h + nav_h),
        },
    )
    flipboard_el = ET.SubElement(
        flip_zone,
        "flipboard",
        {"active-id": "1", "nav-type": nav_type, "show-nav-arrows": "true"},
    )
    story_points_el = ET.SubElement(flipboard_el, "story-points")
    for i, point in enumerate(story_points):
        ET.SubElement(
            story_points_el,
            "story-point",
            {
                "caption": str(point["caption"]),
                "captured-sheet": str(point["captured_sheet"]),
                "id": str(i + 1),
            },
        )

    # <simple-id> offset: 40000 + story index — disjoint from worksheets (1..),
    # regular dashboards (10000+), worksheet windows (20000+), and dashboard
    # windows (30000+). See _append_story_window for the matching 50000+ range.
    ET.SubElement(dashboard, "simple-id", {"uuid": _quuid(40000 + dashboard_index + 1)})

    return dashboard


def _append_story_window(
    windows_el: ET.Element,
    story_name: str,
    story_points: list[dict[str, Any]],
    story_index: int,
) -> None:
    """Append a ``class='dashboard'`` ``<window>`` entry for one story.

    Mirrors the regular-dashboard window pattern exactly — the 400011 lesson
    (see ``build_twb_xml``'s dashboard-window loop): ``<viewpoints>`` lists
    every DISTINCT ``captured_sheet`` referenced by the story's points (each
    with a ``<zoom type='entire-view'/>``, first-appearance order), followed
    by ``<active id='-1'/>`` and a ``<simple-id>`` from a disjoint ``_quuid``
    range (50000+, distinct from worksheet/dashboard/story simple-ids and from
    worksheet/dashboard window simple-ids).
    """
    win = ET.SubElement(windows_el, "window", {"class": "dashboard", "name": story_name})
    viewpoints = ET.SubElement(win, "viewpoints")
    seen: list[str] = []
    for point in story_points:
        captured = str(point["captured_sheet"])
        if captured not in seen:
            seen.append(captured)
            vp = ET.SubElement(viewpoints, "viewpoint", {"name": captured})
            ET.SubElement(vp, "zoom", {"type": "entire-view"})
    ET.SubElement(win, "active", {"id": "-1"})
    ET.SubElement(win, "simple-id", {"uuid": _quuid(50000 + story_index + 1)})


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
    brand: dict[str, Any] | None = None,
    stories: list[dict[str, Any]] | None = None,
    design_theme: dict[str, Any] | None = None,
) -> str:
    """Build a schema-valid TWB XML string.

    Workbook child ordering (required by XSD):
    (``<preferences>`` if branded) → ``<datasources>`` → ``<worksheets>`` →
    ``<dashboards>`` (if any) → ``<windows>`` → ``<explain-data>`` (required).

    When ``dashboards`` is ``None`` (default), both calls with no ``dashboards``
    kwarg and with an explicit ``dashboards=None`` produce byte-identical XML
    (a determinism guard — not a byte-snapshot comparison against the pre-feature
    version; the worksheet/window structure was re-baselined for schema validity).
    The same determinism guarantee holds for ``brand``: omitting it (or passing
    ``None`` explicitly) never changes the output (Phase E1, Slice B), for
    ``stories`` (Phase E4): omitting it never changes the output either, and for
    ``design_theme`` (Design Excellence, Slice D2): omitting it never changes
    the output either.

    Args:
        brand: Optional brand block (``model_dump()`` snake_case dict — see
            ``server.BrandModel``). When present: a workbook-level
            ``<preferences><color-palette>`` is emitted (mirrors "Visualize
            Quota Attainment..." ~26-41), the dashboard title/subtitle runs
            use ``brand["typography"]``, and every measure ``<column>`` gets a
            ``default-format`` attribute via :func:`classify_measure_format`.
        stories: Optional list of story specs (Phase E4 — Stories), each
            ``{name, nav_type?, points: [{caption, captured_sheet}]}``
            (snake_case — post ``model_dump()``). Each is emitted as a
            ``<dashboard type='storyboard'>`` (see :func:`_build_story`)
            appended into the SAME ``<dashboards>`` container as
            ``dashboards``, after every regular dashboard, plus a matching
            ``class='dashboard'`` window entry. Every ``captured_sheet`` MUST
            name an existing worksheet or regular-dashboard in this workbook —
            enforced with a loud ``ValueError`` otherwise.
        design_theme: Optional resolved design-theme block (Design Excellence,
            Slice D2; ``model_dump()`` snake_case dict — see
            ``server.DesignThemeModel``). Forwarded as-is to every
            :func:`_build_dashboard` call — see its docstring for the full
            zone-style mapping. Absent (the default): byte-identical to
            before this slice.
    """
    slug = _slug(datasource_name)
    content_key = datasource_content_url or slug
    ds_internal = _ds_internal_name(content_key)
    server_host = _server_host(server_url)
    workbook = ET.Element(
        "workbook",
        {"source-build": SOURCE_BUILD, "version": TWB_VERSION},
    )

    # --- Brand preferences (workbook-level, BEFORE <datasources> per XSD) ---
    if brand:
        workbook.append(_build_preferences_element(brand))

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
    # Collect geo-role map from sheets to stamp semantic-role on geo columns.
    seen_dims: list[str] = []
    seen_measures: list[str] = []
    sqlproxy_geo_role_map: dict[str, str] = {}
    for sheet in sheets:
        for field in [*sheet.get("cols", []), *sheet.get("rows", [])]:
            if str(field) not in seen_dims:
                seen_dims.append(str(field))
        for field in sheet.get("measures", []):
            if str(field) not in seen_measures:
                seen_measures.append(str(field))
        g = sheet.get("geo")
        if g:
            geo_field = str(g["geo_field"])
            geo_role = str(g.get("geo_role", "state")).lower()
            sqlproxy_geo_role_map[geo_field] = _GEO_SEMANTIC_ROLE.get(
                geo_role, "[State].[Name]"
            )
            # Ensure the geo dimension is declared in the datasource.
            if geo_field not in seen_dims:
                seen_dims.append(geo_field)
            # Ensure the color measure is declared if present.
            geo_color = g.get("color_measure")
            if geo_color and str(geo_color) not in seen_measures:
                seen_measures.append(str(geo_color))
    for field in seen_dims:
        dim_attrs: dict[str, str] = {
            "datatype": "string",
            "name": f"[{field}]",
            "role": "dimension",
            "type": "nominal",
        }
        if field in sqlproxy_geo_role_map:
            dim_attrs["semantic-role"] = sqlproxy_geo_role_map[field]
        ET.SubElement(datasource, "column", dim_attrs)
    brand_formats: dict[str, Any] | None = brand.get("formats") if brand else None
    for field in seen_measures:
        measure_attrs: dict[str, str] = {
            "datatype": "real",
            "name": f"[{field}]",
            "role": "measure",
            "type": "quantitative",
        }
        if brand_formats is not None:
            measure_attrs["default-format"] = classify_measure_format(field, brand_formats)
        ET.SubElement(datasource, "column", measure_attrs)

    # --- Worksheets ---------------------------------------------------------
    # re-baselined for schema-valid output (XSD A2)
    worksheets = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets.append(_build_worksheet(sheet, datasource_name, ds_internal, i))

    # --- Optional dashboard + story block (BEFORE <windows> per XSD) --------
    # XSD workbook sequence: Worksheets → Dashboards → Windows → explain-data.
    # Workbook-Dashboards-G allows exactly ONE <dashboards> element per
    # workbook (holding maxOccurs="unbounded" <dashboard> children) — every
    # regular dashboard AND every story (also a <dashboard type='storyboard'>,
    # Phase E4) is appended into that SAME container, stories AFTER regular
    # dashboards. When both ``dashboards`` and ``stories`` are None/empty
    # (default), no <dashboards> element is emitted at all — both call forms
    # (kwargs omitted vs. explicit None) produce byte-identical XML — a
    # determinism guard, not a pre-feature snapshot.
    if dashboards or stories:
        dashboards_container = ET.SubElement(workbook, "dashboards")
        if dashboards:
            for db_index, db in enumerate(dashboards):
                db_name = str(db.get("name", "Dashboard 1"))
                db_titles: list[str] = [str(t) for t in db.get("titles", [])]
                if not db_titles:
                    db_titles = [str(s["title"]) for s in sheets]
                dashboard_el = _build_dashboard(
                    db_name,
                    dashboard_layout,
                    db_titles,
                    canvas_width,
                    canvas_height,
                    db_index,
                    title=db.get("title") or None,
                    subtitle=db.get("subtitle") or None,
                    text_zones=db.get("text_zones") or None,
                    layout_grammar=db.get("layout_grammar") or None,
                    brand=brand,
                    design_theme=design_theme,
                )
                dashboards_container.append(dashboard_el)
        if stories:
            # Every captured_sheet must resolve to an existing worksheet or
            # regular-dashboard name — validated inside _build_story (fails
            # loud with ValueError, listing the valid names).
            valid_names: set[str] = {str(s["title"]) for s in sheets}
            if dashboards:
                valid_names |= {str(db.get("name", "Dashboard 1")) for db in dashboards}
            for story_index, story in enumerate(stories):
                story_name = str(story.get("name") or f"Story {story_index + 1}")
                story_points = list(story.get("points") or [])
                nav_type = str(story.get("nav_type") or "caption")
                story_el = _build_story(
                    story_name,
                    story_points,
                    story_index,
                    canvas_width,
                    canvas_height,
                    valid_names,
                    nav_type=nav_type,
                )
                dashboards_container.append(story_el)

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

    if stories:
        # Story window entries (Phase E4) — same class='dashboard' pattern as
        # regular dashboards, disjoint _quuid range (50000+). See
        # _append_story_window's docstring.
        for story_index, story in enumerate(stories):
            story_name = str(story.get("name") or f"Story {story_index + 1}")
            story_points = list(story.get("points") or [])
            _append_story_window(windows, story_name, story_points, story_index)

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
    brand: dict[str, Any] | None = None,
    stories: list[dict[str, Any]] | None = None,
    design_theme: dict[str, Any] | None = None,
) -> Path:
    """Build a .twbx (zip containing the generated .twb) for a published datasource.

    When ``dashboards`` is ``None`` (default), the two call forms (no kwarg and
    explicit ``None``) produce byte-identical output (determinism guard).
    Pass a non-None list to include a ``<dashboards>`` block and a dashboard
    window entry. ``brand`` (Phase E1, Slice B), ``stories`` (Phase E4), and
    ``design_theme`` (Design Excellence, Slice D2) follow the same determinism
    guarantee — see :func:`build_twb_xml`.
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
        brand=brand,
        stories=stories,
        design_theme=design_theme,
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
    geo_role_map: dict[str, str] | None = None,
    formats: dict[str, Any] | None = None,
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
        geo_role_map:     Optional mapping of field name → semantic-role string
                          (e.g. ``{"State": "[State].[Name]"}``).  When provided,
                          any ``<column>`` whose name matches a key gets a
                          ``semantic-role`` attribute stamped on it.  Mirrors
                          wb1 ~2804 and the ``_GEO_SEMANTIC_ROLE`` lookup.
        formats:          Optional ``brand["formats"]`` dict (Phase E1, Slice B).
                          When provided, every ``role="measure"`` column gets a
                          ``default-format`` attribute via
                          :func:`classify_measure_format`.  Absent (the
                          default): no ``default-format`` attribute is added,
                          keeping the no-brand path byte-identical.

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
    # Stamp semantic-role on any column whose name is in geo_role_map.
    # This is how Tableau recognises geo dimensions for map rendering.
    # Mirrors wb1 ~2804: semantic-role='[State].[Name]' on [State/Province].
    resolved_geo_map: dict[str, str] = geo_role_map or {}
    for col in columns:
        col_attrs: dict[str, str] = {
            "datatype": col.datatype,
            "name": f"[{col.name}]",
            "role": col.role,
            "type": col.type,
        }
        if col.name in resolved_geo_map:
            col_attrs["semantic-role"] = resolved_geo_map[col.name]
        if formats is not None and col.role == "measure":
            col_attrs["default-format"] = classify_measure_format(col.name, formats)
        ET.SubElement(ds, "column", col_attrs)

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
    brand: dict[str, Any] | None = None,
    stories: list[dict[str, Any]] | None = None,
    design_theme: dict[str, Any] | None = None,
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
        brand:            Optional brand block (Phase E1, Slice B) — see
                          :func:`build_twb_xml` for the full behaviour.
                          Absent (the default): byte-identical to before this
                          slice (determinism guard).
        stories:          Optional list of story specs (Phase E4) — see
                          :func:`build_twb_xml` for the full behaviour. Absent
                          (the default): byte-identical to before this slice.
        design_theme:     Optional resolved design-theme block (Design
                          Excellence, Slice D2) — see :func:`build_twb_xml`
                          for the full behaviour. Absent (the default):
                          byte-identical to before this slice.

    Returns:
        A UTF-8 TWB XML string with an XML declaration header.
    """
    slug = _slug(datasource_name)
    ds_internal = f"federated.{slug}"

    workbook = ET.Element(
        "workbook",
        {"source-build": SOURCE_BUILD, "version": TWB_VERSION},
    )

    # --- Brand preferences (workbook-level, BEFORE <datasources> per XSD) ---
    if brand:
        workbook.append(_build_preferences_element(brand))

    # --- Collect geo-role map from all sheets --------------------------------
    # If any sheet carries a ``geo`` spec, thread the field→semantic-role
    # mapping into the datasource builder so the correct column gets the
    # ``semantic-role`` attribute.  Multiple sheets can reference the same
    # geo field — last writer wins (they should all agree on the role).
    geo_role_map: dict[str, str] = {}
    for sheet in sheets:
        g = sheet.get("geo")
        if g:
            geo_field = str(g["geo_field"])
            geo_role = str(g.get("geo_role", "state")).lower()
            semantic_role = _GEO_SEMANTIC_ROLE.get(geo_role, "[State].[Name]")
            geo_role_map[geo_field] = semantic_role

    # --- Datasource (federated / embedded extract) --------------------------
    datasources_el = ET.SubElement(workbook, "datasources")
    ds_el = _build_federated_datasource(
        datasource_name,
        hyper_filename,
        columns,
        geo_role_map=geo_role_map if geo_role_map else None,
        formats=brand.get("formats") if brand else None,
    )
    datasources_el.append(ds_el)

    # --- Worksheets ---------------------------------------------------------
    worksheets_el = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets_el.append(
            _build_worksheet(sheet, datasource_name, ds_internal, i)
        )

    # --- Optional dashboards + stories ---------------------------------------
    # Same single-<dashboards>-container discipline as build_twb_xml — see its
    # comment for the XSD rationale (Workbook-Dashboards-G allows exactly one
    # <dashboards> element per workbook).
    if dashboards or stories:
        dashboards_container = ET.SubElement(workbook, "dashboards")
        if dashboards:
            for db_index, db in enumerate(dashboards):
                db_name = str(db.get("name", "Dashboard 1"))
                db_titles: list[str] = [str(t) for t in db.get("titles", [])]
                if not db_titles:
                    db_titles = [str(s["title"]) for s in sheets]
                dashboard_el = _build_dashboard(
                    db_name,
                    dashboard_layout,
                    db_titles,
                    canvas_width,
                    canvas_height,
                    db_index,
                    title=db.get("title") or None,
                    subtitle=db.get("subtitle") or None,
                    text_zones=db.get("text_zones") or None,
                    layout_grammar=db.get("layout_grammar") or None,
                    brand=brand,
                    design_theme=design_theme,
                )
                dashboards_container.append(dashboard_el)
        if stories:
            valid_names: set[str] = {str(s["title"]) for s in sheets}
            if dashboards:
                valid_names |= {str(db.get("name", "Dashboard 1")) for db in dashboards}
            for story_index, story in enumerate(stories):
                story_name = str(story.get("name") or f"Story {story_index + 1}")
                story_points = list(story.get("points") or [])
                nav_type = str(story.get("nav_type") or "caption")
                story_el = _build_story(
                    story_name,
                    story_points,
                    story_index,
                    canvas_width,
                    canvas_height,
                    valid_names,
                    nav_type=nav_type,
                )
                dashboards_container.append(story_el)

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

    if stories:
        for story_index, story in enumerate(stories):
            story_name = str(story.get("name") or f"Story {story_index + 1}")
            story_points = list(story.get("points") or [])
            _append_story_window(windows_el, story_name, story_points, story_index)

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
    brand: dict[str, Any] | None = None,
    stories: list[dict[str, Any]] | None = None,
    design_theme: dict[str, Any] | None = None,
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
        brand:            Optional brand block (Phase E1, Slice B) — see
                          :func:`build_twb_xml`. Absent (the default):
                          byte-identical to before this slice.
        stories:          Optional list of story specs (Phase E4) — see
                          :func:`build_twb_xml`. Absent (the default):
                          byte-identical to before this slice.
        design_theme:     Optional resolved design-theme block (Design
                          Excellence, Slice D2) — see :func:`build_twb_xml`.
                          Absent (the default): byte-identical to before this
                          slice.

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
        brand=brand,
        stories=stories,
        design_theme=design_theme,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.twb", twb_xml)
        archive.write(hyper_path, arcname=f"Data/{hyper_filename}")

    return out_path
