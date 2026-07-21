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


def _append_color_palette(
    parent: ET.Element, *, name: str, palette_type: str, colors: list[str]
) -> None:
    """Append a ``<color-palette custom='true' name='...' type='...'>`` child.

    Shared by the categorical/sequential/diverging registrations below —
    same ``custom``/``name``/``type`` attribute order and per-``<color>``
    hex-normalization for every palette kind.
    """
    palette_el = ET.SubElement(
        parent,
        "color-palette",
        {"custom": "true", "name": name, "type": palette_type},
    )
    for color in colors:
        ET.SubElement(palette_el, "color").text = _normalize_hex_color(color)


def _build_preferences_element(brand: dict[str, Any]) -> ET.Element:
    """Return the workbook-level ``<preferences>`` element with its registered palettes.

    Categorical (mirrors "Visualize Quota Attainment for Executives in
    Multiple Ways" ~26-41)::

        <preferences>
          <color-palette custom='true' name='Barkbus Secondary Light' type='regular'>
            <color>#84c8b4</color>
            ...
          </color-palette>
        </preferences>

    Always emitted when a brand block is present (unconditionally, same as
    before Slice D6 — ``categorical`` may legitimately be empty, matching
    pre-D6 behavior of emitting a childless ``<color-palette>``).

    Sequential/diverging (Design Excellence, Slice D6): when
    ``brand.palette.sequential``/``diverging`` is non-empty, an ADDITIONAL
    ``<color-palette custom='true' name='<brandName> Sequential'
    type='ordered-sequential'>``/``...Diverging.../type='ordered-diverging'``
    is appended — mirrors the mined ``WB-062``/``WB-015``
    entries in ``design/corpus/recipes/palettes.yaml`` (``type:
    ordered-sequential``/``ordered-diverging``, ``custom: true``). Gated
    ONLY on the relevant list being non-empty — independent of
    ``design_theme`` (a harmless, independently-useful registration —
    selectable from Tableau Desktop's palette picker — kept even though the
    map-filled per-encoding override in :func:`_build_worksheet` no longer
    references it by name; see :func:`_map_filled_palette_style_rule`'s
    docstring for the live-probe #4 finding that motivated that change).
    Absent list -> no element (never an empty placeholder
    ``<color-palette>``) — this is the "categorical only" behavior the
    byte-identical guards in ``test_twb_branding.py`` and
    ``test_twb_palettes.py`` both rely on.

    Placed as the FIRST child of ``<workbook>`` (before ``<datasources>``),
    matching the reference's position and the XSD's ``WorkbookFile-CT``
    sequence (``Workbook-Preferences-G`` precedes ``Workbook-DataSources-G``).
    Only called when a brand block is present — see module-level note.
    """
    palette = brand.get("palette") or {}
    brand_name = str(brand.get("brand_name") or "Brand")
    categorical = [str(c) for c in (palette.get("categorical") or [])]
    sequential = [str(c) for c in (palette.get("sequential") or [])]
    diverging = [str(c) for c in (palette.get("diverging") or [])]

    preferences_el = ET.Element("preferences")
    _append_color_palette(
        preferences_el,
        name=f"{brand_name} Palette",
        palette_type="regular",
        colors=categorical,
    )
    if sequential:
        _append_color_palette(
            preferences_el,
            name=f"{brand_name} Sequential",
            palette_type="ordered-sequential",
            colors=sequential,
        )
    if diverging:
        _append_color_palette(
            preferences_el,
            name=f"{brand_name} Diverging",
            palette_type="ordered-diverging",
            colors=diverging,
        )
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


# ---------------------------------------------------------------------------
# Design Excellence, an external Tableau MCP skill suite enhancement #2 — default descending-by-
# measure sort for ranking bar charts (BI_DESIGN.md Sec 2.2).
# ---------------------------------------------------------------------------

# Defensive name-pattern guard mirroring BI_DESIGN.md Sec 1.2 priority-2
# (temporal dimension detection). Sec 2.1's decision table never routes a
# temporal dimension onto a bar mark (C-01/C-02 send those to Line), so this
# should never actually fire in practice — it exists purely as a second line
# of defense against a caller that bypasses the planner's own rules.
_TEMPORAL_DIM_NAME_RE = re.compile(
    r"\b(date|day|month|quarter|year|week|timestamp|time|period|fiscal|fy|cy)\b",
    re.IGNORECASE,
)


def _bar_ranking_sort_target(sheet: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(dimension_field, measure_field)`` for a ranking bar sheet
    that should get a default descending-by-measure sort, or ``None`` when
    the sheet does not unambiguously qualify.

    VizCritique-Pro flags alphabetical-when-ranking as the single most common
    "make it readable" anti-pattern; BI_DESIGN.md Sec 2.2 previously had no
    sort rule at all. This default is deliberately narrow — exactly one
    dimension and exactly one measure, ``mark_type == "bar"``,
    ``kind != "kpi_tile"`` — so the no-op path (multi-measure C-08 stacked
    bar, multi-dimension C-09 Color-encoded bar, any non-bar mark, KPI
    tiles) stays byte-identical to before this slice: guessing which of
    several measures/dimensions should drive the sort would be worse than
    leaving Tableau's own default (source order) in place.
    """
    if str(sheet.get("kind", "chart")) == "kpi_tile":
        return None
    if str(sheet.get("mark_type", "bar")).lower() != "bar":
        return None

    dims = [str(d) for d in sheet.get("cols", [])] + [str(d) for d in sheet.get("rows", [])]
    measures = [str(m) for m in sheet.get("measures", [])]
    if len(dims) != 1 or len(measures) != 1:
        return None

    dim_field = dims[0]
    if _TEMPORAL_DIM_NAME_RE.search(dim_field):
        return None

    return dim_field, measures[0]


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
    parent: ET.Element,
    dimensions: list[str],
    measures: list[str],
) -> None:
    """Append dimension/measure ``<column>``/``<column-instance>`` pairs to *parent*.

    Args:
        parent:      The ``<datasource-dependencies>`` element to append into.
        dimensions:  Dimension field names.
        measures:    Measure field names.

    Design Excellence, Slice D4 FINAL-SHAPE note: an earlier hotfix round
    threaded an optional ``measure_default_formats`` map through here to
    stamp ``default-format`` directly on a RAW measure's worksheet-local
    ``<column>`` (KPI-tile compact/arrow formatting). Live-probe #3's bisect
    ladder (V7 specifically) proved Tableau Cloud IGNORES a ``default-format``
    on a raw (non-calculated) field's local dependency column for a naked
    BAN view — the param has been removed as dead weight. KPI-tile BAN
    values now carry their format on a dedicated worksheet-local CALCULATED
    column instead (see :func:`_append_kpi_ban_calc_column`), which V7/V11/
    V12 proved Cloud DOES honor.
    """
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
        measure_attrs: dict[str, str] = {
            "datatype": "real",
            "name": f"[{field}]",
            "role": "measure",
            "type": "quantitative",
        }
        ET.SubElement(parent, "column", measure_attrs)
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
    design_theme: dict[str, Any] | None = None,
    brand: dict[str, Any] | None = None,
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

    Chrome rules + mark labels (Design Excellence, Slice D3)
    ---------------------------------------------------------
    When ``design_theme`` is present, ``design_theme["chrome"]`` drives two
    independent style locations:

    - TABLE-level ``<style>`` (was an unconditional bare ``<style/>``):
      ``chrome.hide_axis_ticks`` adds an ``axis`` style-rule with
      ``line-visibility='off' tick-color='#00000000'`` — applied to EVERY
      sheet kind (harmless blanket chrome removal). ``SheetModel.style_rules``
      (Slice D1's per-sheet escape hatch) is appended after, verbatim,
      sorted by ``element`` name — independently of ``design_theme``.
    - PANE-level ``<style>`` (new, optional, LAST child of ``<pane>``):
      ``chrome.datalabel`` and ``chrome.show_mark_labels`` — ONLY for
      ``kind='chart'`` sheets whose ``mark_type`` is ``bar``/``line`` (see
      :func:`_is_labelable_chart_sheet`'s docstring for the scoping
      rationale). NOT emitted for ``kpi_tile`` or ``map_filled`` sheets.

    See :func:`_table_style_rules`/:func:`_pane_style_rules` for the mined
    XPath provenance. Absent ``design_theme`` (the default): byte-identical
    to before this slice.

    Styled KPI tiles (Design Excellence, Slice D4; FINAL SHAPE after a
    live-probe bisect ladder — V0-V12, see the module-level "BAN mechanism"
    comment block above :data:`_KPI_COMPACT_NUMBER_FORMAT` for the full
    provenance)
    ---------------------------------------------------------------------
    When ``sheet["kind"] == "kpi_tile"`` AND ``design_theme["kpi_tile"]`` is
    present AND ``sheet["kpi"]["primary_measure"]`` is set (the
    ``kpi_ban_active`` gate):

    - A worksheet-local CALCULATED ``<column>`` per BAN value
      (:func:`_append_kpi_ban_calc_column`) — ``[Calculation_BAN_<primary>]
      = SUM([primary])`` always, plus ``[Calculation_BAN_<delta>_Delta] =
      SUM([delta])`` when ``kpi.delta_measure`` is set — each carrying the
      compact number/currency (or, for the delta column, the mined
      arrow-direction) format as its OWN ``default-format``. Live-probe
      #3's bisect ladder (V7) proved a RAW field's local ``default-format``
      is silently ignored by Tableau Cloud for a naked BAN view; a
      CALCULATED field's own local ``default-format`` is NOT. The primary
      and delta ``<text>`` shelf encodings are repointed at these calc
      instances; the comparison measure (when set) stays a plain,
      unformatted raw-field ``<text>`` encoding (stamping a compact format
      on ITS raw local column was proven equally inert, so it is no longer
      emitted at all — dead weight removed, not left in place).
    - A transparent-background TABLE-level ``<style-rule element='table'>``
      (:func:`_kpi_tile_table_transparency_rule`) reveals the KPI tile
      ZONE's themed background through the worksheet's own (otherwise
      opaque white) table fill, when ``kpi_tile["background"]`` is set.
    - A ``<customized-label>`` (:func:`_kpi_tile_customized_label`) on the
      PANE, in the bisect-proven "graft" run idiom: a letter-spaced
      UPPERCASE caption run derived from the tile's OWN title (e.g.
      ``"Sales"`` -> ``"S A L E S"``), the mined literal ``"Æ\\n"``
      glyph-prefixed newline-separator runs (V9/V10 proved a plain ``"\\n"``
      is NOT part of the working shape), and one CDATA placeholder run per
      BAN value (``<[ds].[calc instance]>``) referencing the calc columns
      above. NO ``fontname`` attribute is ever emitted (V11/V12, the
      proven-working shape, carry none — an untested attribute is not
      risked here; BAN font family follows the workbook default instead,
      a documented, honest limitation).
    - A pane-level ``<style-rule element='cell'><format attr='text-align'
      value='center'/></style-rule>`` PLUS ``<style-rule element='mark'>``
      (``mark-labels-show``/``mark-labels-cull`` both ``'true'``) —
      :func:`_kpi_tile_pane_style_rules`. Live-probe #3's V9/V10 proved
      mark-labels-show is NECESSARY (its absence silently drops the
      ``<customized-label>`` back to a plain default text-mark, or — once
      mark-labels-show is added to our OTHER attributes alone — to a BLANK
      mark) but not sufficient alone; V11/V12 (the full proven shape)
      confirm it IS sufficient combined with the rest of this mechanism.
    - ``selection-relaxation-option='selection-relaxation-allow'`` on the
      ``<pane>`` element (mined fidelity, part of the proven V11/V12 shape).
    - The per-worksheet TABLE-level ``element='title'`` style-rule
      (:func:`_kpi_tile_title_style_rule`) is SKIPPED — the in-label
      caption run above already labels the tile, and the dashboard zone
      that hosts this worksheet suppresses its title entirely
      (``show-title='false'``, mirroring WB-118's own mined KPI-tile
      zone attribute — see :func:`_build_dashboard`/:func:`_append_worksheet_zones`),
      making a title-color fix for text that will never render genuinely
      dead weight. (Kept as a defensive fallback for the degenerate case
      where ``kpi_tile`` theming is present but this specific sheet's
      ``kpi`` spec has no ``primary_measure`` — then no label/suppression
      happens either, so the title may still be visible.)

    No-op for non-``kpi_tile`` sheets or when ``design_theme["kpi_tile"]``
    is absent.

    Args:
        sheet:        Sheet spec dict.
        ds_caption:   Human-readable datasource caption.
        ds_internal:  Internal datasource name (``sqlproxy.*``).
        sheet_index:  Zero-based index used to derive a deterministic UUID.
        brand:        Optional resolved brand block (``model_dump()``
                      snake_case dict — see ``server.BrandModel``). Only
                      consumed for ``kpi_tile["ban"]`` font-size/font-family
                      (Slice D4); absent (the default) leaves those two
                      attrs unset (byte-identical guard).
        design_theme: Optional resolved design-theme block (``model_dump()``
                      snake_case dict — see ``server.DesignThemeModel``).
                      Absent (the default): byte-identical to before Slice D3.
    """
    title = str(sheet["title"])
    mark_type = str(sheet.get("mark_type", "bar")).lower()
    sheet_kind = str(sheet.get("kind", "chart"))
    cols_dims = [str(c) for c in sheet.get("cols", [])]
    rows_dims = [str(r) for r in sheet.get("rows", [])]
    measures = [str(m) for m in sheet.get("measures", [])]

    # Slice D3: design_theme.chrome, threaded through to both the table-level
    # and pane-level style-rule builders below. None when design_theme is
    # absent -> every downstream rules list is empty -> byte-identical.
    theme_chrome: dict[str, Any] | None = design_theme.get("chrome") if design_theme else None

    # Slice D4: design_theme.kpi_tile + brand.typography.ban, threaded to the
    # KPI-tile-only style-rule builders below. Both None when absent -> every
    # downstream rules list is empty -> byte-identical.
    theme_kpi_tile: dict[str, Any] | None = design_theme.get("kpi_tile") if design_theme else None
    brand_formats: dict[str, Any] | None = brand.get("formats") if brand else None
    brand_ban: dict[str, Any] | None = (brand.get("typography") or {}).get("ban") if brand else None
    ds_ref = f"[{ds_internal}]"

    # --- Scatter spec ------------------------------------------------------
    scatter = sheet.get("scatter")  # optional {x, y, breakdown?}
    is_scatter = mark_type == "scatter" or scatter is not None

    # --- Color spec --------------------------------------------------------
    color_spec = sheet.get("color")  # optional {field, kind}

    # --- KPI tile spec -----------------------------------------------------
    kpi_spec = sheet.get("kpi")  # optional {primary_measure, comparison_measure?, delta_measure?}
    is_kpi_tile = sheet_kind == "kpi_tile"

    # Design Excellence, Slice D4 FINAL SHAPE: whether the proven BAN
    # customized-label mechanism (calc columns, label, mark-labels-show/
    # cull, selection-relaxation) should be emitted for THIS sheet — a
    # SINGLE shared predicate so every call site below (dependency columns,
    # pane attrs, encodings, label, pane style, title-rule skip) can never
    # diverge on the gating condition.
    kpi_ban_active = bool(
        is_kpi_tile and theme_kpi_tile and kpi_spec and kpi_spec.get("primary_measure")
    )

    # --- Filled map (choropleth) spec --------------------------------------
    geo_spec = sheet.get("geo")  # optional {geo_field, geo_role, color_measure?}
    is_map_filled = mark_type == "map_filled" or geo_spec is not None
    map_color_measure = geo_spec.get("color_measure") if (is_map_filled and geo_spec) else None

    # Design Excellence, Slice D6 (FINAL SHAPE, live-probe #4): brand
    # sequential palette embedded on the map's color-measure encoding.
    # Gated on BOTH design_theme being present AND brand.palette.sequential
    # being non-empty (see _map_filled_sequential_colors) — protects the
    # no-theme byte-identical guard (test_twb_palettes.py group C). None ->
    # no <style-rule> appended below (byte-identical).
    map_palette_rule: ET.Element | None = None
    if map_color_measure and design_theme is not None:
        map_sequential_colors = _map_filled_sequential_colors(brand)
        if map_sequential_colors is not None:
            map_palette_rule = _map_filled_palette_style_rule(
                field_column=f"{ds_ref}.{_measure_instance(str(map_color_measure))}",
                colors=map_sequential_colors,
            )

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

    # Design Excellence, Slice D4 FINAL SHAPE (live-probe #3 bisect ladder,
    # V7/V11/V12): worksheet-local CALCULATED columns carrying the BAN
    # values' compact/arrow default-format — see _append_kpi_ban_calc_column
    # for why a CALCULATED column is used instead of stamping default-format
    # on the raw field's own local column (that mechanism is proven inert).
    # Appended AFTER the raw dependency columns above (same <deps> element;
    # the calc formulas reference the just-declared raw fields by name).
    kpi_primary_calc_instance: str | None = None
    kpi_delta_calc_instance: str | None = None
    if kpi_ban_active and kpi_spec and theme_kpi_tile:
        primary_field = str(kpi_spec["primary_measure"])
        kpi_primary_calc_instance = _append_kpi_ban_calc_column(
            deps,
            primary_field,
            suffix="",
            caption=f"{primary_field} (BAN)",
            default_format=_kpi_compact_format(primary_field, brand_formats or {}),
        )
        delta_field = kpi_spec.get("delta_measure")
        if delta_field:
            delta_field = str(delta_field)
            delta_format = (
                _KPI_DELTA_ARROW_FORMAT
                if theme_kpi_tile.get("use_semantic_delta_colors")
                else _kpi_compact_format(delta_field, brand_formats or {})
            )
            kpi_delta_calc_instance = _append_kpi_ban_calc_column(
                deps,
                delta_field,
                suffix="_Delta",
                caption=f"{delta_field} (BAN Delta)",
                default_format=delta_format,
            )

    # Design Excellence, an external Tableau MCP skill suite enhancement #2: default descending-by-
    # measure sort for ranking bar charts (BI_DESIGN.md Sec 2.2). Mirrors
    # WB-133's real <computed-sort> element — a child of <view>,
    # placed immediately before <aggregation> (ViewSpecification-G's Sort-G
    # precedes the mandatory <aggregation> in the XSD sequence). Unconditional
    # (not gated on design_theme): this is a data-readability default, not a
    # cosmetic theme choice. No-op (nothing appended) whenever the sheet does
    # not unambiguously qualify — see _bar_ranking_sort_target's docstring.
    bar_sort_target = _bar_ranking_sort_target(sheet)
    if bar_sort_target is not None:
        sort_dim_field, sort_measure_field = bar_sort_target
        ET.SubElement(
            view,
            "computed-sort",
            {
                "column": f"{ds_ref}.{_dim_instance(sort_dim_field)}",
                "direction": "DESC",
                "using": f"{ds_ref}.{_measure_instance(sort_measure_field)}",
            },
        )

    # <aggregation> is required by the XSD (last mandatory child of <view>)
    ET.SubElement(view, "aggregation", {"value": "true"})

    # --- <style> (required before <rows>/<cols> by XSD) ---------------
    # Slice D3: table-level style-rules (theme-driven axis-tick removal +
    # SheetModel.style_rules pass-through). Byte-identical to the pre-D3 bare
    # <style/> when there is nothing to say (see _build_style_element).
    table_rules = _table_style_rules(theme_chrome, sheet.get("style_rules"))
    if is_kpi_tile:
        # Design Excellence, Slice D4 FINAL SHAPE: the per-worksheet
        # title-legibility rule is SKIPPED whenever kpi_ban_active — the
        # in-label caption already labels the tile, and the dashboard zone
        # hosting it suppresses the title entirely (show-title='false',
        # mirroring WB-118's own mined zone attribute — see
        # _build_dashboard), making a title-color fix for never-rendered
        # text genuinely dead weight. Kept as a defensive fallback for the
        # degenerate case (kpi_tile theming present, but this sheet's kpi
        # spec has no primary_measure) where neither the label nor the
        # zone-level suppression happens.
        if not kpi_ban_active:
            kpi_title_rule = _kpi_tile_title_style_rule(theme_kpi_tile)
            if kpi_title_rule is not None:
                table_rules = [*table_rules, kpi_title_rule]
        # Live-probe #2b hotfix: transparent table background so the KPI
        # tile ZONE's navy background-color (Slice D2) shows through instead
        # of being hidden behind the table's own opaque white fill.
        kpi_transparency_rule = _kpi_tile_table_transparency_rule(theme_kpi_tile)
        if kpi_transparency_rule is not None:
            table_rules = [*table_rules, kpi_transparency_rule]
    # Live-probe #3 hotfix: the TABLE-level element='cell' font-size/
    # font-family/color rule Slice D4 originally emitted here is REMOVED —
    # fresh renders proved it does nothing for BAN typography on a naked
    # (rows/cols empty) Text mark. BAN typography now lives on the pane's
    # <customized-label> instead (see _kpi_tile_customized_label, appended
    # below in the <panes> section).
    style_el = _build_style_element(table_rules)
    if map_palette_rule is not None:
        # Design Excellence, Slice D6: appended as an independent style-rule
        # (Stylesheet-G's style-rule sequence is unbounded/unordered by
        # element name) — never merged into table_rules's (element, formats)
        # tuple shape, which only models <format> children, not <encoding>.
        style_el.append(map_palette_rule)
    table.append(style_el)

    # --- <panes> ------------------------------------------------------
    panes = ET.SubElement(table, "panes")

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
        if kpi_ban_active:
            # Design Excellence, Slice D4 FINAL SHAPE: mined fidelity, part
            # of the proven V11/V12 shape (our non-kpi_tile panes carry no
            # attributes at all, so this is the pane's only attribute).
            pane.set("selection-relaxation-option", "selection-relaxation-allow")
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
            # Multi-measure text: primary, comparison, delta (mirrors wb1 ~3394-3397).
            # Design Excellence, Slice D4 FINAL SHAPE: when kpi_ban_active,
            # the primary/delta encodings are repointed at their CALCULATED
            # column instances (kpi_primary_calc_instance/
            # kpi_delta_calc_instance) instead of the raw field — the
            # comparison measure ALWAYS stays a raw-field encoding (never
            # calc-backed; see _append_kpi_ban_calc_column's docstring).
            kpi_field_instances = {
                "primary_measure": kpi_primary_calc_instance,
                "delta_measure": kpi_delta_calc_instance,
            }
            for kpi_field_key in ("primary_measure", "comparison_measure", "delta_measure"):
                kpi_field = kpi_spec.get(kpi_field_key)
                if not kpi_field:
                    continue
                calc_instance = kpi_field_instances.get(kpi_field_key)
                instance = calc_instance if calc_instance else _measure_instance(str(kpi_field))
                encoding_elements.append(("text", f"{ds_ref}.{instance}"))
        elif mark_type == "text" and measures and not is_kpi_tile:
            # Plain text mark: single-measure text encoding (original behaviour)
            encoding_elements.append(("text", f"{ds_ref}.{_measure_instance(measures[0])}"))

        if encoding_elements:
            encodings_el = ET.SubElement(pane, "encodings")
            for tag, col_val in encoding_elements:
                ET.SubElement(encodings_el, tag, {"column": col_val})

        # Design Excellence, Slice D4 FINAL SHAPE: <customized-label> — the
        # mechanism that ACTUALLY renders BAN typography and makes the calc
        # columns' compact/arrow default-formats visible (see
        # _kpi_tile_customized_label's docstring). MUST come after
        # <encodings> and before <style> (PaneSpecification-G's
        # CustomLabel-G / Stylesheet-G ordering — verified against the
        # mined pane). None when kpi_primary_calc_instance is None (i.e.
        # kpi_ban_active is False).
        if is_kpi_tile:
            kpi_label_el = _kpi_tile_customized_label(
                theme_kpi_tile,
                brand_ban,
                ds_ref,
                title,
                primary_calc_instance=kpi_primary_calc_instance,
                delta_calc_instance=kpi_delta_calc_instance,
            )
            if kpi_label_el is not None:
                pane.append(kpi_label_el)

        # Slice D3: pane-level mark-labels/datalabel style-rule — ONLY for
        # bar/line chart sheets (see _is_labelable_chart_sheet), and MUST be
        # the LAST child of <pane> (PaneSpecification-G's Stylesheet-G
        # ordering). No-op (nothing appended) when there are no rules to say.
        if _is_labelable_chart_sheet(sheet):
            _append_style_rules(pane, _pane_style_rules(theme_chrome))
        elif is_kpi_tile:
            # Live-probe #2b hotfix (finding 4): centered BAN text — a
            # pane-scoped element='cell' rule (mirrors WB-118's own
            # <table><panes><pane><style>), also MUST be the LAST child of
            # <pane>. Design Excellence, Slice D4 FINAL SHAPE (live-probe
            # #3's V9-V12): a SECOND element='mark' rule
            # (mark-labels-show/cull) is added whenever kpi_ban_active — see
            # _kpi_tile_pane_style_rules's docstring. No-op when
            # design_theme has no kpi_tile block.
            _append_style_rules(pane, _kpi_tile_pane_style_rules(theme_kpi_tile, kpi_ban_active))

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


# Design Excellence, Slice D5 (superseded by Slice T2 below): themed
# header-band zone height (0-100000 grid).
#
# D5 ORIGINAL VALUE (9722) mirrored one exemplar dashboard's OWN ``h``
# attribute verbatim (design/corpus/recipes/text_zones.yaml ->
# /workbook/dashboards/dashboard[2]/zones/zone[4]/zone[1]/zone[1], source
# WB-117.twbx: ``<zone forceUpdate='true'
# h='9722' id='556' type-v2='text' ...>``). Beauty-gate round 2 flagged the
# resulting themed header as "too big" — nearly double the non-themed title
# zone's ``h=6000``, occupying ~9.7% of a dashboard-sized canvas.
#
# T2 (PLAN.md's Top-100 Corpus plan) FIX: replaced the single-exemplar value
# with the T1-mined STRATIFIED MEDIAN title-zone height RATIO for
# dashboard-sized canvases (design/corpus/stats/dashboard_norms.yaml ->
# strata."900-1400".title_height_ratio: median 0.0696, n=107,
# confidence "ok"). ``0.0696 * 100000 = 6960`` (rounded to a whole number on
# the 0-100000 grid) — a real, stratified, n>=15 mined value rather than one
# exemplar's own height, and the actual fix for the "title too big" defect
# (the driver was the header ZONE HEIGHT + long question-derived title TEXT
# LENGTH, not the title run's fontsize — see `shortenDashboardTitle` in
# `src/planner/plan.ts` for the length half of this fix).
_HEADER_ZONE_H = 6960


def _build_header_zone(
    title: str,
    subtitle: str | None,
    zone_id: int,
    brand: dict[str, Any] | None,
    header: dict[str, Any],
    h: int = _HEADER_ZONE_H,
) -> ET.Element:
    """Return a themed header ``<zone type-v2='text'>`` with title (+ subtitle) runs.

    Design Excellence, Slice D5. Mirrors the mined MULTI-RUN title-zone
    vocabulary in ``design/corpus/recipes/text_zones.yaml`` at
    ``/workbook/dashboards/dashboard[2]/zones/zone[4]/zone[1]/zone[1]``
    (source ``WB-117.twbx``, cited verbatim by
    ``design/corpus/themes/executive_dark.yaml``'s header provenance entry):
    a bold title run, a bare glyph-separator run (``"Æ  "`` — Tableau
    Desktop's own manual-line-break idiom, copied byte-for-byte from the
    source XML's ``<run fontcolor='#ffffff'>Æ  </run>``, NOT an invented
    ``"\\n"``), and a plain subtitle run — all inside ONE
    ``<formatted-text>``. This replaces the separate title-zone + subtitle-
    zone pair the non-themed path emits (see ``_build_dashboard``) whenever
    ``design_theme.header`` is present.

    Title/subtitle font family/size/bold still come from
    ``brand.typography.title``/``.body`` via ``_resolve_font_spec``/
    ``_title_or_subtitle_run_attrs`` — unchanged from the non-themed path.
    Only ``fontcolor`` is overridden, by ``header["title_color"]``/
    ``header["subtitle_color"]``, when set. The separator run's own
    ``fontcolor`` mirrors the mined entry's choice of the title-side color
    (the mined source's title and subtitle runs share one color, so this is
    the closest real, non-invented choice for the separator).

    When *subtitle* is falsy, only the title run is emitted (no separator,
    no subtitle run) — the natural single-run degenerate case of this same
    multi-run mechanism.

    The zone additionally receives a themed ``<zone-style
    background-color=...>`` (Slice D2's ``_append_zone_style``, appended
    LAST per the XSD's zone-style-is-last-child rule) when
    ``header["background"]`` is set.

    Args:
        title:    Header title text (required — callers only invoke this
                  helper when ``title`` is truthy).
        subtitle: Optional subtitle text.
        zone_id:  Deterministic zone id.
        brand:    Optional brand block (``model_dump()`` snake_case dict),
                  same shape ``_resolve_font_spec`` already consumes.
        header:   ``design_theme["header"]`` dict (``ThemeHeaderModel.model_dump()``).
        h:        Zone height on the 0-100000 grid. Defaults to
                  :data:`_HEADER_ZONE_H`.

    Returns:
        A ``<zone>`` ET.Element with ``type-v2='text'``.
    """
    t_bold, t_fontsize, _t_fontcolor, t_fontname = _title_or_subtitle_run_attrs(
        _resolve_font_spec(brand, "title"), default_bold=True, default_fontsize=20
    )
    title_color = header.get("title_color")

    title_run: dict[str, str] = {}
    if t_bold:
        title_run["bold"] = "true"
    if title_color:
        title_run["fontcolor"] = _normalize_hex_color(str(title_color))
    if t_fontname:
        title_run["fontname"] = t_fontname
    title_run["fontsize"] = str(t_fontsize)
    runs: list[tuple[str, dict[str, str]]] = [(title, title_run)]

    if subtitle:
        sep_run: dict[str, str] = {}
        if title_color:
            sep_run["fontcolor"] = _normalize_hex_color(str(title_color))
        runs.append(("Æ  ", sep_run))

        s_bold, s_fontsize, _s_fontcolor, s_fontname = _title_or_subtitle_run_attrs(
            _resolve_font_spec(brand, "body"), default_bold=False, default_fontsize=14
        )
        subtitle_color = header.get("subtitle_color")
        subtitle_run: dict[str, str] = {}
        if s_bold:
            subtitle_run["bold"] = "true"
        if subtitle_color:
            subtitle_run["fontcolor"] = _normalize_hex_color(str(subtitle_color))
        if s_fontname:
            subtitle_run["fontname"] = s_fontname
        subtitle_run["fontsize"] = str(s_fontsize)
        runs.append((subtitle, subtitle_run))

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
    for run_text, run_attrs in runs:
        run_el = ET.SubElement(ft, "run", run_attrs)
        run_el.text = run_text

    if header.get("background"):
        _append_zone_style(zone, {"background-color": str(header["background"])})

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


# Design Excellence, Slice D4 BEAUTY-GATE hotfix (live-probe #5): mined
# verbatim from WB-118's real, published "Superstore Dashboard" (the
# SAME WB-118.twbx exemplar the whole D4 BAN
# mechanism is grafted from) — its KPI band's fixed-size WRAPPER flow
# (e.g. `<zone fixed-size='210' ... is-fixed='true' param='horz'
# type-v2='layout-flow' ...>` wrapping `<zone fixed-size='150' ...
# is-fixed='true' name='Sales KPI (BAN) New' .../>`) is the exact,
# live-probe-CONFIRMED shape (see design/corpus/SCHEMA.md constraint #5's
# resolution). The precise unit semantics of `fixed-size` are not fully
# understood (it is NOT the same 0-100000 percentage scale as `h`/`w`) —
# these two literal values are used because they are PROVEN, not derived.
_KPI_TILE_FIXED_SIZE_WRAPPER = "210"
_KPI_TILE_FIXED_SIZE_LEAF = "150"


def _kpi_wrapped_indices(n: int) -> set[int]:
    """Which of *n* KPI-tile positions get the ``wrap_fixed_size`` cascade.

    Design Excellence, Slice D4 BEAUTY-GATE hotfix, live-probe #5 round 2:
    a real multi-tile KPI band (4 tiles, the exec-audience default) still
    rendered every tile's value as a static ``####`` placeholder — BYTE-
    IDENTICAL across three different BAN font sizes and even across a
    brand-new workbook identity (ruling out both a font-fit issue and a
    render-cache issue) — after wrapping ALL FOUR tiles in the cascade.
    Re-examining the mined exemplar's OWN "4 KPI quadrants" row
    (``WB-118.twbx``, zone id 9) showed it does
    NOT mark every quadrant ``is-fixed``: 3 of its 4 quadrant wrappers
    carry ``is-fixed='true' fixed-size='...'``, but the 3rd one (id 74,
    "Total Orders") has NEITHER attribute — it is the flow's one FLEXIBLE
    sibling. A ``param='horz'`` flow with every child pixel-fixed appears
    to leave Tableau's layout resolver with no free dimension to reconcile
    against the container's own actual rendered width, producing a
    degenerate/placeholder render; at least one flexible sibling is
    required to anchor the remainder.

    Mirrors that: when ``n > 1``, every title EXCEPT THE LAST is wrapped
    (title ``n-1`` stays a plain, unwrapped, non-fixed zone — matching
    V25's proof that a SINGLE (n=1) tile must still be wrapped in full).
    """
    if n <= 1:
        return set(range(n))
    return set(range(n - 1))


def _append_worksheet_zones(
    parent: ET.Element,
    titles: list[str],
    id_start: int,
    zone_style_formats: dict[str, str] | None = None,
    show_title: bool = True,
    wrap_fixed_size: bool = False,
    wrapper_id_start: int | None = None,
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
        show_title:          Design Excellence, Slice D4 FINAL SHAPE. When
                              ``False``, every zone gets ``show-title='false'``
                              — mirrors WB-118's OWN mined KPI-tile zone
                              attribute (``WB-118.twbx``:
                              ``<zone ... name='Sales KPI (BAN) New'
                              show-title='false' ...>``), used when the
                              worksheet's own ``<customized-label>`` already
                              carries an in-label caption (see
                              :func:`_kpi_tile_customized_label`) so the
                              dashboard doesn't show the title twice. Default
                              ``True`` (the attribute is omitted entirely,
                              Tableau's own default): unchanged from before
                              this param existed.
        wrap_fixed_size:      Design Excellence, Slice D4 BEAUTY-GATE hotfix
                              (live-probe #5). When ``True``, EVERY zone is
                              wrapped in an intermediate ``is-fixed='true'
                              fixed-size='210'`` ``<zone type-v2='layout-flow'
                              param='horz'>``, with the worksheet zone itself
                              ALSO carrying ``is-fixed='true'
                              fixed-size='150'`` — mined verbatim from
                              WB-118's real KPI band. A user reported "I
                              can't see the numbers" in the INTERACTIVE
                              browser view (not just static image exports);
                              the untouched exemplar workbook rendered its
                              BANs PERFECTLY on the SAME Tableau Cloud site,
                              proving the failure was OUR dashboard zone
                              composition, not a renderer/site limitation.
                              An exhaustive zone-tree diff plus a live-probe
                              bisect ladder (V13–V25) isolated THIS
                              is-fixed/fixed-size CASCADE (both the wrapper
                              AND the leaf, not either alone — V20 tested
                              leaf-only and it did not help) as the fix.
                              Default ``False``: unchanged from before this
                              param existed — callers must opt in explicitly
                              (currently only the KPI band in
                              ``kpi_band_over_charts``; chart zones already
                              render correctly without it and are left
                              unchanged).
        wrapper_id_start:     First id to allocate for the ``wrap_fixed_size``
                              wrapper zones, one per WRAPPED tile (see
                              ``_kpi_wrapped_indices`` below — NOT
                              necessarily one per title), sequential.
                              REQUIRED when ``wrap_fixed_size=True`` — callers
                              must source these from the SAME id counter used
                              for every other zone in the dashboard (e.g. the
                              caller's ``_next_id()`` closure) so the wrapper
                              ids can never collide with header/footer text
                              zones, sub-flow containers, or worksheet zone
                              ids. Ignored when ``wrap_fixed_size=False``.

    Raises:
        ValueError: if ``wrap_fixed_size=True`` and ``wrapper_id_start`` is
            not supplied.
    """
    n = len(titles)
    if n == 0:
        return
    if wrap_fixed_size and wrapper_id_start is None:
        raise ValueError("wrapper_id_start is required when wrap_fixed_size=True")
    GRID = 100000
    unit_w = GRID // n
    wrapped_indices = _kpi_wrapped_indices(n) if wrap_fixed_size else set()
    next_wrapper_id = wrapper_id_start if wrapper_id_start is not None else 0
    for i, title in enumerate(titles):
        w = unit_w if i < n - 1 else GRID - (n - 1) * unit_w
        wrap_this = i in wrapped_indices
        target_parent = parent
        zone_attrs: dict[str, str] = {}
        if wrap_this:
            zone_attrs["fixed-size"] = _KPI_TILE_FIXED_SIZE_LEAF
        zone_attrs["h"] = "100000"
        zone_attrs["id"] = str(id_start + i)
        if wrap_this:
            zone_attrs["is-fixed"] = "true"
        zone_attrs["name"] = title
        if not show_title:
            zone_attrs["show-title"] = "false"
        if wrap_this:
            # The leaf now fills its OWN wrapper entirely (w=100000 of the
            # wrapper's local space) — the wrapper carries the tile's real
            # x/w allocation instead (mirrors the mined nesting exactly).
            wrapper_attrs = {
                "fixed-size": _KPI_TILE_FIXED_SIZE_WRAPPER,
                "h": "100000",
                "id": str(next_wrapper_id),
                "is-fixed": "true",
                "param": "horz",
                "type-v2": "layout-flow",
                "w": str(w),
                "x": str(i * unit_w),
                "y": "0",
            }
            next_wrapper_id += 1
            target_parent = ET.SubElement(parent, "zone", wrapper_attrs)
            zone_attrs.update({"w": "100000", "x": "0", "y": "0"})
        else:
            zone_attrs.update({"w": str(w), "x": str(i * unit_w), "y": "0"})
        ws_zone = ET.SubElement(target_parent, "zone", zone_attrs)
        ET.SubElement(
            ws_zone,
            "layout-cache",
            {"cell-count-h": "1", "cell-count-w": "1", "type-h": "cell", "type-w": "cell"},
        )
        if zone_style_formats:
            _append_zone_style(ws_zone, zone_style_formats)


# ---------------------------------------------------------------------------
# Chrome rules + mark labels (Design Excellence, Slice D3)
#
# Mirrors the mined vocabulary in ``design/corpus/recipes/chrome_rules.yaml``:
# ``<style><style-rule element='...'><format attr='...' value='...'/></style-rule></style>``
# at three distinct XSD locations:
#   - workbook-level  (``Workbook-Styles-G``, optional — omitted entirely
#     when there is nothing to say)
#   - worksheet TABLE-level  (``Workbook-Stylesheet-G`` on ``Table-CT`` —
#     REQUIRED even when empty; the pre-D3 bare ``<style/>`` stays
#     byte-identical when there is nothing to say)
#   - worksheet PANE-level  (``PaneSpecification-G``'s ``Stylesheet-G``,
#     optional, and MUST be the LAST child of ``<pane>``)
# Every helper here is a pure formats-mapper; ``_build_worksheet``/
# ``build_twb_xml``/``build_embedded_twb_xml`` decide WHERE to attach the
# result and whether the calling context is eligible (see
# ``_is_labelable_chart_sheet``).
# ---------------------------------------------------------------------------


def _build_style_element(rules: list[tuple[str, dict[str, str]]]) -> ET.Element:
    """Return a standalone ``<style>`` element built from *rules*.

    Each ``(element, formats)`` pair becomes a ``<style-rule element='...'>``
    with one ``<format attr='...' value='...'/>`` per *formats* key, emitted
    in sorted (alphabetical) order for deterministic output — same discipline
    as ``_append_zone_style`` (Slice D2). An empty *rules* list returns a
    childless (self-closing) ``<style/>`` — the exact shape the pre-D3 bare
    ``ET.SubElement(table, "style")`` produced, preserving byte-identical
    output at call sites where ``<style>`` is structurally REQUIRED even when
    there is nothing themed to say (worksheet table-level).
    """
    style_el = ET.Element("style")
    for element, formats in rules:
        rule_el = ET.SubElement(style_el, "style-rule", {"element": element})
        for attr in sorted(formats):
            ET.SubElement(rule_el, "format", {"attr": attr, "value": formats[attr]})
    return style_el


def _append_style_rules(parent: ET.Element, rules: list[tuple[str, dict[str, str]]]) -> None:
    """Append a ``<style>`` child (via :func:`_build_style_element`) to *parent*.

    A no-op when *rules* is empty — for XSD contexts where ``<style>`` itself
    is OPTIONAL (workbook-level ``Workbook-Stylesheet-G`` and pane-level
    ``Stylesheet-G``, both ``minOccurs='0'``). Worksheet TABLE-level
    ``<style>`` is REQUIRED even when empty (no ``minOccurs`` on its
    ``Workbook-Stylesheet-G`` reference) — callers there must append
    :func:`_build_style_element`'s result directly and unconditionally
    instead of going through this helper.
    """
    if not rules:
        return
    parent.append(_build_style_element(rules))


def _workbook_style_rules(chrome: dict[str, Any] | None) -> list[tuple[str, dict[str, str]]]:
    """Return workbook-level ``(element, formats)`` pairs from ``design_theme.chrome``.

    ``hide_gridlines``/``hide_zeroline`` each map to a plain
    ``line-visibility='off'`` format — mirrors ``WB-062``
    ``/workbook/style/style-rule[2]`` (gridline) and ``WB-095``
    ``/workbook/style/style-rule[2]`` (zeroline): a single workbook-wide rule
    rather than repeating the same rule on every worksheet.

    ``title_color`` additionally emits a ``title`` style-rule with a plain
    ``color`` format — mirrors WB-117's
    ``WB-117.twbx`` ``/workbook/style/style-rule[1]``
    (``font-size='11' color='#2f2e41'``; only ``color`` is emitted here since
    no theme field for title font-size exists). ``title_color`` is NOT a
    field on Slice D1's ``ThemeChromeModel`` — it is read defensively via
    ``.get()`` so this is a no-op through the current Pydantic/zod wire
    schema; only a caller passing a raw ``design_theme`` dict directly to
    this builder module can reach it today (builder-only support; the
    Pydantic model + zod schema wiring is deferred — see the D3 report).

    Returns pairs sorted by element name for deterministic output. Returns an
    empty list when *chrome* is ``None``/falsy or every flag is unset — the
    byte-identical guard: a ``design_theme`` with no chrome intent never adds
    a workbook-level ``<style>``.
    """
    if not chrome:
        return []
    rules: dict[str, dict[str, str]] = {}
    if chrome.get("hide_gridlines"):
        rules["gridline"] = {"line-visibility": "off"}
    if chrome.get("hide_zeroline"):
        rules["zeroline"] = {"line-visibility": "off"}
    title_color = chrome.get("title_color")
    if title_color:
        rules["title"] = {"color": str(title_color)}
    return [(element, rules[element]) for element in sorted(rules)]


def _theme_datalabel_formats(datalabel: dict[str, Any] | None) -> dict[str, str]:
    """Map ``design_theme.chrome.datalabel`` onto the mined datalabel vocabulary.

    Mirrors WB-117's ``worksheet[15]/table/panes/pane/style/style-rule[2]``
    (``color-mode='auto' font-weight='bold' font-family='Calibri'``):
    ``font_size`` -> ``font-size``, ``font_weight`` -> ``font-weight``,
    ``color_mode`` -> ``color-mode``. ``color`` is read defensively via
    ``.get()`` (NOT a field on Slice D1's ``ThemeDatalabelModel``) — same
    builder-only-support discipline as ``title_color`` above. Unset keys are
    omitted entirely — never written as an empty-string placeholder.
    """
    dl = datalabel or {}
    formats: dict[str, str] = {}
    if dl.get("font_size") is not None:
        formats["font-size"] = str(dl["font_size"])
    if dl.get("font_weight"):
        formats["font-weight"] = str(dl["font_weight"])
    if dl.get("color_mode"):
        formats["color-mode"] = str(dl["color_mode"])
    if dl.get("color"):
        formats["color"] = str(dl["color"])
    return formats


def _pane_style_rules(chrome: dict[str, Any] | None) -> list[tuple[str, dict[str, str]]]:
    """Return pane-level ``(element, formats)`` pairs: ``datalabel`` THEN ``mark``.

    Mirrors the mined shape at ``.../table/panes/pane/style/style-rule`` where
    BOTH ``datalabel`` and ``mark`` rules live in the SAME ``<pane><style>``
    block, datalabel first (WB-117 worksheet[10]/[15] and
    WB-114 worksheet[8]/[15] all place the datalabel
    style-rule immediately before the mark style-rule).

    ``chrome.show_mark_labels`` emits ``mark-labels-show='true'`` +
    ``mark-labels-cull='false'`` — readable, uncrowded bar/line labels.
    Callers MUST gate this function behind :func:`_is_labelable_chart_sheet`;
    it performs no sheet-kind scoping itself (pure formats-mapper).

    Returns an empty list when *chrome* is ``None``/falsy or neither
    ``show_mark_labels`` nor any datalabel field is set.
    """
    if not chrome:
        return []
    rules: list[tuple[str, dict[str, str]]] = []
    datalabel_formats = _theme_datalabel_formats(chrome.get("datalabel"))
    if datalabel_formats:
        rules.append(("datalabel", datalabel_formats))
    if chrome.get("show_mark_labels"):
        rules.append(("mark", {"mark-labels-show": "true", "mark-labels-cull": "false"}))
    return rules


def _is_labelable_chart_sheet(sheet: dict[str, Any]) -> bool:
    """True for ``kind='chart'`` sheets whose ``mark_type`` is ``bar`` or ``line``.

    Design Excellence, Slice D3 scoping decision: mined
    ``design/corpus/recipes/chrome_rules.yaml`` evidence for
    ``mark-labels-show`` on MAP worksheets is genuinely mixed across the
    reference workbooks (some map layers carry ``mark-labels-show='true'``,
    others ``'false'``), so per the D3 plan's explicit fallback instruction
    ("if ambiguous, apply to bar/line only — Few discipline: labels on bars,
    not on maps") theme-driven mark-labels/datalabel styling is restricted to
    bar/line chart sheets. This naturally excludes ``kpi_tile`` sheets
    (``kind != 'chart'``; their hero ``<text>`` marks are not "labels" in
    this sense — KPI styling lands in Slice D4) and ``map_filled`` sheets
    (``mark_type == 'map_filled'``, not in the allowed set), as well as
    scatter/text mark types.
    """
    # NOTE: ``sheet.get("kind") or "chart"`` (not ``sheet.get("kind", "chart")``)
    # — after a Pydantic ``SheetModel.model_dump()`` round-trip (the real
    # ``/workbook/dashboard`` request path) the "kind" key is ALWAYS present,
    # with value ``None`` when the caller didn't set it. ``.get(key, default)``
    # only substitutes the default for an ABSENT key, so it would silently
    # read ``None`` here for the common case (an ordinary bar/line sheet with
    # no explicit ``kind``) — the ``or`` form treats both "absent" and
    # "present but falsy" as the "chart" default, matching caller intent.
    sheet_kind = str(sheet.get("kind") or "chart")
    mark_type = str(sheet.get("mark_type") or "bar").lower()
    return sheet_kind == "chart" and mark_type in ("bar", "line")


def _table_style_rules(
    chrome: dict[str, Any] | None,
    sheet_style_rules: list[dict[str, Any]] | None,
) -> list[tuple[str, dict[str, str]]]:
    """Return worksheet TABLE-level ``(element, formats)`` pairs.

    Order: the theme-driven ``axis`` tick-color rule first (when
    ``chrome.hide_axis_ticks`` is set), then any ``SheetModel.style_rules``
    pass-through entries (Slice D1's per-sheet escape hatch — carried
    verbatim), sorted by ``element`` name for deterministic output.

    The axis rule mirrors WB-117's
    ``worksheet[10]/table/style/style-rule[1]`` EXACTLY:
    ``line-visibility='off' tick-color='#00000000'`` (fully transparent
    ticks). Unlike :func:`_pane_style_rules`, this is NOT scoped to
    bar/line charts — ``hide_axis_ticks`` is a blanket chrome setting that is
    harmless on sheet kinds with no visible axis (e.g. KPI tiles).

    ``sheet_style_rules`` works independently of ``chrome``/``design_theme``
    — a sheet can carry ``style_rules`` even when no design theme is applied
    at all.
    """
    rules: list[tuple[str, dict[str, str]]] = []
    if chrome and chrome.get("hide_axis_ticks"):
        rules.append(("axis", {"line-visibility": "off", "tick-color": "#00000000"}))
    passthrough = sorted(sheet_style_rules or [], key=lambda r: str(r.get("element") or ""))
    for rule in passthrough:
        element = str(rule.get("element") or "")
        formats = {str(k): str(v) for k, v in (rule.get("formats") or {}).items()}
        if element and formats:
            rules.append((element, formats))
    return rules


# ---------------------------------------------------------------------------
# Brand sequential palette on filled-map color encodings (Design Excellence,
# Slice D6)
# ---------------------------------------------------------------------------


def _map_filled_palette_style_rule(field_column: str, colors: list[str]) -> ET.Element:
    """Return a ``<style-rule element='mark'><encoding attr='color'
    type='custom-interpolated'><color-palette .../></encoding></style-rule>``.

    FINAL SHAPE (live-probe #4): mirrors ``WB-015``'s own
    ``<table><style><style-rule element='mark'><encoding attr='color'
    field='[Sample - Superstore].[usr:Calculation_...:qk]'
    type='custom-interpolated'><color-palette custom='true' name=''
    type='ordered-sequential'><color>#f1f1f1</color>...</color-palette>
    </encoding></style-rule>`` attribute-for-attribute: ``<encoding>``
    carries only ``attr``/``field``/``type`` (alphabetical; NO ``center``/
    ``num-steps``/``max``/``min``/``reverse`` — those only appear on the
    corpus's DIVERGING encodings, which need a two-sided midpoint; a
    one-directional sequential ramp mirrors the sequential exemplar exactly,
    carrying none of them), and the nested ``<color-palette>`` carries
    ``custom``/``name``/``type`` with an EMPTY ``name=''`` (verified: every
    one of the 8 mined embedded-palette encodings across ``WB-015``
    and ``WB-062``/``WB-063`` uses ``name=''`` — the inline
    override is never itself given a reusable name). No ``enable-transparency``
    or other extra attribute appears anywhere in the mined exemplars (grepped
    across all 4 scratchpad-restored corpus source files) — none is invented
    here either.

    SUPERSEDES the ``palette='<brandName> Sequential'`` attribute-reference
    shape shipped in the first D6 pass: live-probe #4 published that shape
    and rendered it on Tableau Cloud — the workbook published and rendered
    without error, but the map's color ramp was BYTE-IDENTICAL to the
    pre-D6 (unbranded) render, i.e. Cloud silently ignores a ``palette=``
    NAME reference for a continuous measure's ``<encoding attr='color'>``
    (that construct's only verified real-world use, ``WB-058``'s
    ``palette='miller_stone_10_0'``, is a DISCRETE bucket-map encoding, not
    a continuous ramp — the two are not interchangeable on Cloud). This is
    the 8x-attested embedded-color-stops construct instead: duplicates the
    brand's sequential stops directly in the worksheet's own style-rule
    (rather than referencing the ``<preferences>`` registration by name),
    which is exactly what every real mined exemplar does.

    The ``<preferences>`` registration (:func:`_build_preferences_element`)
    is KEPT as-is — it is a harmless, independently-useful custom-palette
    declaration (selectable from Tableau Desktop's palette picker) even
    though this per-encoding override no longer references it by name.
    """
    rule_el = ET.Element("style-rule", {"element": "mark"})
    encoding_el = ET.SubElement(
        rule_el,
        "encoding",
        {"attr": "color", "field": field_column, "type": "custom-interpolated"},
    )
    _append_color_palette(encoding_el, name="", palette_type="ordered-sequential", colors=colors)
    return rule_el


def _map_filled_sequential_colors(brand: dict[str, Any] | None) -> list[str] | None:
    """Return ``brand.palette.sequential`` (hex strings) when non-empty, else ``None``.

    ``None`` when ``brand`` is absent or its sequential list is empty —
    callers use this as the single gate for whether the map-filled color
    encoding gets an embedded ``<color-palette>`` override at all (the OTHER
    gate, ``design_theme is not None``, is checked separately by the caller
    — see :func:`_build_worksheet`).
    """
    if not brand:
        return None
    sequential = [str(c) for c in ((brand.get("palette") or {}).get("sequential") or [])]
    return sequential or None


# ---------------------------------------------------------------------------
# Styled KPI tiles (Design Excellence, Slice D4; FINAL SHAPE after a
# live-probe #3 offline bisect ladder, V0-V12)
#
# Fixes live-probe findings on the KPI band across FOUR probe rounds: (a)
# numeric overflow (### in narrow tiles) via a compact number/currency
# format; (b) illegible dark-on-navy titles via a per-worksheet title-color
# rule (later superseded, see #4 below); (c) unstyled tiles via the D2
# zone-style box model, now also applied to kpi_tile zones; (d) a
# table-level opaque-white fill hiding the tile zone's themed background;
# (e) BAN typography/compact-formats not rendering AT ALL on Tableau Cloud,
# despite three independently-plausible-looking mechanisms in a row (a
# TABLE-level cell rule, then a WORKSHEET-LOCAL raw-field default-format +
# a customized-label with brand-driven styling) — root-caused only by an
# offline bisect ladder (a dozen ``.twbx`` variants, V0-V12, isolating one
# variable at a time against a verbatim graft of WB-118's real, published
# "Sales KPI (BAN) New" worksheet, WB-118.twbx).
#
# FOUR mined locations, each covering a DIFFERENT concern (see each
# helper's own docstring for the full root-cause writeup of why it lives
# where it does):
#
# 1. NUMBER FORMATTING (compact primary + delta arrow-direction): a plain
#    ``default-format`` attribute on a worksheet-local CALCULATED column's
#    OWN ``<datasource-dependencies><column>`` declaration
#    (:func:`_append_kpi_ban_calc_column`) — NOT the raw field's local
#    column (an earlier hotfix round's mechanism, PROVEN inert by the
#    bisect ladder's V7: Cloud silently ignores a default-format on a raw
#    field's local dependency column for a naked BAN view). This value is
#    what a ``customized-label`` placeholder run (below) ultimately renders.
# 2. BAN TYPOGRAPHY + the label's run STRUCTURE itself: the PANE's own
#    ``<customized-label>`` — :func:`_kpi_tile_customized_label`. Mirrors
#    WB-118's "Sales KPI (BAN) New" pane structure (``view, mark,
#    encodings, customized-label, style``) AND its run idiom (caption run,
#    glyph-prefixed newline runs, CDATA placeholders) verbatim — the bisect
#    ladder proved partial adoption of this shape (e.g. mark-labels-show
#    added to an otherwise-different run structure) renders a BLANK mark,
#    worse than not adopting it at all.
# 3. TABLE/PANE CHROME: a transparent TABLE-level ``background-color``
#    (:func:`_kpi_tile_table_transparency_rule`) so the zone's themed
#    background shows through, plus a PANE-level ``text-align: center`` AND
#    (FINAL SHAPE) ``mark-labels-show``/``mark-labels-cull`` rule
#    (:func:`_kpi_tile_pane_style_rules`) — REQUIRED for the
#    customized-label to render at all, not merely cosmetic. All mined
#    verbatim from the SAME WB-118 worksheet.
# 4. TITLE SUPPRESSION: since #2's label now carries an in-label caption,
#    the per-worksheet title-color rule (:func:`_kpi_tile_title_style_rule`)
#    is skipped, and the dashboard zone hosting the tile gets
#    ``show-title='false'`` (:func:`_append_worksheet_zones`) — mirrors
#    WB-118's own mined KPI-tile zone attribute.
# ---------------------------------------------------------------------------

# Mined compact-number pattern (WB-015, /workbook/worksheets/
# worksheet[5]/table/style/style-rule[2], element='cell' attr='text-format',
# no field= -- see design/corpus/recipes/chrome_rules.yaml).
_KPI_COMPACT_NUMBER_FORMAT = "n#,##0,.0K;-#,##0,.0K"

# Mined delta arrow-direction pattern (WB-117's
# WB-117.twbx, default-format='*▲ #,##;▼ #,##'
# on [MOM - Sales (copy)_233624247371403264] et al.) -- the D7-deferred
# fallback for true color-by-sign (see _append_kpi_ban_calc_column's
# docstring). Direction is visible (▲/▼) without any new calc-field
# machinery beyond the BAN mechanism itself.
_KPI_DELTA_ARROW_FORMAT = "*▲ #,##;▼ #,##"

_CURRENCY_SYMBOL_RE = re.compile(r"^([^#0-9]*)")


def _extract_currency_symbol(currency_format: str) -> str:
    """Return the leading currency-symbol prefix of a Tableau currency format,
    with any pre-existing quote-wrapping stripped.

    E.g. ``"$#,##0"`` -> ``"$"``, and the Tableau-native quoted form
    ``'"$"#,##0'`` (symbol already wrapped in literal quote characters,
    matching real mined ``default-format`` values like
    ``c"$"#,##0;-"$"#,##0``) -> ALSO ``"$"``. Falls back to ``"$"`` when the
    format has no leading non-numeric prefix (defensive; brand.formats.currency
    is expected to always have one).

    Design Excellence, Slice D4 hotfix (live-probe #2b): the regex prefix
    match (``^[^#0-9]*``) does not stop at a literal ``"`` — a brand currency
    string already in Tableau-native quoted form (``'"$"#,##0'``) would
    otherwise be captured AS-IS (quote characters included), and
    :func:`_compact_currency_format` would then wrap THAT in a second pair of
    quotes (``c""$""#,##0...``), producing a format string Tableau cannot
    parse (silently falls back, leaving the un-compacted default and the
    overflow this whole mechanism exists to fix). Stripping any leading/
    trailing ``"`` from the extracted prefix before wrapping guarantees
    exactly ONE quote pair in the final pattern, regardless of which
    convention the input already used.
    """
    match = _CURRENCY_SYMBOL_RE.match(currency_format)
    symbol = match.group(1) if match else ""
    symbol = symbol.strip('"')
    return symbol or "$"


def _compact_currency_format(currency_format: str) -> str:
    """Return the mined compact-currency pattern for *currency_format*'s symbol.

    Mirrors WB-117's ``c"R$ "#,##0,.0K;-"R$ "#,##0,.0K`` and WB-118's
    ``c"$"#,##0,.0K;-"$"#,##0,.0K`` (WB-118.twbx)
    -- the ONLY parameterization this builder permits is the currency SYMBOL
    itself; the surrounding ``#,##0,.0K`` compaction grammar is copied
    verbatim (Tableau's format-code grammar is opaque to us — same discipline
    as :func:`classify_measure_format`).
    """
    symbol = _extract_currency_symbol(currency_format)
    return f'c"{symbol}"#,##0,.0K;-"{symbol}"#,##0,.0K'


def _kpi_compact_format(field_name: str, formats: dict[str, Any]) -> str:
    """Return the compact ``text-format`` value for a KPI tile's PRIMARY measure.

    Uses the SAME word-hint classification as :func:`classify_measure_format`
    (currency/percent/number), but a DIFFERENT, per-worksheet output — this
    must never be written to the datasource-GLOBAL ``default-format`` (that
    would change :func:`classify_measure_format`'s existing, already-shipped
    behavior for every OTHER sheet referencing the same measure).

    - currency-hinted -> the mined compact-currency pattern
      (:func:`_compact_currency_format`), symbol parameterized from
      ``formats["currency"]``.
    - percent-hinted -> UNCHANGED (:func:`classify_measure_format`'s own
      percent output) — percents don't overflow a ~250px tile, so no K-suffix
      compaction is applied.
    - everything else -> :data:`_KPI_COMPACT_NUMBER_FORMAT`.
    """
    words = set(re.findall(r"[a-z]+", field_name.lower()))
    if words & _PERCENT_MEASURE_HINTS:
        return classify_measure_format(field_name, formats)
    if words & _CURRENCY_MEASURE_HINTS:
        currency_format = str(formats.get("currency") or _DEFAULT_CURRENCY_FORMAT)
        return _compact_currency_format(currency_format)
    return _KPI_COMPACT_NUMBER_FORMAT


def _kpi_tile_zone_style_formats(kpi_tile: dict[str, Any] | None) -> dict[str, str]:
    """Map ``design_theme["kpi_tile"]`` onto the mined zone-style vocabulary.

    Design Excellence, Slice D4. Same attribute-mapping discipline as
    :func:`_chart_card_zone_style_formats` (Slice D2), applied to
    ``ThemeKpiTileModel``: ``background`` -> ``background-color``,
    ``border.{color,style,width}`` -> ``border-{color,style,width}``,
    ``padding`` -> ``padding``. ``ThemeKpiTileModel`` carries no
    ``margin``/``corner_radius`` fields (unlike ``chart_card``), so those
    attrs are never emitted here. Unset keys are omitted entirely — never
    written as an empty-string or zero placeholder.
    """
    formats: dict[str, str] = {}
    tile = kpi_tile or {}

    background = tile.get("background")
    if background:
        formats["background-color"] = str(background)

    border = tile.get("border") or {}
    if border.get("color"):
        formats["border-color"] = str(border["color"])
    if border.get("style"):
        formats["border-style"] = str(border["style"])
    if border.get("width") is not None:
        formats["border-width"] = str(border["width"])

    if tile.get("padding") is not None:
        formats["padding"] = str(tile["padding"])

    return formats


def _kpi_tile_title_style_rule(
    kpi_tile: dict[str, Any] | None,
) -> tuple[str, dict[str, str]] | None:
    """Return a worksheet-scoped ``("title", {"color": ...})`` rule, or ``None``.

    Design Excellence, Slice D4 — fixes live-probe #1 finding (b): KPI title
    labels dark-on-navy. Mirrors the mined PER-WORKSHEET (not workbook-level)
    ``element='title'`` table-level style-rule location:
    ``/workbook/worksheets/worksheet[9]/table/style/style-rule[3]``
    (attr='color') and ``worksheet[1]/table/style/style-rule[4]``
    (attr='font-family') in ``design/corpus/recipes/chrome_rules.yaml`` —
    confirming a per-worksheet ``element='title'`` rule is a real, mined
    construct, distinct from D3's WORKBOOK-level ``_workbook_style_rules``
    (``chrome.title_color``), which would incorrectly recolor every
    worksheet's title in the workbook, not just this KPI tile's. Scoping to
    the worksheet's OWN table-level ``<style>`` keeps chart worksheets on the
    light canvas unaffected.

    Returns ``None`` when *kpi_tile* is falsy or ``ban_color`` is unset.
    """
    tile = kpi_tile or {}
    ban_color = tile.get("ban_color")
    if not ban_color:
        return None
    return ("title", {"color": str(ban_color)})


def _kpi_tile_table_transparency_rule(
    kpi_tile: dict[str, Any] | None,
) -> tuple[str, dict[str, str]] | None:
    """Return a worksheet-scoped ``("table", {"background-color": "#00000000"})``
    rule, or ``None``.

    Design Excellence, Slice D4 hotfix (live-probe #2b finding 3 — "white
    tiles"): the D2 zone-style ``background-color`` on the KPI tile's ZONE
    renders correctly, but the worksheet's own TABLE has an opaque white
    fill of its own that paints on top of it, hiding the navy band. Mirrors
    WB-118's real, published ``"Sales KPI (BAN) New"`` worksheet
    (WB-118.twbx) verbatim:
    ``<style-rule element='table'><format attr='background-color'
    value='#00000000'/></style-rule>`` — a fully transparent table fill so
    the containing zone's background shows through.

    Returns ``None`` when *kpi_tile* is falsy or ``background`` is unset —
    only relevant when the tile actually has a themed background to reveal.
    """
    tile = kpi_tile or {}
    if not tile.get("background"):
        return None
    return ("table", {"background-color": "#00000000"})


def _kpi_tile_pane_style_rules(
    kpi_tile: dict[str, Any] | None,
    kpi_ban_active: bool = False,
) -> list[tuple[str, dict[str, str]]]:
    """Return PANE-level ``(element, formats)`` pairs for a themed KPI tile.

    Design Excellence, Slice D4 hotfix (live-probe #2b finding 4, mined
    verbatim from WB-118's real "Sales KPI (BAN) New" worksheet's OWN
    ``<table><panes><pane><style>``): ``<style-rule element='cell'>
    <format attr='text-align' value='center'/></style-rule>`` — centers the
    BAN text within the tile. This is a PANE-scoped ``element='cell'`` rule
    (``<panes><pane><style>``), a DIFFERENT XSD location from the
    (now-removed) TABLE-scoped ``element='cell'`` rule that used to live at
    ``<table><style>`` — both legitimately share the ``cell`` element name
    at their own scope. Emitted whenever ``kpi_tile`` theming is active
    (``design_theme.kpi_tile`` present), independent of ``ban_color``/
    typography specifics.

    Design Excellence, Slice D4 FINAL SHAPE (live-probe #3's bisect ladder,
    V9-V12): a SECOND rule, ``<style-rule element='mark'><format
    attr='mark-labels-show' value='true'/><format attr='mark-labels-cull'
    value='true'/></style-rule>``, is appended whenever *kpi_ban_active* is
    true — mined verbatim from the SAME worksheet's pane ``<style>`` (it
    carries BOTH rules). V9/V10 proved this rule is NECESSARY for the
    ``<customized-label>`` to render at all on Tableau Cloud (its absence
    silently drops the label back to the plain default text-mark; V9/V10
    themselves, missing one other proven-shape trait, went further and
    rendered a BLANK mark instead — mark-labels-show suppressed the
    fallback too) though not sufficient alone; V11/V12 (the full
    graft-derived shape) confirmed it IS sufficient combined with the rest
    of :func:`_kpi_tile_customized_label`'s run idiom. Gated separately
    from the ``cell``/``text-align`` rule above (which stays independent of
    whether the label mechanism specifically is active) because a KPI tile
    with ``kpi_tile`` theming but no working label (e.g. no
    ``primary_measure``) has no mark labels to show.

    Returns an empty list when *kpi_tile* is falsy.
    """
    if not kpi_tile:
        return []
    rules: list[tuple[str, dict[str, str]]] = [("cell", {"text-align": "center"})]
    if kpi_ban_active:
        rules.append(("mark", {"mark-labels-show": "true", "mark-labels-cull": "true"}))
    return rules


_KPI_BAN_CALC_PREFIX = "Calculation_BAN_"


def _append_kpi_ban_calc_column(
    deps: ET.Element,
    field: str,
    *,
    suffix: str,
    caption: str,
    default_format: str,
) -> str:
    """Append a worksheet-local CALCULATED ``<column>`` + ``<column-instance>``
    pair to *deps* (a worksheet's own ``<datasource-dependencies>``), and
    return its fully-qualified instance name fragment (e.g.
    ``"[usr:Calculation_BAN_Sales:qk]"``).

    Design Excellence, Slice D4 FINAL SHAPE — replaces the earlier
    ``_kpi_tile_local_default_formats`` mechanism (stamping ``default-format``
    directly on a RAW field's worksheet-local ``<column>``), which
    live-probe #3's bisect ladder (V7) proved Tableau Cloud silently
    IGNORES for a naked BAN view. A CALCULATED field's own local
    ``default-format`` is NOT ignored (V7/V11/V12 all confirmed the exact
    formatted value renders). Mirrors WB-118's real, mined calc-column
    shape verbatim (verified directly against their own dependency block,
    e.g. their "Sales % Chg" calc)::

        <column caption="..." datatype="real" default-format="..."
                name="[Calculation_BAN_<field>[suffix]]" role="measure"
                type="quantitative">
          <calculation class="tableau" formula="SUM([<field>])" />
        </column>
        <column-instance column="[Calculation_BAN_<field>[suffix]]"
                          derivation="User"
                          name="[usr:Calculation_BAN_<field>[suffix]:qk]"
                          pivot="key" type="quantitative" />

    ``derivation="User"`` (the ``usr:`` instance prefix) mirrors WB-118's
    OWN convention, verified against two real examples in the SAME
    worksheet: a calc whose formula does NOT already aggregate gets
    ``derivation='Sum'`` (``sum:`` prefix); a calc whose formula ALREADY
    contains an aggregation function (as ``SUM([field])`` always does) gets
    ``derivation='User'`` (``usr:`` prefix).

    Args:
        deps:            The worksheet's ``<datasource-dependencies>`` element.
        field:           The RAW field name the calc sums (e.g. ``"Sales"``).
        suffix:          Appended to the calc's field name for uniqueness —
                         ``""`` for the primary BAN value, ``"_Delta"`` for
                         the delta value (so a sheet whose primary and delta
                         happen to reference the same raw field never
                         collides).
        caption:         Human-readable caption for the calc column.
        default_format:  The compact/arrow ``default-format`` value.

    Returns:
        The instance name fragment, e.g. ``"[usr:Calculation_BAN_Sales:qk]"``
        (unqualified — same contract as :func:`_measure_instance`; callers
        prefix with ``f"{ds_ref}."``).
    """
    calc_field_name = f"{_KPI_BAN_CALC_PREFIX}{_slug(field)}{suffix}"
    column_el = ET.SubElement(
        deps,
        "column",
        {
            "caption": caption,
            "datatype": "real",
            "default-format": default_format,
            "name": f"[{calc_field_name}]",
            "role": "measure",
            "type": "quantitative",
        },
    )
    ET.SubElement(column_el, "calculation", {"class": "tableau", "formula": f"SUM([{field}])"})
    instance_name = f"[usr:{calc_field_name}:qk]"
    ET.SubElement(
        deps,
        "column-instance",
        {
            "column": f"[{calc_field_name}]",
            "derivation": "User",
            "name": instance_name,
            "pivot": "key",
            "type": "quantitative",
        },
    )
    return instance_name


# Design Excellence, Slice D4 FINAL SHAPE — customized-label run-idiom
# constants, all taken VERBATIM from the bisect ladder's proven-working
# shape (V0's graft, confirmed rendering by V11/V12 combined with calc
# columns + mark-labels-show/cull + selection-relaxation — see
# _kpi_tile_pane_style_rules/_append_kpi_ban_calc_column's docstrings and
# the module-level "BAN mechanism" comment block above
# _KPI_COMPACT_NUMBER_FORMAT for the full ladder provenance).
_KPI_LABEL_CAPTION_FONTSIZE = 7
_KPI_LABEL_VALUE_FONTSIZE_FALLBACK = 17
# Live-probe #2e finding: the full pipeline's label mechanism WORKS end to
# end, but brand.yaml's ban.size=36 overflows the ~240px KPI tile cell —
# the value renders as "###" (a Tableau cell-overflow placeholder, not a
# label failure). V12's own tested value (17) rendered correctly; the
# largest BAN fontsize anywhere in the 10-workbook mined corpus is 26
# (WB-117's WB-117.twbx). The PRIMARY
# value run's fontsize is therefore clamped to this mined maximum whenever
# brand.typography.ban.size is set (brand can still request anything up to
# 26; larger requests are capped, not rejected) — see
# _kpi_tile_customized_label's docstring.
_KPI_LABEL_VALUE_FONTSIZE_MAX = 26
# Not clamped: at 12px, an arrow-formatted delta value ("▲ 281,016", ~9
# characters) comfortably fits the same ~240px tile that overflows at
# 36px/primary-length content — left as a fixed value per live-probe #2e's
# review, not overflow-safe by explicit design (unlike the primary run).
_KPI_LABEL_DELTA_FONTSIZE = 12
_KPI_LABEL_DEFAULT_VALUE_COLOR = "#555555"
# The mined literal "Æ" glyph-prefixed newline run text — undocumented
# Tableau Desktop label-editor semantics, but PROVEN part of the working
# shape (V9/V10 showed a plain "\n" substitute does NOT render; V1's
# fontalignment/trailing-run addition alone did not fix a plain-"\n" label
# either — only the graft's OWN literal runs, verbatim, render).
_KPI_LABEL_NEWLINE_GLYPH = "Æ\n"  # "Æ\n"
_KPI_LABEL_CAPTION_SEPARATOR = _KPI_LABEL_NEWLINE_GLYPH + "\n"  # "Æ\n\n"

# ElementTree cannot emit CDATA natively (ET.tostring always escapes "<"/
# ">" inside element text). A placeholder run's text is written as this
# plain, XML-safe sentinel-wrapped marker during tree construction, then
# _finalize_xml's regex substitution (run AFTER ET.tostring has already
# escaped everything ELSE correctly) converts it into a real
# <![CDATA[<...>]]> section, matching WB-118's mined placeholder form.
# Two-pass technique proven in the offline bisect ladder (V0/V3/V6/V9-V12,
# .../scratchpad/bisect/build_ladder.py) before being ported here verbatim.
_CDATA_MARKER_START = "CDATA_MARKER_START"
_CDATA_MARKER_END = "CDATA_MARKER_END"
_CDATA_MARKER_RE = re.compile(f"{_CDATA_MARKER_START}(.*?){_CDATA_MARKER_END}")


def _kpi_tile_caption_text(title: str) -> str:
    """Return the letter-spaced UPPERCASE caption text for a KPI tile's
    in-label caption run (e.g. ``"Sales"`` -> ``"S A L E S"``) — mirrors
    WB-118's mined ``"S A L E S"``/``"P R O F I T"`` caption runs
    (WB-118.twbx, "Sales KPI (BAN) New") and
    the bisect ladder's confirmed-working V11/V12 shape."""
    return " ".join(title.strip().upper())


def _kpi_tile_ban_value_color(kpi_tile: dict[str, Any] | None) -> str:
    """Return the BAN value runs' fontcolor: ``kpi_tile.ban_color`` when
    set, else :data:`_KPI_LABEL_DEFAULT_VALUE_COLOR` (the graft's own
    ``#555555`` — legible against the default, unstyled white table fill
    a KPI tile has when no themed background is set)."""
    tile = kpi_tile or {}
    ban_color = tile.get("ban_color")
    return str(ban_color) if ban_color else _KPI_LABEL_DEFAULT_VALUE_COLOR


def _kpi_tile_ban_caption_color(kpi_tile: dict[str, Any] | None) -> str:
    """Return the caption run's fontcolor.

    ``kpi_tile.caption_color`` wins when explicitly set. Otherwise: when
    ``kpi_tile.background`` is set (a themed, often-dark tile), default to
    the SAME color as the value runs (:func:`_kpi_tile_ban_value_color`) —
    the caption sits on the same background, so it needs the same
    contrast treatment. When no themed background is set, default to
    :data:`_KPI_LABEL_DEFAULT_VALUE_COLOR` (the graft's own gray, legible
    on a plain white tile).
    """
    tile = kpi_tile or {}
    explicit = tile.get("caption_color")
    if explicit:
        return str(explicit)
    if tile.get("background"):
        return _kpi_tile_ban_value_color(kpi_tile)
    return _KPI_LABEL_DEFAULT_VALUE_COLOR


def _kpi_tile_customized_label(
    kpi_tile: dict[str, Any] | None,
    ban_font: dict[str, Any] | None,
    ds_ref: str,
    title: str,
    *,
    primary_calc_instance: str | None,
    delta_calc_instance: str | None,
) -> ET.Element | None:
    """Return a ``<customized-label>`` for the KPI tile's pane, or ``None``.

    Design Excellence, Slice D4 FINAL SHAPE (live-probe #3's bisect ladder,
    V0/V9-V12) — encodes the run idiom PROVEN to render on Tableau Cloud, in
    place of an earlier, independently-plausible-looking shape (fontsize/
    fontcolor from brand+theme, plain ``"\\n"`` separators, no caption run,
    escaped-text placeholders) that a live probe showed rendered NOTHING —
    the label silently fell back to the plain default text-mark. A dozen
    offline bisect variants (V0-V12, kept in scratchpad, never committed)
    isolated the exact difference: this builder's shape needed the graft's
    OWN caption run, its literal glyph-prefixed ``"Æ\\n"`` newline runs, its
    CDATA (not escaped-text) placeholder form, AND a pane-level
    ``mark-labels-show``/``mark-labels-cull`` rule
    (:func:`_kpi_tile_pane_style_rules`) — EVERY one of those traits,
    together; V9/V10 (missing only the run idiom, mark-labels-show added
    alone) rendered a BLANK mark, proving partial adoption is worse than
    none. This function now reproduces that idiom byte-for-byte, with
    VALUES (not structure) parameterized off ``kpi_tile``/``brand``/the
    calc-column instances built by :func:`_append_kpi_ban_calc_column`.

    Verified DIRECTLY against WB-118's real, published "Sales KPI (BAN)
    New" worksheet (WB-118.twbx): its
    ``<pane>`` children, IN ORDER, are ``view, mark, encodings,
    customized-label, style`` — ``<customized-label>`` COEXISTS with the
    full ``<encodings>`` list (all measures stay declared; the label is a
    presentation template layered on top, not a replacement). Confirmed
    against the XSD's ``PaneSpecification-G`` sequence (``encodings`` ->
    ``HiddenFields`` -> ``DropLine`` -> ``Trendline`` -> ``ReferenceLine``
    -> ``[CustomTooltip]`` -> ``[CustomLabel]`` -> ``[Stylesheet]``) — since
    this builder emits none of the optional groups between ``encodings``
    and ``CustomLabel``, the effective order is exactly ``encodings,
    customized-label, style``, matching the mined pane byte-for-byte.

    Run shape (every run carries ``fontalignment='0'``, mirroring the
    graft verbatim):

    1. CAPTION run: letter-spaced uppercase tile title
       (:func:`_kpi_tile_caption_text`), ``fontsize``
       :data:`_KPI_LABEL_CAPTION_FONTSIZE`, ``fontcolor`` from
       :func:`_kpi_tile_ban_caption_color`.
    2. Separator run: the literal glyph-prefixed double newline
       (:data:`_KPI_LABEL_CAPTION_SEPARATOR`, ``"Æ\\n\\n"``), no other attrs.
    3. PRIMARY value run: a CDATA placeholder referencing
       *primary_calc_instance*, ``fontsize`` from
       ``min(brand.typography.ban.size, `` :data:`_KPI_LABEL_VALUE_FONTSIZE_MAX`
       ``)`` when brand is present else :data:`_KPI_LABEL_VALUE_FONTSIZE_FALLBACK`,
       ``fontcolor`` from :func:`_kpi_tile_ban_value_color`. The clamp is a
       live-probe #2e finding, NOT cosmetic: the full-pipeline label
       mechanism renders correctly end to end, but a large requested
       fontsize (e.g. brand.yaml's ``36``) overflows the ~240px KPI tile
       cell and Tableau renders the value as ``"###"`` (a cell-overflow
       placeholder, not a label failure) — see
       :data:`_KPI_LABEL_VALUE_FONTSIZE_MAX`'s own comment for the mined
       maximum this clamps to. NO ``fontname`` — the proven V11/V12 shape
       carries none; BAN font family follows the workbook default instead
       (a documented, honest limitation — an earlier version of this
       function set ``fontname`` from ``brand.typography.ban.font``, but
       that attribute was NEVER part of any variant that actually
       rendered, so it is not risked here).
    4. Newline run: the literal glyph-prefixed single newline
       (:data:`_KPI_LABEL_NEWLINE_GLYPH`, ``"Æ\\n"``).
    5. DELTA value run (only when *delta_calc_instance* is set): a CDATA
       placeholder referencing it, ``fontsize`` :data:`_KPI_LABEL_DELTA_FONTSIZE`
       (a fixed, proven value — NOT derived as half the primary's fontsize,
       unlike an earlier version of this function; V12 tested exactly
       ``12``, nothing else), same ``fontcolor`` as the primary.
    6. A second trailing newline run (same glyph form), always LAST when a
       delta run was emitted — mirrors the graft's own trailing run and
       V12's exact shape.

    Returns ``None`` when *primary_calc_instance* is falsy (mirrors
    ``kpi_ban_active``'s gating exactly — the caller only passes a calc
    instance once ``kpi_tile``, ``kpi_spec``, and
    ``kpi_spec.primary_measure`` are all present).
    """
    if not primary_calc_instance:
        return None

    caption_color = _kpi_tile_ban_caption_color(kpi_tile)
    value_color = _kpi_tile_ban_value_color(kpi_tile)
    ban_size = ban_font.get("size") if ban_font else None
    # Live-probe #2e: clamp to the mined maximum (26) so a large brand-
    # requested size never overflows the ~240px KPI tile cell into "###".
    primary_fontsize = (
        min(int(ban_size), _KPI_LABEL_VALUE_FONTSIZE_MAX)
        if ban_size is not None
        else _KPI_LABEL_VALUE_FONTSIZE_FALLBACK
    )

    label_el = ET.Element("customized-label")
    formatted_text_el = ET.SubElement(label_el, "formatted-text")

    def _append_run(
        text: str, *, fontcolor: str | None = None, fontsize: int | None = None
    ) -> None:
        # Alphabetical attribute order (fontalignment, fontcolor, fontsize)
        # — matches the graft's own mined run attribute order.
        run_attrs: dict[str, str] = {"fontalignment": "0"}
        if fontcolor:
            run_attrs["fontcolor"] = fontcolor
        if fontsize is not None:
            run_attrs["fontsize"] = str(fontsize)
        run_el = ET.SubElement(formatted_text_el, "run", run_attrs)
        run_el.text = text

    def _append_value_run(field_ref: str, *, fontsize: int) -> None:
        marker = f"{_CDATA_MARKER_START}{field_ref}{_CDATA_MARKER_END}"
        _append_run(marker, fontcolor=value_color, fontsize=fontsize)
        _append_run(_KPI_LABEL_NEWLINE_GLYPH)

    _append_run(
        _kpi_tile_caption_text(title),
        fontcolor=caption_color,
        fontsize=_KPI_LABEL_CAPTION_FONTSIZE,
    )
    _append_run(_KPI_LABEL_CAPTION_SEPARATOR)
    _append_value_run(f"{ds_ref}.{primary_calc_instance}", fontsize=primary_fontsize)
    if delta_calc_instance:
        _append_value_run(f"{ds_ref}.{delta_calc_instance}", fontsize=_KPI_LABEL_DELTA_FONTSIZE)

    return label_el


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
                         Slice D2+; ``model_dump()`` snake_case dict — see
                         ``server.DesignThemeModel``).  When present:
                         ``design_theme["dashboard_background"]`` becomes a
                         ``background-color`` ``<zone-style>`` on the canvas
                         (``layout-basic``) zone; ``design_theme["chart_card"]``
                         becomes a ``<zone-style>`` on every chart/worksheet
                         zone; ``design_theme["spacing"]["gutter"]``
                         supplies each chart zone's ``margin`` when
                         ``chart_card["margin"]`` is unset (an explicit
                         ``chart_card["margin"]`` always wins); and
                         ``design_theme["spacing"]["outer_margin"]`` becomes a
                         ``margin`` ``<zone-style>`` on the outer
                         ``layout-flow`` zone. In a ``kpi_band_over_charts``
                         layout, ``design_theme["kpi_tile"]`` (Slice D4) gives
                         every KPI-tile zone the full box model
                         (background/border/padding) AND gives the KPI band's
                         OWN container zone ``kpi_tile["background"]`` alone
                         (continuous band look) — absent ``kpi_tile``, KPI
                         tiles stay fully unstyled even when other
                         ``design_theme`` blocks are set. Design Excellence,
                         Slice D4 FINAL SHAPE: every KPI-tile zone ALSO gets
                         ``show-title='false'`` whenever ``kpi_tile`` theming
                         is active — each tile's own worksheet now carries an
                         in-label caption (:func:`_kpi_tile_customized_label`),
                         so the zone's own title would duplicate it; mirrors
                         WB-118's own mined KPI-tile zone attribute (see
                         :func:`_append_worksheet_zones`'s docstring). Design
                         Excellence, Slice D5: when ``design_theme["header"]``
                         is present AND ``title`` is truthy, the separate
                         title/subtitle zones below collapse into ONE
                         multi-run themed header zone (see
                         :func:`_build_header_zone`) carrying
                         ``header["background"]`` as its own ``<zone-style>``
                         and ``header["title_color"]``/``["subtitle_color"]``
                         as run ``fontcolor``. Absent
                         ``design_theme`` entirely (``None``, the default):
                         no ``<zone-style>`` is ever emitted (byte-identical
                         determinism guard, same discipline as ``brand``).
    """
    dashboard = ET.Element("dashboard", {"name": name})

    # Real dashboards (wb1–wb7) carry a <style/> child before <size>.
    ET.SubElement(dashboard, "style")

    # Design Excellence, Slice D4 FINAL SHAPE hotfix (live-probe #2f's
    # sizing-mode investigation): ``sizing-mode='fixed'`` is REQUIRED,
    # unconditionally, on every dashboard's <size> — mined verbatim from
    # every exemplar dashboard (WB-118/WB-117, e.g.
    # ``<size maxheight='900' maxwidth='1300' minheight='900'
    # minwidth='1300' sizing-mode='fixed'/>``). Without it, an
    # equal-min/max ``<size>`` is NOT treated as a truly fixed canvas by
    # Tableau's server-side image renderer — it falls back to range/
    # automatic sizing, which COMPRESSES zone content in ways a purely
    # proportional 0-100000 zone grid does not predict, independent of
    # font size, mark-labels-cull, or any other per-zone attribute (see
    # design/corpus/SCHEMA.md constraint #5 for the live-probe bisect
    # ladder — V13 (single-zone canvas: worked) vs V14-V23 (multi-zone
    # canvas, NO sizing-mode: every combination of fontsize/cull/delta/
    # band-height/fixed-size-zones failed identically — pointed straight
    # at a dashboard-level, not zone-level, sizing attribute). This is a
    # DELIBERATE, unconditional baseline change (not design_theme-gated):
    # it affects the no-theme byte-identical guards' EXPECTED bytes too
    # (both sides of the guard still match each other — the guard proves
    # design_theme doesn't change output, not that output is frozen
    # forever) — see the guard tests' updated assertions for the render
    # evidence justifying this one-time re-baselining.
    ET.SubElement(
        dashboard,
        "size",
        {
            "maxheight": str(canvas_height),
            "maxwidth": str(canvas_width),
            "minheight": str(canvas_height),
            "minwidth": str(canvas_width),
            "sizing-mode": "fixed",
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

    # Design Excellence, Slice D5: header-band theming. ``design_theme``
    # absent/``header`` unset -> ``theme_header`` is None -> the title/
    # subtitle code below falls back to the pre-D5 separate-zone path
    # unchanged (the byte-identical guard).
    theme_header = design_theme.get("header") if design_theme else None

    # Design Excellence, Slice D4: KPI-tile zone-style inputs. ``design_theme``
    # absent/``kpi_tile`` unset -> both dicts empty -> no <zone-style> is ever
    # appended below (same byte-identical guard as chart_zone_style_formats).
    theme_kpi_tile = design_theme.get("kpi_tile") if design_theme else None
    kpi_zone_style_formats = _kpi_tile_zone_style_formats(theme_kpi_tile)
    kpi_band_background_formats: dict[str, str] = (
        {"background-color": str(theme_kpi_tile["background"])}
        if theme_kpi_tile and theme_kpi_tile.get("background")
        else {}
    )

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
        # Design Excellence, Slice D5: when a theme header block is present,
        # title (+ optional subtitle) collapse into ONE multi-run themed
        # header zone (_build_header_zone) instead of the two separate zones
        # below — see that function's docstring for the mined multi-run
        # vocabulary it mirrors. Absent theme_header (design_theme unset or
        # carries no header block): unchanged separate-zone path, byte-
        # identical to pre-D5 output.
        #
        # Title: no brand → bold=true, fontsize=20 (mirrors wb6 ~1664).
        # With brand → typography.title drives fontcolor/fontname/fontsize,
        # no bold attribute (mirrors wb7 ~4478: the "Tableau Bold" font name
        # itself carries the boldness). See _title_or_subtitle_run_attrs.
        if title and theme_header:
            header_zone: ET.Element = _build_header_zone(
                title, subtitle, _next_id(), brand, theme_header
            )
            outer_flow.append(header_zone)
        else:
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
            effective_charts = (
                chart_titles if chart_titles else [t for t in titles if t not in set(effective_kpi)]
            )

            # KPI band (param='horz', one zone per KPI tile).
            if effective_kpi:
                kpi_flow = ET.SubElement(
                    outer_flow,
                    "zone",
                    {
                        # BEAUTY-GATE hotfix (live-probe #5, round 6): the
                        # BAND CONTAINER itself — not just each tile's
                        # wrapper/leaf — is ALSO is-fixed/fixed-size AND
                        # carries layout-strategy-id='distribute-evenly' in
                        # the mined exemplar (its own KPI row, zone id 9:
                        # `fixed-size='96' is-fixed='true'
                        # layout-strategy-id='distribute-evenly'
                        # param='horz'`). Every prior round only cascaded
                        # the WRAPPER+LEAF (2 levels); a live-probe render
                        # of the real multi-tile pipeline still showed every
                        # tile's value as an unreadable '####' placeholder
                        # with that 2-level cascade alone — this extends it
                        # to the BAND container (3rd level), matching the
                        # exemplar's own nesting depth exactly.
                        "fixed-size": "140",
                        "h": "20000",
                        "id": str(_next_id()),
                        "is-fixed": "true",
                        "layout-strategy-id": "distribute-evenly",
                        "param": "horz",
                        "type-v2": "layout-flow",
                        "w": "100000",
                        "x": "0",
                        "y": "0",
                    },
                )
                # Slice D2 deferred KPI-tile theming to Slice D4; it now
                # applies here: each tile gets the full kpi_tile box model
                # (background/border/padding), AND the band container itself
                # gets kpi_tile.background ONLY (continuous band look) as its
                # OWN zone-style, appended LAST (after its tile children) per
                # the XSD's "zone-style is the last child of a zone" rule.
                # Design Excellence, Slice D4 FINAL SHAPE: whenever kpi_tile
                # theming is active, every tile's worksheet now carries an
                # in-label caption (_kpi_tile_customized_label) — the zone's
                # own title would duplicate it, so it is suppressed here
                # (show-title='false', mirroring WB-118's own mined KPI
                # tile zone attribute — see _append_worksheet_zones's
                # docstring).
                # BEAUTY-GATE hotfix (live-probe #5, round 2): cascade
                # is-fixed/fixed-size around every KPI tile EXCEPT THE LAST
                # (see _append_worksheet_zones's wrap_fixed_size docstring
                # and _kpi_wrapped_indices) so its value actually renders.
                # Wrapper ids are allocated from the SAME counter as every
                # other zone in this dashboard (guarantees no id collisions)
                # — exactly one id per WRAPPED tile, not per title.
                kpi_wrap_count = len(_kpi_wrapped_indices(len(effective_kpi)))
                kpi_wrapper_id_start = _next_id() if kpi_wrap_count else None
                for _ in range(kpi_wrap_count - 1):
                    _next_id()
                _append_worksheet_zones(
                    kpi_flow,
                    effective_kpi,
                    id_start=3,
                    zone_style_formats=kpi_zone_style_formats,
                    show_title=not bool(theme_kpi_tile),
                    wrap_fixed_size=True,
                    wrapper_id_start=kpi_wrapper_id_start,
                )
                _append_zone_style(kpi_flow, kpi_band_background_formats)

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
    # Design Excellence, Slice D4 FINAL SHAPE hotfix: same
    # sizing-mode='fixed' fix as _build_dashboard's <size> — see that
    # function's docstring comment for the full mined-provenance/render-
    # evidence rationale. Applied here too for consistency (a storyboard
    # is still a <dashboard> element with its own <size>).
    ET.SubElement(
        dashboard,
        "size",
        {
            "maxheight": str(canvas_height),
            "maxwidth": str(canvas_width),
            "minheight": str(canvas_height),
            "minwidth": str(canvas_width),
            "sizing-mode": "fixed",
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


def _finalize_xml(workbook: ET.Element) -> str:
    """Serialize *workbook* to a declaration-prefixed XML string, converting
    any KPI-tile customized-label placeholder runs' ``CDATA_MARKER_START``/
    ``CDATA_MARKER_END``-wrapped text (see :func:`_kpi_tile_customized_label`)
    into a real ``<![CDATA[...]]>`` section.

    ``xml.etree.ElementTree`` cannot emit CDATA natively — ``ET.tostring()``
    always escapes ``<``/``>`` inside element text, which would turn the
    mined ``<[ds].[instance]>`` placeholder form into
    ``&lt;[ds].[instance]&gt;``. This two-pass technique (write an
    XML-safe sentinel string as the run's ``.text`` during tree
    construction; AFTER ``ET.tostring()`` has already correctly escaped
    everything ELSE, regex-substitute the — still-untouched, since it
    contains no ``<``/``>``/``&`` characters — sentinel wrapper into a
    literal CDATA section) was proven deterministic in the offline bisect
    ladder (``.../scratchpad/bisect/build_ladder.py``, variants V0/V3/V6/
    V9-V12) before being ported here. It is confined to this ONE
    finalization call site (shared by :func:`build_twb_xml` and
    :func:`build_embedded_twb_xml`) so every other element's normal
    escaping stays untouched; a workbook with no KPI-tile customized-label
    (the vast majority) has no marker text at all and this is a no-op
    substitution.
    """
    xml_body = ET.tostring(workbook, encoding="unicode")

    def _replace(match: re.Match[str]) -> str:
        return f"<![CDATA[<{match.group(1)}>]]>"

    xml_body = _CDATA_MARKER_RE.sub(_replace, xml_body)
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"


# ---------------------------------------------------------------------------
# Dashboard actions (Design Excellence, Slice D7 — verified XML only)
#
# Mirrors two mined action shapes from ``design/corpus/recipes/actions.yaml``
# (see ``_build_actions``'s docstring for the full derivation + citations):
#   - a ``tsc:tsl-filter`` <action> per chart worksheet (cross-filtering)
#   - a ``tsc:brush`` <action> per chart worksheet with a color field (highlight)
# Emitted as a single workbook-level <actions> element, positioned AFTER
# <datasources> and BEFORE <worksheets> (XSD ``Workbook-Actions-G``, verified
# against ``sidecar/tests/schemas/twb_2026.1.0.xsd`` lines ~7218-7224:
# ...DataSources-G -> DataSourceRelationships-G -> MapSources-G ->
# SharedViews-G -> Actions-G -> Worksheets-G -> Dashboards-G...).
# ---------------------------------------------------------------------------


def _dashboard_chart_and_kpi_titles(
    db: dict[str, Any], sheets_by_title: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Return ``(chart_titles, kpi_titles)`` for one dashboard dict, in the
    dashboard's own title order.

    Mirrors ``_build_dashboard``'s own ``kpi_band_over_charts`` split (see its
    "Content zone(s)" section): prefer ``layout_grammar.chart_titles``/
    ``kpi_tile_titles`` when the dashboard actually uses that grammar kind;
    otherwise classify every dashboard title purely by its own
    ``sheet["kind"]`` — a sheet with ``kind != "kpi_tile"`` (including
    ``kind is None``/``"chart"``) counts as a chart, the SAME "not kpi ==
    chart" fallback ``_build_dashboard`` itself uses for ``effective_charts``.

    Falls back to every sheet's title (in ``sheets_by_title``'s insertion
    order) when the dashboard dict carries no explicit ``titles`` — the same
    fallback ``build_twb_xml``/``build_embedded_twb_xml`` apply before calling
    ``_build_dashboard``.
    """
    titles = [str(t) for t in db.get("titles", [])] or list(sheets_by_title)
    layout_grammar = db.get("layout_grammar") or {}
    grammar_kind = str(layout_grammar.get("kind", "")) if layout_grammar else ""

    if grammar_kind == "kpi_band_over_charts":
        kpi_titles = [str(t) for t in (layout_grammar.get("kpi_tile_titles") or [])]
        if not kpi_titles:
            kpi_titles = [t for t in titles if sheets_by_title.get(t, {}).get("kind") == "kpi_tile"]
        chart_titles = [str(t) for t in (layout_grammar.get("chart_titles") or [])]
        if not chart_titles:
            chart_titles = [t for t in titles if t not in set(kpi_titles)]
        return chart_titles, kpi_titles

    kpi_titles = [t for t in titles if sheets_by_title.get(t, {}).get("kind") == "kpi_tile"]
    chart_titles = [t for t in titles if t not in set(kpi_titles)]
    return chart_titles, kpi_titles


def _sheet_color_field_caption(sheet: dict[str, Any] | None) -> str | None:
    """Return ``sheet["color"]["field"]`` (a plain field caption), or ``None``.

    Mirrors WB-114's ``WB-114.twbx`` "Highlight 1 (generated)"
    action's ``field-captions`` param: a plain caption string (e.g.
    ``"Clusters"``), never a bracketed ``[field]`` reference. Returns ``None``
    when *sheet* has no ``color`` block or an empty ``field`` — the caller
    skips emitting a highlight action for that sheet ("if present else skip",
    per the D7 slice scope).
    """
    if not sheet:
        return None
    color = sheet.get("color")
    if not color:
        return None
    field = color.get("field")
    return str(field) if field else None


def _build_actions(
    dashboards: list[dict[str, Any]] | None,
    sheets: list[dict[str, Any]],
    interactions: dict[str, Any] | None,
) -> ET.Element | None:
    """Return the workbook-level ``<actions>`` element, or ``None`` (no-op).

    Returns ``None`` — the byte-identical guard — when *interactions* is
    falsy, both flags are unset/False, *dashboards* is empty/``None``, or no
    dashboard actually qualifies for any action (see below). Only *regular*
    dashboards are considered (never ``stories`` — a storyboard has no chart/
    KPI zones of its own to source/target an action against).

    Two action families, each mirroring a REAL, mined ``<action>`` from
    ``design/corpus/recipes/actions.yaml`` — never invented XML (the
    D0-D7 corpus discipline):

    ``interactions["cross_filter"]`` — ``tsc:tsl-filter``
    ------------------------------------------------------
    For every dashboard with >=2 chart sheets (``kind != "kpi_tile"``; KPI
    tiles are never eligible as either a source or a cross-filter target —
    clicking a BAN shouldn't filter, and tiles shouldn't react, an explicit
    D7-scope product decision layered on top of the mined shape below), one
    ``<action>`` per chart worksheet, in the dashboard's own chart order::

        <action caption='Filter: {ws}' name='[ActionN]'>
          <activation auto-clear='true' type='on-select'/>
          <source dashboard='{dashboard}' type='sheet' worksheet='{ws}'/>
          <command command='tsc:tsl-filter'>
            <param name='exclude' value='{sorted(kpi_titles + [ws])}'/>
            <param name='special-fields' value='all'/>
            <param name='target' value='{dashboard}'/>
          </command>
        </action>

    This shape — ``<source dashboard=... type='sheet' worksheet=...>``,
    ``command='tsc:tsl-filter'``, params ``exclude``/``special-fields='all'``/
    ``target={dashboard name}``, ``activation auto-clear='true'`` — is
    mirrored VERBATIM from WB-118's
    ``WB-118.twbx`` (``Superstore Dashboard.twb``
    ``/workbook/actions/action`` — captions "State FA"/"Cat FA"/"Segment FA"/
    "Subcat filter"/"Manufacturer FA", every one targeting the shared
    ``Superstore Dashboard``).

    **Exclude-list semantics — derived from TWO mined exemplars (evidence,
    not invention):**

    1. WB-118's five ``tsc:tsl-filter`` actions above ALL carry the exact
       SAME ``exclude`` value — ``'Info button,Last Updated,Metric
       Select,Min and Max Date,Year Select'`` — REGARDLESS of which chart
       worksheet is the source. None of those five chart worksheets (Sales |
       By State/Category/Sub-Category/Manufacturer/Segment) ever appears in
       ITS OWN exclude list or any peer's — every other chart, AND every KPI
       BAN sheet (Total Orders KPI, Profit KPI (Line), Sales KPI (BAN) New,
       etc.), remains an implicit filter TARGET. ``exclude`` names only the
       non-chart utility/control sheets (a metric selector, a year selector,
       a "last updated" text zone, an info button) — proving ``target`` +
       ``exclude`` together express "filter every OTHER chart/KPI on this
       dashboard", the cross-filter behavior this flag is named for.
    2. WB-114's ``WB-114.twbx`` Action4 ("Map to Scatter
       Plot", source worksheet ``Prescriptive Map``, target dashboard
       ``Super: Prescriptive``) is the SAME shape but its ``exclude`` value
       — ``'Annotations Button: Inactive,Descriptive Button:
       Inactive,Insight 1,Insight 1 Indicator,Insight 2,Insight
       3,Insight Indicator 2,Insight Indicator 3,Prescriptive Button:
       Active,Prescriptive Map,X-Axis Label'`` — DOES include the source
       itself (``Prescriptive Map``), while ``Prescriptive Scatter Plot``
       (the intended cross-filter target, per the caption) is conspicuously
       absent from it. This is the self-exclusion pattern: a source
       excluded from its own action's filtering keeps showing full,
       unfiltered context while everything else reacts.
    3. This module adopts (2)'s self-exclusion (cleaner, more predictable
       "click chart A -> every OTHER chart/KPI updates, chart A itself keeps
       its full context" UX) rather than (1)'s "the source also filters
       itself" behavior, PLUS the D7-scope KPI-tile exclusion — so
       ``exclude = sorted({*kpi_titles, source_worksheet})``. Peer chart
       worksheets are NEVER added to ``exclude`` (matching both exemplars):
       they remain implicit cross-filter targets via ``target=dashboard``.
       ``exclude``'s member ordering is ALPHABETICAL, mirroring both (1)'s
       and WB-114's own five-worksheet ordering (Annotations < Descriptive <
       Insight... < Prescriptive < X-Axis) — deterministic and reproducible
       without needing the real workbook's internal zone/z-order (which this
       builder has no equivalent representation of).

    ``interactions["highlight"]`` — ``tsc:brush``
    ----------------------------------------------
    For every chart worksheet (across every dashboard, no >=2 gate — a
    highlight is self-contained per sheet, see below) carrying a ``color``
    encoding, one ``<action>``::

        <action caption='Highlight: {ws}' name='[ActionN]'>
          <activation auto-clear='true' type='on-select'/>
          <source type='sheet' worksheet='{ws}'/>
          <command command='tsc:brush'>
            <param name='field-captions' value='{color field caption}'/>
            <param name='target' value='{ws}'/>
          </command>
        </action>

    Mirrored VERBATIM from WB-114's ``WB-114.twbx`` Action1,
    caption "Highlight 1 (generated)" — ``tsc:brush`` with ONLY
    ``field-captions``/``target`` params (no ``exclude``), a ``<source>``
    with NO ``dashboard`` attribute (unscoped — a sheet-level auto-generated
    highlight action, not a dashboard cross-filter), and ``target`` set to
    the WORKSHEET's own name rather than a dashboard. Chart worksheets
    without a ``color`` block are skipped entirely ("if present else skip",
    per the D7 slice scope) rather than emitting a meaningless empty
    ``field-captions``.

    Both this module's chosen ``[ActionN]`` naming (no GUID suffix) and its
    caption text are a DELIBERATE D7-scope simplification over Tableau
    Desktop's own GUID-suffixed names (e.g.
    ``[Action12_A765F0E5A3974254AB2C92AE6117D6DF]``, seen in WB-118's own
    workbook) — deterministic, reproducible output is required here (this
    slice's own gate: "deterministic naming/ordering, two builds identical"),
    and the plain ``[ActionN]`` form is ITSELF real, mined XML: WB-114's
    ``WB-114.twbx`` uses exactly this GUID-free form
    throughout (``[Action1]``, ``[Action2]``, … ``[Action10]``). ``name``
    values are assigned by a single counter spanning the WHOLE ``<actions>``
    element (every dashboard's cross-filter actions, in chart order, THEN
    every dashboard's highlight actions, in chart order) — simple and
    deterministic, not a literal reproduction of either exemplar's
    (non-sequential, edit-history-dependent) numbering.
    """
    if not interactions or not dashboards:
        return None
    cross_filter = bool(interactions.get("cross_filter"))
    highlight = bool(interactions.get("highlight"))
    if not cross_filter and not highlight:
        return None

    sheets_by_title: dict[str, dict[str, Any]] = {str(s["title"]): s for s in sheets}
    action_els: list[ET.Element] = []
    counter = [1]

    def _next_name() -> str:
        name = f"[Action{counter[0]}]"
        counter[0] += 1
        return name

    dashboard_chart_titles: list[tuple[str, list[str], list[str]]] = []
    for db in dashboards:
        db_name = str(db.get("name", "Dashboard 1"))
        chart_titles, kpi_titles = _dashboard_chart_and_kpi_titles(db, sheets_by_title)
        dashboard_chart_titles.append((db_name, chart_titles, kpi_titles))

    if cross_filter:
        for db_name, chart_titles, kpi_titles in dashboard_chart_titles:
            if len(chart_titles) < 2:
                continue
            for ws in chart_titles:
                exclude = sorted({*kpi_titles, ws})
                action_el = ET.Element("action", {"caption": f"Filter: {ws}", "name": _next_name()})
                ET.SubElement(action_el, "activation", {"auto-clear": "true", "type": "on-select"})
                ET.SubElement(
                    action_el, "source", {"dashboard": db_name, "type": "sheet", "worksheet": ws}
                )
                command_el = ET.SubElement(action_el, "command", {"command": "tsc:tsl-filter"})
                ET.SubElement(command_el, "param", {"name": "exclude", "value": ",".join(exclude)})
                ET.SubElement(command_el, "param", {"name": "special-fields", "value": "all"})
                ET.SubElement(command_el, "param", {"name": "target", "value": db_name})
                action_els.append(action_el)

    if highlight:
        for _db_name, chart_titles, _kpi_titles in dashboard_chart_titles:
            for ws in chart_titles:
                color_field = _sheet_color_field_caption(sheets_by_title.get(ws))
                if not color_field:
                    continue
                action_el = ET.Element(
                    "action", {"caption": f"Highlight: {ws}", "name": _next_name()}
                )
                ET.SubElement(action_el, "activation", {"auto-clear": "true", "type": "on-select"})
                ET.SubElement(action_el, "source", {"type": "sheet", "worksheet": ws})
                command_el = ET.SubElement(action_el, "command", {"command": "tsc:brush"})
                ET.SubElement(command_el, "param", {"name": "field-captions", "value": color_field})
                ET.SubElement(command_el, "param", {"name": "target", "value": ws})
                action_els.append(action_el)

    if not action_els:
        return None

    actions_el = ET.Element("actions")
    for action_el in action_els:
        actions_el.append(action_el)
    return actions_el


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
    interactions: dict[str, Any] | None = None,
) -> str:
    """Build a schema-valid TWB XML string.

    Workbook child ordering (required by XSD):
    (``<preferences>`` if branded) → ``<datasources>`` → (``<actions>`` if
    interactions) → ``<worksheets>`` → ``<dashboards>`` (if any) →
    ``<windows>`` → ``<explain-data>`` (required).

    When ``dashboards`` is ``None`` (default), both calls with no ``dashboards``
    kwarg and with an explicit ``dashboards=None`` produce byte-identical XML
    (a determinism guard — not a byte-snapshot comparison against the pre-feature
    version; the worksheet/window structure was re-baselined for schema validity).
    The same determinism guarantee holds for ``brand``: omitting it (or passing
    ``None`` explicitly) never changes the output (Phase E1, Slice B), for
    ``stories`` (Phase E4): omitting it never changes the output either, for
    ``design_theme`` (Design Excellence, Slice D2): omitting it never changes
    the output either, and for ``interactions`` (Design Excellence, Slice D7):
    omitting it (or both flags being ``False``) never changes the output either.

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
        interactions: Optional dashboard interaction toggles (Design
            Excellence, Slice D7; ``model_dump()`` snake_case dict — see
            ``server.InteractionsModel``). When present, drives
            :func:`_build_actions` to emit a workbook-level ``<actions>``
            element (verified mined XML only — see its docstring for the
            full derivation). Absent (the default), or both flags ``False``:
            byte-identical to before this slice (no ``<actions>`` element).
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

    # --- Design Excellence, Slice D3: workbook-level chrome style-rules -----
    # (gridline/zeroline off, optional defensive title color) — AFTER
    # <preferences>, BEFORE <datasources> per the XSD's Workbook-Preferences-G
    # -> Workbook-StyleTheme-G -> Workbook-Styles-G -> ... ->
    # Workbook-DataSources-G sequence. No-op when design_theme has no chrome
    # intent (byte-identical guard, same discipline as brand above).
    if design_theme:
        _append_style_rules(workbook, _workbook_style_rules(design_theme.get("chrome")))

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
            sqlproxy_geo_role_map[geo_field] = _GEO_SEMANTIC_ROLE.get(geo_role, "[State].[Name]")
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

    # --- Design Excellence, Slice D7: dashboard actions ----------------------
    # AFTER <datasources>, BEFORE <worksheets> per the XSD's
    # Workbook-Actions-G position. No-op (byte-identical guard) when
    # interactions is absent/falsy or resolves to zero actions — see
    # _build_actions's docstring for the full mined-XML derivation.
    actions_el = _build_actions(dashboards, sheets, interactions)
    if actions_el is not None:
        workbook.append(actions_el)

    # --- Worksheets ---------------------------------------------------------
    # re-baselined for schema-valid output (XSD A2)
    worksheets = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets.append(
            _build_worksheet(
                sheet, datasource_name, ds_internal, i, design_theme=design_theme, brand=brand
            )
        )

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
            db_titles = [str(t) for t in db.get("titles", [])] or [str(s["title"]) for s in sheets]
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

    return _finalize_xml(workbook)


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
    interactions: dict[str, Any] | None = None,
) -> Path:
    """Build a .twbx (zip containing the generated .twb) for a published datasource.

    When ``dashboards`` is ``None`` (default), the two call forms (no kwarg and
    explicit ``None``) produce byte-identical output (determinism guard).
    Pass a non-None list to include a ``<dashboards>`` block and a dashboard
    window entry. ``brand`` (Phase E1, Slice B), ``stories`` (Phase E4),
    ``design_theme`` (Design Excellence, Slice D2), and ``interactions``
    (Design Excellence, Slice D7) follow the same determinism guarantee — see
    :func:`build_twb_xml`.
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
        interactions=interactions,
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
    interactions: dict[str, Any] | None = None,
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
        interactions:     Optional dashboard interaction toggles (Design
                          Excellence, Slice D7) — see :func:`build_twb_xml`
                          for the full behaviour. Absent (the default), or
                          both flags False: byte-identical to before this
                          slice.

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

    # --- Design Excellence, Slice D3: workbook-level chrome style-rules -----
    # See build_twb_xml's twin comment for the full XSD-ordering rationale.
    if design_theme:
        _append_style_rules(workbook, _workbook_style_rules(design_theme.get("chrome")))

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

    # --- Design Excellence, Slice D7: dashboard actions ----------------------
    # See build_twb_xml's twin comment for the full XSD-ordering rationale.
    actions_el = _build_actions(dashboards, sheets, interactions)
    if actions_el is not None:
        workbook.append(actions_el)

    # --- Worksheets ---------------------------------------------------------
    worksheets_el = ET.SubElement(workbook, "worksheets")
    for i, sheet in enumerate(sheets):
        worksheets_el.append(
            _build_worksheet(
                sheet, datasource_name, ds_internal, i, design_theme=design_theme, brand=brand
            )
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
            db_titles = [str(t) for t in db.get("titles", [])] or [str(s["title"]) for s in sheets]
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

    return _finalize_xml(workbook)


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
    interactions: dict[str, Any] | None = None,
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
        interactions:     Optional dashboard interaction toggles (Design
                          Excellence, Slice D7) — see :func:`build_twb_xml`.
                          Absent (the default), or both flags False:
                          byte-identical to before this slice.

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
        interactions=interactions,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.twb", twb_xml)
        archive.write(hyper_path, arcname=f"Data/{hyper_filename}")

    return out_path
