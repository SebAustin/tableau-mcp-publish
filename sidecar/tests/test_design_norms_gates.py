"""Design Excellence, Slice T2 (PLAN.md's Top-100 Corpus plan) — mined-norm
CI gates for the beauty-gate round 2 "title too big" defect.

Two independent, machine-checkable closure conditions, cross-referenced with
`tests/planner-titleShorten.test.ts` (the TS-side length half of the same
fix):

1. `test_themed_title_fontsize_at_or_below_mined_norm` — the themed header
   title run's fontsize, for a DEFAULT-BRAND build (no `brand=` override),
   must be <= the 900-1400-stratum (dashboard-sized canvases) stratified
   median title fontsize mined from the top-100 corpus
   (`design/corpus/stats/dashboard_norms.yaml`).
2. `test_themed_header_zone_height_matches_mined_title_height_ratio` — the
   themed header zone's `h` attribute (0-100000 grid) must equal the mined
   `title_height_ratio` median for the same stratum, rounded to the nearest
   integer grid unit.

Both PARSE THE COMMITTED STATS FILE AT TEST TIME (not a hardcoded copy of
the numbers), so a future corpus refresh that shifts either mined value
fails this test instead of silently drifting out of sync with the evidence
it claims to follow — exactly the "norm drift fails CI" discipline the plan
mandates.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

import twb_builder
from server import DesignThemeModel

STATS_PATH = (
    Path(__file__).parent.parent.parent / "design" / "corpus" / "stats" / "dashboard_norms.yaml"
)

SHEETS_BASIC = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARD_TITLE_SUBTITLE = [
    {
        "name": "Dashboard 1",
        "titles": ["Revenue by Region"],
        "title": "Executive Overview",
        "subtitle": "Q4 Performance",
        "text_zones": [],
        "layout_grammar": None,
    }
]


def _theme(**overrides: object) -> dict[str, object]:
    """Same discipline as test_twb_header_zone.py's ``_theme()`` helper."""
    base: dict[str, object] = {"name": "executive_dark"}
    base.update(overrides)
    return DesignThemeModel(**base).model_dump()  # type: ignore[arg-type]


THEME_WITH_HEADER = _theme(
    header={
        "background": "#2f2e41",
        "title_color": "#ffffff",
        "subtitle_color": "#ffffff",
    }
)


def _load_dashboard_stratum(stratum_key: str) -> dict[str, Any]:
    with STATS_PATH.open(encoding="utf-8") as f:
        stats = yaml.safe_load(f)
    stratum: dict[str, Any] = stats["strata"][stratum_key]
    return stratum


def _header_zone(root: ET.Element) -> ET.Element:
    zone = root.find(".//dashboards/dashboard/zones//zone[@type-v2='text']")
    assert zone is not None, "no <zone type-v2='text'> found"
    return zone


# ---------------------------------------------------------------------------
# Gate 1 — themed title fontsize <= mined 900-1400 stratified median
# ---------------------------------------------------------------------------


def test_themed_title_fontsize_at_or_below_mined_norm() -> None:
    stratum = _load_dashboard_stratum("900-1400")
    title_fontsize = stratum["title_fontsize"]
    assert title_fontsize["confidence"] == "ok", (
        "900-1400 title_fontsize stratum dropped below the T1 plan's n>=15 "
        "confidence floor -- this gate's premise (a stable stratified median "
        "to check against) no longer holds. Route to design/corpus/GAPS.md "
        "for a documented judgment call instead of silently passing/failing "
        "against a low-confidence bucket."
    )
    median = float(title_fontsize["median"])

    # Default-brand build: no `brand=` kwarg -> _title_or_subtitle_run_attrs
    # falls back to its hardcoded default (fontsize=20), exactly the build
    # `design_dashboard`/`build_from_plan` produce for a plan with no brand
    # typography override.
    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    root = ET.fromstring(xml)
    run = _header_zone(root).find("formatted-text").find("run")
    assert run is not None
    fontsize = float(run.get("fontsize", "0"))

    assert fontsize <= median, (
        f"themed header title fontsize {fontsize} exceeds the mined 900-1400 "
        f"stratified median {median} (design/corpus/stats/dashboard_norms.yaml) "
        "-- beauty-gate 'title too big' regression."
    )


# ---------------------------------------------------------------------------
# Gate 2 — themed header zone height matches the mined title_height_ratio
# ---------------------------------------------------------------------------


def test_themed_header_zone_height_matches_mined_title_height_ratio() -> None:
    stratum = _load_dashboard_stratum("900-1400")
    height_ratio = stratum["title_height_ratio"]
    assert height_ratio["confidence"] == "ok", (
        "900-1400 title_height_ratio stratum dropped below the T1 plan's "
        "n>=15 confidence floor -- route to design/corpus/GAPS.md instead of "
        "silently passing/failing against a low-confidence bucket."
    )
    expected_h = round(float(height_ratio["median"]) * 100_000)

    xml = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_BASIC,
        dashboards=DASHBOARD_TITLE_SUBTITLE,
        design_theme=THEME_WITH_HEADER,
    )
    root = ET.fromstring(xml)
    zone = _header_zone(root)
    actual_h = int(zone.get("h", "0"))

    assert actual_h == expected_h, (
        f"themed header zone h={actual_h} does not match the mined 900-1400 "
        f"title_height_ratio median ({height_ratio['median']} * 100000 = "
        f"{expected_h}, design/corpus/stats/dashboard_norms.yaml) -- "
        "sidecar/twb_builder.py's _HEADER_ZONE_H has drifted from the mined "
        "evidence it's supposed to mirror."
    )
