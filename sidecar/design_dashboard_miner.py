"""Dashboard-level / story-level mining (Slice T1 — inputs for design_stats.py's aggregation).

Sibling module to ``design_miner.py``, NOT a fork of it: this file reuses ``design_miner.py``'s
hardened parsing plumbing (``safe_parser`` — XXE-safe, ``huge_tree``; ``read_twb_bytes`` —
zip-slip-guarded ``.twbx`` extraction) and its ``_zone_style_formats`` helper rather than
reimplementing any of it. Split out from ``design_miner.py`` purely for file-size cohesion (the
5 D0 recipe extractors and these 2 T1 extractors are genuinely different concerns — "mine
individual constructs" vs. "mine dashboard/story-level shape" — see ``design_miner.py``'s module
docstring for the boundary).

Exposes two public functions:

- ``mine_dashboards(paths)`` — one record per REGULAR (non-storyboard) ``<dashboard>``: canvas
  size/sizing-mode, zone-type counts, nesting depth, title-zone shape (fontsize/bold/color/height
  ratio, if a text zone sits in the top 15% of the canvas), KPI-band-like rows, device-layout
  presence, filter/paramctrl zone counts, raw margin/padding pixel values, and whether any
  embedded worksheet shows mark labels.
- ``mine_stories(paths)`` — one record per WORKBOOK (always ``len(paths)`` records, even for a
  workbook with zero storyboards): story-point counts per story, caption lengths, nav types.

Like the 5 recipe collections, this is a **dev-only** tool — never imported by ``server.py``,
``twb_builder.py``, or any runtime path. Unlike them, these records are **not** written to
``design/corpus/recipes/`` — they are in-memory inputs consumed directly by
``design_stats.py``'s corpus-wide aggregation into ``design/corpus/stats/*.yaml`` (committing
the raw per-workbook records for a ~100-workbook corpus would be repo bloat with no review
value; only the aggregated, provenance-cited stats are committed).

Determinism: exactly like ``design_miner.py`` — inputs are processed sorted by basename, and
output is sorted (``mine_dashboards`` by ``(source, xpath)``, ``mine_stories`` by ``source``),
so re-running with the same input set (in any order) yields identical records in identical
order.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from lxml import etree  # type: ignore[import-untyped]

from design_miner import _zone_style_formats, read_twb_bytes, safe_parser

# ---------------------------------------------------------------------------
# Dashboard-level mining
# ---------------------------------------------------------------------------

# Tableau's dashboard zone grid is a fixed 0-100000 unit space regardless of
# actual canvas pixel size (every <zone> h/w/x/y shares this one grid, at any
# nesting depth -- verified against every mined top-100 exemplar). A text
# zone with y below this threshold sits in the top 15% of the canvas -- the
# heuristic band for "this might be the dashboard's title" (documented, not
# mined -- see design_miner.py's module docstring discipline).
TITLE_ZONE_TOP_BAND_Y_MAX: int = 15_000

# A layout-flow row needs at least this many is-fixed='true' children,
# alongside layout-strategy-id='distribute-evenly', to be classified as a
# "KPI-band-like row" -- the real-world shape design/corpus/SCHEMA.md's
# render-constraint #5 documents as the working multi-tile KPI cascade.
KPI_BAND_MIN_FIXED_CHILDREN = 3


def _direct_child_zones(zone: Any) -> list[Any]:
    """Direct ``<zone>`` children, one level deep -- not the recursive ``.iter("zone")``."""
    return [c for c in zone if c.tag == "zone"]


def _max_nesting_depth(zones_el: Any) -> int:
    """Depth of the zone tree under ``<zones>``; the outermost zone(s) count as depth 1.

    An empty ``<zones>`` (no children at all) is depth 0 -- never observed in a real
    dashboard, but handled rather than raising on a malformed input.
    """

    def _depth(zone: Any) -> int:
        children = _direct_child_zones(zone)
        if not children:
            return 1
        return 1 + max(_depth(c) for c in children)

    top = _direct_child_zones(zones_el)
    return max((_depth(z) for z in top), default=0)


def _zone_type_counts(zones_el: Any) -> dict[str, int]:
    """Count every ``<zone>`` under ``<zones>`` (any depth) by its ``type-v2``.

    A bare worksheet-reference zone (``name`` attribute, no ``type``/``type-v2`` at all --
    the shape ``twb_builder.py``'s ``_append_worksheet_zones`` docstring calls out
    explicitly) is bucketed as ``"worksheet"``, mirroring ``design_miner._mine_zone_styles``'s
    existing ``zone_type`` fallback convention. A zone with neither ``type-v2`` nor a ``name``
    (e.g. a storyboard's bare ``type='title'``/``'flipboard'``/``'flipboard-nav'`` zones) falls
    back to its bare ``type`` attribute, or ``"unknown"`` if that is absent too -- storyboards
    are never passed to this function by ``mine_dashboards`` in practice, but the fallback keeps
    this helper total rather than raising on an unanticipated shape.
    """
    counts: dict[str, int] = {}
    for zone in zones_el.iter("zone"):
        type_v2 = zone.get("type-v2")
        if type_v2 is None:
            type_v2 = "worksheet" if zone.get("name") is not None else zone.get("type", "unknown")
        counts[type_v2] = counts.get(type_v2, 0) + 1
    return counts


def _parsed_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _title_zone_candidate(zones_el: Any) -> dict[str, Any] | None:
    """The topmost dashboard text zone within the top-15% band, or ``None`` if none qualifies.

    "Topmost" breaks ties by document order when two candidates share the same ``y`` (never
    observed in practice, but a deterministic tie-break is required either way).
    """
    candidates: list[tuple[int, Any]] = []
    for zone in zones_el.iter("zone"):
        if zone.get("type-v2") != "text":
            continue
        y = _parsed_int(zone.get("y"))
        if y is None or y >= TITLE_ZONE_TOP_BAND_Y_MAX:
            continue
        candidates.append((y, zone))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    _, zone = candidates[0]
    h = _parsed_int(zone.get("h"))
    formatted_text = zone.find("formatted-text")
    runs = formatted_text.findall("run") if formatted_text is not None else []
    first_run = runs[0] if runs else None
    fontsize: float | None = None
    if first_run is not None and first_run.get("fontsize") is not None:
        try:
            fontsize = float(first_run.get("fontsize"))
        except ValueError:
            fontsize = None
    return {
        "height_ratio": h / 100_000 if h is not None else None,
        "fontsize": fontsize,
        "bold": first_run is not None and first_run.get("bold") == "true",
        "color": first_run.get("fontcolor") if first_run is not None else None,
    }


def _kpi_band_rows(zones_el: Any) -> list[dict[str, Any]]:
    """Every horz ``layout-flow`` row with ``distribute-evenly`` and >= 3 fixed-size children."""
    rows: list[dict[str, Any]] = []
    for zone in zones_el.iter("zone"):
        if (
            zone.get("type-v2") != "layout-flow"
            or zone.get("param") != "horz"
            or zone.get("layout-strategy-id") != "distribute-evenly"
        ):
            continue
        children = _direct_child_zones(zone)
        fixed_children = [c for c in children if c.get("is-fixed") == "true"]
        if len(fixed_children) < KPI_BAND_MIN_FIXED_CHILDREN:
            continue
        h = _parsed_int(zone.get("h"))
        rows.append(
            {"height_ratio": h / 100_000 if h is not None else None, "child_count": len(children)}
        )
    return rows


def _dashboard_margins_and_paddings(dashboard_el: Any) -> tuple[list[int], list[int]]:
    """Raw (non-deduplicated) ``margin``/``padding`` pixel values from every ``<zone-style>``
    nested anywhere under this one ``<dashboard>`` -- reuses ``design_miner._zone_style_formats``
    (D0), scoped to a single dashboard so ``design_stats.py`` can stratify by that dashboard's
    own canvas width.
    """
    margins: list[int] = []
    paddings: list[int] = []
    for zs in dashboard_el.iter("zone-style"):
        formats = _zone_style_formats(zs)
        margin = _parsed_int(formats.get("margin"))
        if margin is not None:
            margins.append(margin)
        padding = _parsed_int(formats.get("padding"))
        if padding is not None:
            paddings.append(padding)
    return margins, paddings


def _dashboard_referenced_worksheet_names(zones_el: Any) -> set[str]:
    """Names of every worksheet-reference zone (``name`` attr, no ``type``/``type-v2``) under
    this dashboard's ``<zones>`` -- the shape ``twb_builder.py`` documents for embedding a real
    worksheet into a dashboard.
    """
    names: set[str] = set()
    for zone in zones_el.iter("zone"):
        if zone.get("type-v2") is None and zone.get("type") is None:
            name = zone.get("name")
            if name:
                names.add(name)
    return names


def _worksheets_with_mark_labels(root: Any) -> set[str]:
    """Worksheet names with a ``mark-labels-show='true'`` style-rule anywhere under them."""
    names: set[str] = set()
    for worksheet in root.findall(".//worksheets/worksheet"):
        name = worksheet.get("name")
        if not name:
            continue
        for sr in worksheet.iter("style-rule"):
            if any(
                f.get("attr") == "mark-labels-show" and f.get("value") == "true"
                for f in sr.findall("format")
            ):
                names.add(name)
                break
    return names


def _mine_dashboard_record(
    tree: Any,
    dashboard_el: Any,
    source: str,
    sha256: str,
    mark_label_worksheets: set[str],
) -> dict[str, Any] | None:
    """One mined record for a single REGULAR (non-storyboard) ``<dashboard>``.

    Returns ``None`` when the dashboard has no parseable canvas ``<size>`` (both a width and a
    height) or no ``<zones>`` at all -- such a dashboard cannot be bucketed by canvas width and is
    excluded from every dashboard-level stat (documented in ``design/corpus/SCHEMA.md``), rather
    than silently coerced into a bucket it was never actually mined for.
    """
    size_el = dashboard_el.find("size")
    zones_el = dashboard_el.find("zones")
    if size_el is None or zones_el is None:
        return None
    width = _parsed_int(size_el.get("maxwidth") or size_el.get("minwidth"))
    height = _parsed_int(size_el.get("maxheight") or size_el.get("minheight"))
    if width is None or height is None:
        return None

    referenced = _dashboard_referenced_worksheet_names(zones_el)
    margins_px, paddings_px = _dashboard_margins_and_paddings(dashboard_el)

    return {
        "source": source,
        "sha256": sha256,
        "xpath": tree.getpath(dashboard_el),
        "name": dashboard_el.get("name", ""),
        "canvas_width": width,
        "canvas_height": height,
        "sizing_mode": size_el.get("sizing-mode", ""),
        "zone_type_counts": _zone_type_counts(zones_el),
        "max_nesting_depth": _max_nesting_depth(zones_el),
        "title_zone": _title_zone_candidate(zones_el),
        "kpi_band_rows": _kpi_band_rows(zones_el),
        "has_device_layouts": dashboard_el.find("devicelayouts") is not None,
        "filter_zone_count": sum(1 for z in zones_el.iter("zone") if z.get("type-v2") == "filter"),
        "paramctrl_zone_count": sum(
            1 for z in zones_el.iter("zone") if z.get("type-v2") == "paramctrl"
        ),
        "margins_px": margins_px,
        "paddings_px": paddings_px,
        "has_mark_labels": bool(referenced & mark_label_worksheets),
    }


def mine_dashboards(paths: list[Path]) -> list[dict[str, Any]]:
    """Mine one per-dashboard record for every REGULAR dashboard (storyboards excluded; see
    :func:`mine_stories`) across ``paths``.

    Deterministic like ``design_miner.mine_workbooks``: inputs are processed sorted by basename,
    and the output is sorted by ``(source, xpath)``, so re-running with the same input set (in
    any order) yields the same records in the same order.
    """
    ordered = sorted(paths, key=lambda p: p.name)
    records: list[dict[str, Any]] = []
    for path in ordered:
        raw = read_twb_bytes(path)
        sha256 = hashlib.sha256(raw).hexdigest()
        root = etree.fromstring(raw, parser=safe_parser())
        tree = root.getroottree()
        mark_label_worksheets = _worksheets_with_mark_labels(root)
        for dashboard_el in root.findall(".//dashboard"):
            if dashboard_el.get("type") == "storyboard":
                continue
            record = _mine_dashboard_record(
                tree, dashboard_el, path.name, sha256, mark_label_worksheets
            )
            if record is not None:
                records.append(record)
    records.sort(key=lambda r: (str(r["source"]), str(r["xpath"])))
    return records


# ---------------------------------------------------------------------------
# Story-level mining
# ---------------------------------------------------------------------------


def _story_record_for_workbook(tree: Any, root: Any, source: str, sha256: str) -> dict[str, Any]:
    """One per-WORKBOOK story-usage record, covering every ``<dashboard type='storyboard'>``
    found anywhere in it (a workbook may have zero, one, or several).
    """
    story_points_per_story: list[int] = []
    caption_lengths: list[int] = []
    nav_types: list[str] = []
    for dashboard_el in root.findall(".//dashboard"):
        if dashboard_el.get("type") != "storyboard":
            continue
        points = dashboard_el.findall(".//story-points/story-point")
        story_points_per_story.append(len(points))
        caption_lengths.extend(len(p.get("caption", "")) for p in points)
        flipboard = dashboard_el.find(".//flipboard")
        if flipboard is not None:
            nav_types.append(flipboard.get("nav-type", ""))
    return {
        "source": source,
        "sha256": sha256,
        "xpath": tree.getpath(root),
        "has_story": bool(story_points_per_story),
        "story_points_per_story": story_points_per_story,
        "caption_lengths": caption_lengths,
        "nav_types": nav_types,
    }


def mine_stories(paths: list[Path]) -> list[dict[str, Any]]:
    """Mine one per-WORKBOOK story-usage record for every path in ``paths``.

    Unlike :func:`mine_dashboards`, this always emits exactly ``len(paths)`` records -- one per
    input file, even when that workbook has zero storyboards (``has_story: False``, empty
    lists) -- because ``design_stats.py`` needs the full corpus as the denominator to compute a
    usage RATE (``n_workbooks_with_story / n_workbooks_total``), not just a count over the
    workbooks that happen to have one.

    Deterministic: inputs processed sorted by basename; output sorted by ``source``.
    """
    ordered = sorted(paths, key=lambda p: p.name)
    records: list[dict[str, Any]] = []
    for path in ordered:
        raw = read_twb_bytes(path)
        sha256 = hashlib.sha256(raw).hexdigest()
        root = etree.fromstring(raw, parser=safe_parser())
        tree = root.getroottree()
        records.append(_story_record_for_workbook(tree, root, path.name, sha256))
    records.sort(key=lambda r: str(r["source"]))
    return records
