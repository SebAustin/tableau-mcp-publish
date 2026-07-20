"""Tests for sidecar/design_dashboard_miner.py (Slice T1 — dashboard/story-level mining).

Covers, against the shared committed fixture (``tests/fixtures/design_miner_fixture.twb``)'s
"Fixture Dashboard" (canvas size, KPI-band-like row, filter/paramctrl zones, device layouts) and
"Fixture Story" (storyboard, 2 story-points) dashboards:

- ``mine_dashboards()``: storyboard exclusion, canvas/provenance, zone-type counts, nesting
  depth, title-zone detection (top-15%-of-canvas band), KPI-band-like row detection,
  filter/paramctrl/device-layout fields, margin/padding extraction, mark-label cross-reference,
  exclusion of dashboards with no parseable ``<size>``, determinism.
- ``mine_stories()``: one record per input file (even with zero storyboards), story-point/
  caption/nav-type extraction, determinism.

Both fixture dashboards were added to ``design_miner_fixture.twb`` verbatim from the real shapes
documented in ``design/corpus/SCHEMA.md`` and ``sidecar/twb_builder.py``'s ``_build_story``
docstring — see ``test_design_miner.py``'s module docstring for the D0 construct provenance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import design_dashboard_miner as ddm

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_TWB = FIXTURES_DIR / "design_miner_fixture.twb"


def _fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE_TWB.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# mine_dashboards()
# ---------------------------------------------------------------------------


def _only_dashboard() -> dict[str, object]:
    records = ddm.mine_dashboards([FIXTURE_TWB])
    assert len(records) == 1, f"expected exactly 1 regular dashboard, got {len(records)}"
    return records[0]


def test_mine_dashboards_excludes_storyboards() -> None:
    """Only 'Fixture Dashboard' is a regular dashboard; 'Fixture Story' (type='storyboard')
    must never appear in mine_dashboards() output."""
    records = ddm.mine_dashboards([FIXTURE_TWB])
    names = [r["name"] for r in records]
    assert names == ["Fixture Dashboard"]


def test_mine_dashboard_record_canvas_and_provenance() -> None:
    record = _only_dashboard()
    assert record["canvas_width"] == 1200
    assert record["canvas_height"] == 1000
    assert record["sizing_mode"] == "fixed"
    assert record["source"] == "design_miner_fixture.twb"
    assert record["sha256"] == _fixture_sha256()
    assert record["xpath"] == "/workbook/dashboards/dashboard[1]"


def test_mine_dashboard_record_zone_type_counts_and_nesting_depth() -> None:
    record = _only_dashboard()
    assert record["zone_type_counts"] == {
        "empty": 1,
        "text": 1,
        "layout-flow": 1,
        "worksheet": 3,
        "filter": 1,
        "paramctrl": 1,
    }
    # The KPI-band row (depth 1) nests 3 worksheet-reference zones (depth 2) beneath it.
    assert record["max_nesting_depth"] == 2


def test_mine_dashboard_record_title_zone_in_top_band() -> None:
    record = _only_dashboard()
    title_zone = record["title_zone"]
    assert title_zone is not None
    assert title_zone["height_ratio"] == pytest.approx(4167 / 100_000)
    assert title_zone["fontsize"] == 20.0
    assert title_zone["bold"] is True
    assert title_zone["color"] == "#ffffff"


def test_mine_dashboard_record_kpi_band_row_detected() -> None:
    record = _only_dashboard()
    assert record["kpi_band_rows"] == [{"height_ratio": pytest.approx(0.2), "child_count": 3}]


def test_mine_dashboard_record_filter_paramctrl_and_device_layouts() -> None:
    record = _only_dashboard()
    assert record["filter_zone_count"] == 1
    assert record["paramctrl_zone_count"] == 1
    assert record["has_device_layouts"] is True


def test_mine_dashboard_record_margins_paddings_and_mark_labels() -> None:
    record = _only_dashboard()
    # The one existing <zone-style> block carries margin='0' and no padding attr.
    assert record["margins_px"] == [0]
    assert record["paddings_px"] == []
    # One of the KPI band's referenced worksheets ("Fixture Sheet") has mark-labels-show=true.
    assert record["has_mark_labels"] is True


def test_mine_dashboards_excludes_dashboard_without_parseable_size(tmp_path: Path) -> None:
    """A dashboard with no <size> at all is excluded, not crashed on or coerced into a bucket."""
    no_size_twb = tmp_path / "no_size.twb"
    no_size_twb.write_text(
        "<workbook><dashboards><dashboard name='No Size'><zones>"
        "<zone h='100000' id='1' type-v2='empty' w='100000' x='0' y='0'/>"
        "</zones></dashboard></dashboards></workbook>"
    )
    assert ddm.mine_dashboards([no_size_twb]) == []


def test_mine_dashboards_is_deterministic_and_order_independent(tmp_path: Path) -> None:
    second = tmp_path / "zzz_another.twb"
    second.write_text(FIXTURE_TWB.read_text().replace("Fixture Dashboard", "Another Dashboard"))

    forward = ddm.mine_dashboards([FIXTURE_TWB, second])
    reversed_order = ddm.mine_dashboards([second, FIXTURE_TWB])
    assert forward == reversed_order

    again = ddm.mine_dashboards([FIXTURE_TWB, second])
    assert forward == again


# ---------------------------------------------------------------------------
# mine_stories()
# ---------------------------------------------------------------------------


def test_mine_stories_emits_one_record_per_input_file() -> None:
    """Unlike mine_dashboards(), mine_stories() always emits len(paths) records -- one per
    workbook, even ones with zero storyboards -- so design_stats.py has a full-corpus
    denominator for the usage-rate calculation."""
    records = ddm.mine_stories([FIXTURE_TWB])
    assert len(records) == 1


def test_mine_stories_finds_the_storyboard_with_points_captions_and_nav_type() -> None:
    record = ddm.mine_stories([FIXTURE_TWB])[0]
    assert record["has_story"] is True
    assert record["story_points_per_story"] == [2]
    assert record["caption_lengths"] == [len("Fixture Point One"), len("Fixture Point Two")]
    assert record["nav_types"] == ["caption"]
    assert record["source"] == "design_miner_fixture.twb"
    assert record["sha256"] == _fixture_sha256()
    assert record["xpath"] == "/workbook"


def test_mine_stories_reports_no_story_for_a_workbook_without_one(tmp_path: Path) -> None:
    no_story_twb = tmp_path / "no_story.twb"
    no_story_twb.write_text("<workbook><dashboards/></workbook>")

    record = ddm.mine_stories([no_story_twb])[0]
    assert record["has_story"] is False
    assert record["story_points_per_story"] == []
    assert record["caption_lengths"] == []
    assert record["nav_types"] == []


def test_mine_stories_is_deterministic_and_order_independent(tmp_path: Path) -> None:
    second = tmp_path / "zzz_another.twb"
    second.write_text("<workbook><dashboards/></workbook>")

    forward = ddm.mine_stories([FIXTURE_TWB, second])
    reversed_order = ddm.mine_stories([second, FIXTURE_TWB])
    assert forward == reversed_order
