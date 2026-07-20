"""Offline CLI: aggregate the design-excellence corpus into deterministic design-norm statistics.

Slice T1 (PLAN.md's Top-100 Corpus plan). This is a **dev-only** tool, exactly like
``design_miner.py`` — never imported by ``server.py``, ``twb_builder.py``, or any runtime path.
It reads the same ``.twb``/``.twbx`` inputs ``design_miner.py`` reads (never committed; covered
by the repo-root ``.gitignore``) and writes two deterministic YAML artifacts under
``design/corpus/stats/`` — ``dashboard_norms.yaml`` and ``story_norms.yaml`` — which ARE
committed and are what downstream slices (T2/T3) read.

Usage
-----
::

    python design_stats.py \\
        --refs-dir ../design/references \\
        --top100-dir ../design/references/top100 \\
        --extra-refs <path>... \\
        --out ../design/corpus

Input resolution
-----------------
- **Root exemplars**: every ``.twb``/``.twbx`` directly under ``--refs-dir`` (NOT its
  ``top100/`` subdirectory — that one is handled by the manifest-driven rule below).
- **Top-100**: every ``design/references/top100/manifest.yaml`` item with
  ``status: downloaded`` whose saved file (``<repoUrl>.twbx`` or ``.twb``) still exists on disk
  under ``--top100-dir``. A manifest entry whose file went missing (e.g. manually cleaned up)
  is skipped, not an error — the manifest is the provenance record, not a promise every byte is
  still present.
- **Extra refs** (``--extra-refs``, optional, zero or more paths): additional workbooks to
  fold in (e.g. pre-existing reference ``.twb`` files staged outside the repo). Missing paths
  are skipped with a note, never an error — "if present" is the contract.

Confidence guard (plan mandate)
--------------------------------
Every aggregated bucket carries ``n`` and ``confidence``: ``"ok"`` when ``n >= MIN_CONFIDENT_N``
(15), else ``"low"``. A ``confidence: low`` bucket is a signal for a human, or a future slice's
explicit judgment call — it is never auto-applied to themes/the builder (see ``GAPS.md`` /
``SCHEMA.md``).

Determinism
------------
No wall-clock timestamp is embedded in either output file (unlike a typical "generated at"
header) specifically so that ``aggregate twice with the same corpus -> byte-identical output``
holds, mirroring ``design_miner.py``'s own zero-wall-clock-fields precedent. The corpus
composition itself (``sources`` in the header) is the reproducibility record. Every list is
sorted (records by ``(source, xpath)``, dict keys via ``design_miner.dump_yaml``'s
``sort_keys=True``) before being dumped.

Notable-construct append (plan mandate, scoped)
-------------------------------------------------
``append_notable_constructs`` mines the FULL input corpus for ``zone_styles``/``chrome_rules``/
``palettes`` (the three CONTENT-DEDUPLICATED recipe types — see ``SCHEMA.md``'s dedup policy)
and appends any construct that is (a) not already present in the committed
``design/corpus/recipes/*.yaml`` and (b) independently observed in at least
``NOTABLE_CONSTRUCT_MIN_FILES`` distinct source files (a deliberate frequency floor — roughly
8.5% of a ~94-file corpus, chosen to be comfortably above one-off noise while still low enough
to surface genuinely-common real-world constructs; see the T1 slice report for the exact
counts this threshold produced). Capped at ``NOTABLE_CONSTRUCT_MAX_NEW_PER_FILE`` (200) new
entries per file, deterministically sorted. ``actions.yaml``/``text_zones.yaml`` are
intentionally EXCLUDED from this step: both are already NOT content-deduplicated by design
(every action/text-zone is individually meaningful — see ``design_miner.py``'s module
docstring), so a "how many files repeat this exact content" frequency floor does not apply to
them the same way; appending every top-100 action/text-zone verbatim would be exactly the
"repo bloat" the plan explicitly warns against. If nothing clears the frequency bar for a given
recipe type, that type is skipped entirely (documented in the CLI's printed report, not
silently no-opped).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

import design_dashboard_miner as ddm
import design_miner as dm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Plan mandate: a bucket with fewer than this many samples is never confident enough to
# auto-apply -- routed to GAPS.md / left `confidence: low` for a human/future slice instead.
MIN_CONFIDENT_N = 15

CANVAS_WIDTH_STRATA: tuple[str, ...] = ("<900", "900-1400", ">1400")

NOTABLE_CONSTRUCT_MIN_FILES = 8
NOTABLE_CONSTRUCT_MAX_NEW_PER_FILE = 200
NOTABLE_CONSTRUCT_RECIPE_NAMES: tuple[str, ...] = ("zone_styles", "chrome_rules", "palettes")


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def root_reference_paths(refs_dir: Path) -> list[Path]:
    """Direct-child ``.twb``/``.twbx`` files under ``refs_dir`` (its subdirectories, e.g.
    ``top100/``, are handled separately -- see :func:`top100_paths`)."""
    if not refs_dir.is_dir():
        return []
    return sorted(
        (p for p in refs_dir.iterdir() if p.is_file() and p.suffix.lower() in (".twb", ".twbx")),
        key=lambda p: p.name,
    )


def top100_paths(top100_dir: Path, manifest_path: Path) -> tuple[list[Path], int, int]:
    """Paths for every manifest item with ``status: downloaded`` still present on disk.

    Returns ``(paths, n_manifest_downloaded, n_missing_on_disk)`` so the caller can report the
    manifest/disk delta (e.g. a file removed after the manifest was written).
    """
    if not manifest_path.exists():
        return [], 0, 0
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    items = manifest.get("items", []) if isinstance(manifest, dict) else []
    downloaded = [it for it in items if isinstance(it, dict) and it.get("status") == "downloaded"]

    paths: list[Path] = []
    missing = 0
    for item in downloaded:
        repo_url = str(item.get("repoUrl", ""))
        twbx_path = top100_dir / f"{repo_url}.twbx"
        twb_path = top100_dir / f"{repo_url}.twb"
        if twbx_path.exists():
            paths.append(twbx_path)
        elif twb_path.exists():
            paths.append(twb_path)
        else:
            missing += 1
    return sorted(paths, key=lambda p: p.name), len(downloaded), missing


def extra_reference_paths(extra_refs: list[str]) -> tuple[list[Path], list[str]]:
    """Optional extra reference paths. Missing ones are skipped (not an error) -- "if present"."""
    present: list[Path] = []
    skipped: list[str] = []
    for raw in extra_refs:
        candidate = Path(raw)
        if candidate.exists():
            present.append(candidate)
        else:
            skipped.append(raw)
    return sorted(present, key=lambda p: p.name), skipped


# ---------------------------------------------------------------------------
# Small stats primitives
# ---------------------------------------------------------------------------


def confidence(n: int) -> str:
    """``"ok"`` when ``n`` meets the plan's minimum-sample floor, else ``"low"``."""
    return "ok" if n >= MIN_CONFIDENT_N else "low"


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (the common "linear" method) over ``values``.

    ``values`` must be non-empty -- callers only invoke this after checking ``len(values) > 0``
    (see :func:`distribution`, which handles the empty case itself).
    """
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0]
    rank = (pct / 100) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def distribution(values: list[float]) -> dict[str, Any]:
    """``{n, confidence, median, p25, p75}`` over ``values`` (n=0 -> all quantiles ``None``)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "confidence": "low", "median": None, "p25": None, "p75": None}
    return {
        "n": n,
        "confidence": confidence(n),
        "median": round(percentile(values, 50), 4),
        "p25": round(percentile(values, 25), 4),
        "p75": round(percentile(values, 75), 4),
    }


def rate(count: int, total: int) -> dict[str, Any]:
    """``{n, confidence, count, total, rate}`` -- a usage-rate bucket over ``total`` samples."""
    return {
        "n": total,
        "confidence": confidence(total),
        "count": count,
        "total": total,
        "rate": round(count / total, 4) if total else None,
    }


def citations(records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    """Up to ``limit`` provenance citations (``{source, sha256, xpath}``), one per DISTINCT
    source workbook (sorted by ``(source, xpath)``) -- "top-5 exemplar citations", not just the
    first 5 records (which could all be the same workbook if it has many dashboards).
    """
    ordered = sorted(records, key=lambda r: (str(r["source"]), str(r["xpath"])))
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in ordered:
        source = str(r["source"])
        if source in seen:
            continue
        seen.add(source)
        out.append({"source": source, "sha256": str(r["sha256"]), "xpath": str(r["xpath"])})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# dashboard_norms.yaml
# ---------------------------------------------------------------------------


def stratum_for_width(width: int) -> str:
    """Which of the 3 canvas-width strata (plan-mandated buckets) ``width`` falls into."""
    if width < 900:
        return "<900"
    if width <= 1400:
        return "900-1400"
    return ">1400"


def _aggregate_stratum(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)

    title_records = [r for r in records if r["title_zone"] is not None]
    fontsize_records = [r for r in title_records if r["title_zone"]["fontsize"] is not None]
    height_records = [r for r in title_records if r["title_zone"]["height_ratio"] is not None]

    margin_records = [r for r in records for _ in r["margins_px"]]
    margins = [float(m) for r in records for m in r["margins_px"]]
    padding_records = [r for r in records for _ in r["paddings_px"]]
    paddings = [float(p) for r in records for p in r["paddings_px"]]

    sizing_modes: Counter[str] = Counter(r["sizing_mode"] for r in records if r["sizing_mode"])
    n_filter = sum(1 for r in records if r["filter_zone_count"] > 0)
    n_paramctrl = sum(1 for r in records if r["paramctrl_zone_count"] > 0)
    n_device = sum(1 for r in records if r["has_device_layouts"])
    n_mark_labels = sum(1 for r in records if r["has_mark_labels"])

    return {
        "n": n,
        "confidence": confidence(n),
        "title_fontsize": {
            **distribution([float(r["title_zone"]["fontsize"]) for r in fontsize_records]),
            "citations": citations(fontsize_records),
        },
        "title_height_ratio": {
            **distribution([float(r["title_zone"]["height_ratio"]) for r in height_records]),
            "citations": citations(height_records),
        },
        "canvas_width_px": {
            **distribution([float(r["canvas_width"]) for r in records]),
            "citations": citations(records),
        },
        "canvas_height_px": {
            **distribution([float(r["canvas_height"]) for r in records]),
            "citations": citations(records),
        },
        "margin_px": {**distribution(margins), "citations": citations(margin_records)},
        "padding_px": {**distribution(paddings), "citations": citations(padding_records)},
        "sizing_mode_distribution": dict(sorted(sizing_modes.items())),
        "filter_zone_usage_rate": rate(n_filter, n),
        "paramctrl_zone_usage_rate": rate(n_paramctrl, n),
        "device_layout_usage_rate": rate(n_device, n),
        "mark_label_usage_rate": rate(n_mark_labels, n),
    }


def aggregate_dashboard_norms(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate ``design_dashboard_miner.mine_dashboards()`` output into
    ``dashboard_norms.yaml``'s body.

    Stratified by canvas width (``<900``, ``900-1400``, ``>1400`` -- plan-mandated buckets):
    title fontsize, title-zone height ratio, canvas size, margin/padding medians, sizing-mode
    distribution, and filter/paramctrl/device-layout/mark-label usage rates. KPI-band-like rows
    are corpus-wide (not stratified by width) -- the real corpus yields very few rows total (see
    the T1 slice report), too few to meaningfully sub-divide by stratum on top of the existing
    ``confidence: low`` guard.
    """
    strata = {
        name: _aggregate_stratum(
            [r for r in records if stratum_for_width(r["canvas_width"]) == name]
        )
        for name in CANVAS_WIDTH_STRATA
    }

    kpi_pairs = [(r, row) for r in records for row in r["kpi_band_rows"]]
    kpi_heights = [
        float(row["height_ratio"]) for _, row in kpi_pairs if row["height_ratio"] is not None
    ]
    kpi_children = [float(row["child_count"]) for _, row in kpi_pairs]
    kpi_records = [r for r, _ in kpi_pairs]

    return {
        "n_dashboards": len(records),
        "strata": strata,
        "kpi_band": {
            "n": len(kpi_pairs),
            "confidence": confidence(len(kpi_pairs)),
            "height_ratio": distribution(kpi_heights),
            "child_count": distribution(kpi_children),
            "citations": citations(kpi_records),
        },
    }


# ---------------------------------------------------------------------------
# story_norms.yaml
# ---------------------------------------------------------------------------


def aggregate_story_norms(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate ``design_dashboard_miner.mine_stories()`` output into ``story_norms.yaml``'s body.

    ``usage_rate``'s ``n``/``confidence`` deliberately gate on the NUMERATOR (workbooks that
    actually have a story), not the (much larger) total-corpus denominator -- a rate estimated
    from zero or a handful of positive examples is exactly as unreliable to extrapolate from as
    any other n<15 bucket, regardless of how big the denominator is (see SCHEMA.md).
    """
    total = len(records)
    with_story = [r for r in records if r["has_story"]]
    n_with_story = len(with_story)

    points_per_story = [float(c) for r in records for c in r["story_points_per_story"]]
    caption_lengths = [float(c) for r in records for c in r["caption_lengths"]]
    nav_types: Counter[str] = Counter(nt for r in records for nt in r["nav_types"] if nt)

    return {
        "n_workbooks": total,
        "usage_rate": {
            "n": n_with_story,
            "confidence": confidence(n_with_story),
            "count": n_with_story,
            "total": total,
            "rate": round(n_with_story / total, 4) if total else None,
        },
        "points_per_story": distribution(points_per_story),
        "caption_length": distribution(caption_lengths),
        "nav_type_distribution": dict(sorted(nav_types.items())),
        "citations": citations(with_story),
    }


# ---------------------------------------------------------------------------
# Notable-construct append (see module docstring for full scope/rationale)
# ---------------------------------------------------------------------------


def _content_key(recipe_name: str, entry: dict[str, Any]) -> tuple[Any, ...]:
    """The same content-dedup key each recipe type already uses in ``design_miner.py``."""
    if recipe_name == "zone_styles":
        return ("zone_style", entry["zone_type"], tuple(sorted(entry["formats"].items())))
    if recipe_name == "chrome_rules":
        formats_sig = tuple(tuple(sorted(f.items())) for f in entry["formats"])
        return ("chrome_rule", entry["scope"], entry["element"], formats_sig)
    if recipe_name == "palettes":
        return ("palette", entry["name"], entry["type"], tuple(entry["colors"]))
    raise ValueError(f"notable-construct append is not defined for recipe type {recipe_name!r}")


def _per_file_frequency(paths: list[Path], recipe_name: str) -> Counter[tuple[Any, ...]]:
    """In how many distinct input files does each content-key appear at least once."""
    freq: Counter[tuple[Any, ...]] = Counter()
    for path in paths:
        mined = dm.mine_workbooks([path])
        seen = {_content_key(recipe_name, e) for e in mined[recipe_name]}
        freq.update(seen)
    return freq


def append_notable_constructs(paths: list[Path], corpus_dir: Path) -> dict[str, dict[str, int]]:
    """Mine ``paths`` for new, high-frequency ``zone_styles``/``chrome_rules``/``palettes``
    constructs and append them to the committed ``design/corpus/recipes/*.yaml`` files under
    ``corpus_dir``. See the module docstring for the full scope/threshold rationale.

    Returns a per-recipe-type ``{found, appended}`` report (``found`` may exceed ``appended``
    when the 200-new-entries-per-file cap is hit).
    """
    full = dm.mine_workbooks(paths)
    report: dict[str, dict[str, int]] = {}

    for name in NOTABLE_CONSTRUCT_RECIPE_NAMES:
        recipe_path = corpus_dir / "recipes" / f"{name}.yaml"
        existing = yaml.safe_load(recipe_path.read_text(encoding="utf-8")) or []
        existing_keys = {_content_key(name, e) for e in existing}

        freq = _per_file_frequency(paths, name)
        candidates = [
            e
            for e in full[name]
            if _content_key(name, e) not in existing_keys
            and freq[_content_key(name, e)] >= NOTABLE_CONSTRUCT_MIN_FILES
        ]
        candidates.sort(key=lambda e: (str(e["source"]), str(e["xpath"])))
        capped = candidates[:NOTABLE_CONSTRUCT_MAX_NEW_PER_FILE]
        report[name] = {"found": len(candidates), "appended": len(capped)}

        if not capped:
            continue
        merged = existing + capped
        merged.sort(key=lambda e: (str(e["source"]), str(e["xpath"])))
        recipe_path.write_text(dm.dump_recipe_yaml(merged), encoding="utf-8")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="design_stats.py",
        description=(
            "Aggregate the design-excellence corpus (root exemplars + manifest-driven top-100 "
            "+ optional extra refs) into deterministic dashboard/story design-norm statistics."
        ),
    )
    parser.add_argument(
        "--refs-dir", required=True, help="Directory holding the root exemplar .twb/.twbx files."
    )
    parser.add_argument(
        "--top100-dir", required=True, help="Directory holding the top-100 corpus + manifest.yaml."
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to manifest.yaml (default: <top100-dir>/manifest.yaml).",
    )
    parser.add_argument(
        "--extra-refs",
        nargs="*",
        default=[],
        help="Additional .twb/.twbx paths to fold in, if present.",
    )
    parser.add_argument(
        "--out", required=True, help="Output corpus directory (stats/ is created under it)."
    )
    parser.add_argument(
        "--skip-notable-constructs",
        action="store_true",
        help="Skip the notable-construct append step (aggregation only; recipes/*.yaml untouched).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    refs_dir = Path(args.refs_dir)
    top100_dir = Path(args.top100_dir)
    manifest_path = Path(args.manifest) if args.manifest else top100_dir / "manifest.yaml"
    out_dir = Path(args.out)

    root_paths = root_reference_paths(refs_dir)
    top100_files, n_downloaded, n_missing = top100_paths(top100_dir, manifest_path)
    extra_paths, extra_skipped = extra_reference_paths(args.extra_refs)

    all_paths = sorted(
        {p.resolve() for p in root_paths + top100_files + extra_paths}, key=lambda p: p.name
    )
    if not all_paths:
        print("error: no input workbooks found", file=sys.stderr)
        return 1

    dashboard_records = ddm.mine_dashboards(all_paths)
    story_records = ddm.mine_stories(all_paths)

    dashboard_norms = aggregate_dashboard_norms(dashboard_records)
    story_norms = aggregate_story_norms(story_records)

    header: dict[str, Any] = {
        "corpus_size": len(all_paths),
        "sources": {
            "root_refs": [p.name for p in root_paths],
            "top100_manifest_downloaded": n_downloaded,
            "top100_missing_on_disk": n_missing,
            "extra_refs_used": [p.name for p in extra_paths],
            "extra_refs_skipped_missing": extra_skipped,
        },
    }

    stats_dir = out_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "dashboard_norms.yaml").write_text(
        dm.dump_yaml({**header, **dashboard_norms}), encoding="utf-8"
    )
    (stats_dir / "story_norms.yaml").write_text(
        dm.dump_yaml({**header, **story_norms}), encoding="utf-8"
    )

    print(f"corpus_size: {len(all_paths)}")
    print(f"n_dashboards: {dashboard_norms['n_dashboards']}")
    print(f"n_workbooks_with_story: {story_norms['usage_rate']['count']}")

    if not args.skip_notable_constructs:
        notable_report = append_notable_constructs(all_paths, out_dir)
        for name, counts in notable_report.items():
            print(
                f"notable_constructs[{name}]: found={counts['found']} appended={counts['appended']}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
