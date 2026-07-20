"""Tests for sidecar/design_stats.py (Slice T1 — corpus statistical aggregation).

Covers:
- Stats primitives: ``percentile`` (linear interpolation), ``confidence`` (n<15 -> "low"),
  ``distribution``, ``rate``, ``citations`` (dedup by source, deterministic order, capped).
- ``stratum_for_width`` boundaries (<900, 900-1400, >1400).
- Aggregation math (``aggregate_dashboard_norms`` / ``aggregate_story_norms``) on synthetic
  in-memory records — stratification, the n<15 confidence guard, and story usage-rate gating
  on the positive-sample count, not the corpus denominator.
- End-to-end: mining the small fixture through ``design_dashboard_miner.mine_dashboards``/
  ``mine_stories`` and aggregating it, verifying provenance citations are present.
- Determinism: aggregating the same records twice, and dumping via
  ``design_miner.dump_yaml``, produces byte-identical output.
- Input resolution: ``root_reference_paths`` (excludes subdirectories), ``top100_paths``
  (manifest-driven, ``status: downloaded`` only, missing-on-disk skipped), and
  ``extra_reference_paths`` ("if present" semantics).
- ``append_notable_constructs``: only NEW, high-frequency (>=8 files), content-deduplicated
  constructs are appended; low-frequency and already-present constructs are not; the
  per-file cap is enforced; a recipe type with nothing clearing the bar is left untouched.
- CLI ``main()`` end-to-end smoke test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import design_dashboard_miner as ddm
import design_miner as dm
import design_stats as ds

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_TWB = FIXTURES_DIR / "design_miner_fixture.twb"

# ---------------------------------------------------------------------------
# Synthetic workbook builder (small, parametrizable — no dependency on any
# real downloaded corpus, so these tests are fully hermetic/offline).
# ---------------------------------------------------------------------------


def _make_twb(
    path: Path,
    canvas_width: int,
    canvas_height: int = 1000,
    title_fontsize: float = 20.0,
    zone_style_border_color: str = "#222222",
    with_mark_labels: bool = False,
) -> Path:
    mark_labels_worksheet = ""
    if with_mark_labels:
        mark_labels_worksheet = (
            "<worksheets><worksheet name='WS'><table><panes><pane><style>"
            "<style-rule element='mark'><format attr='mark-labels-show' value='true'/></style-rule>"
            "</style></pane></panes></table></worksheet></worksheets>"
        )
    xml = f"""<workbook>
  {mark_labels_worksheet}
  <dashboards>
    <dashboard name='D'>
      <size maxheight='{canvas_height}' maxwidth='{canvas_width}' minheight='{canvas_height}'
            minwidth='{canvas_width}' sizing-mode='fixed'/>
      <zones>
        <zone h='100000' id='1' type-v2='empty' w='100000' x='0' y='0'>
          <zone-style>
            <format attr='border-color' value='{zone_style_border_color}'/>
            <format attr='margin' value='4'/>
          </zone-style>
        </zone>
        <zone h='5000' id='2' type-v2='text' w='100000' x='0' y='0'>
          <formatted-text>
            <run bold='true' fontcolor='#111111' fontsize='{title_fontsize}'>Title</run>
          </formatted-text>
        </zone>
      </zones>
    </dashboard>
  </dashboards>
</workbook>"""
    path.write_text(xml, encoding="utf-8")
    return path


def _dashboard_record(**overrides: Any) -> dict[str, Any]:
    """A minimal synthetic dashboard record matching mine_dashboards()'s output shape."""
    base: dict[str, Any] = {
        "source": "synthetic.twbx",
        "sha256": "0" * 64,
        "xpath": "/workbook/dashboards/dashboard[1]",
        "name": "Synthetic",
        "canvas_width": 1000,
        "canvas_height": 800,
        "sizing_mode": "fixed",
        "zone_type_counts": {"text": 1},
        "max_nesting_depth": 1,
        "title_zone": {"height_ratio": 0.05, "fontsize": 20.0, "bold": True, "color": "#ffffff"},
        "kpi_band_rows": [],
        "has_device_layouts": False,
        "filter_zone_count": 0,
        "paramctrl_zone_count": 0,
        "margins_px": [4],
        "paddings_px": [0],
        "has_mark_labels": False,
    }
    base.update(overrides)
    return base


def _story_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": "synthetic.twbx",
        "sha256": "0" * 64,
        "xpath": "/workbook",
        "has_story": False,
        "story_points_per_story": [],
        "caption_lengths": [],
        "nav_types": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Stats primitives
# ---------------------------------------------------------------------------


def test_percentile_median_of_odd_and_even_length_lists() -> None:
    assert ds.percentile([1.0, 2.0, 3.0], 50) == 2.0
    assert ds.percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)


def test_percentile_p25_p75_linear_interpolation() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert ds.percentile(values, 25) == pytest.approx(20.0)
    assert ds.percentile(values, 75) == pytest.approx(40.0)


def test_percentile_single_value_returns_that_value() -> None:
    assert ds.percentile([42.0], 25) == 42.0
    assert ds.percentile([42.0], 75) == 42.0


def test_confidence_boundary_at_min_n() -> None:
    assert ds.confidence(ds.MIN_CONFIDENT_N - 1) == "low"
    assert ds.confidence(ds.MIN_CONFIDENT_N) == "ok"
    assert ds.confidence(0) == "low"


def test_distribution_empty_is_low_confidence_with_none_quantiles() -> None:
    result = ds.distribution([])
    assert result == {"n": 0, "confidence": "low", "median": None, "p25": None, "p75": None}


def test_distribution_reports_n_and_quantiles() -> None:
    result = ds.distribution([float(v) for v in range(1, 16)])  # n=15 -> "ok"
    assert result["n"] == 15
    assert result["confidence"] == "ok"
    assert result["median"] == 8.0


def test_rate_zero_total_is_none_rate_not_a_crash() -> None:
    assert ds.rate(0, 0) == {"n": 0, "confidence": "low", "count": 0, "total": 0, "rate": None}


def test_rate_computes_ratio_and_confidence() -> None:
    result = ds.rate(3, 20)
    assert result["rate"] == 0.15
    assert result["confidence"] == "ok"


def test_citations_dedups_by_source_and_respects_limit() -> None:
    records = [
        {"source": "a.twbx", "sha256": "aa", "xpath": "/workbook/dashboards/dashboard[1]"},
        {"source": "a.twbx", "sha256": "aa", "xpath": "/workbook/dashboards/dashboard[2]"},
        {"source": "b.twbx", "sha256": "bb", "xpath": "/workbook/dashboards/dashboard[1]"},
        {"source": "c.twbx", "sha256": "cc", "xpath": "/workbook/dashboards/dashboard[1]"},
    ]
    result = ds.citations(records, limit=2)
    assert len(result) == 2
    assert [c["source"] for c in result] == ["a.twbx", "b.twbx"]
    assert result[0] == {
        "source": "a.twbx",
        "sha256": "aa",
        "xpath": "/workbook/dashboards/dashboard[1]",
    }


def test_citations_on_empty_records_is_empty_list() -> None:
    assert ds.citations([]) == []


# ---------------------------------------------------------------------------
# stratum_for_width
# ---------------------------------------------------------------------------


def test_stratum_for_width_boundaries() -> None:
    assert ds.stratum_for_width(899) == "<900"
    assert ds.stratum_for_width(900) == "900-1400"
    assert ds.stratum_for_width(1400) == "900-1400"
    assert ds.stratum_for_width(1401) == ">1400"


# ---------------------------------------------------------------------------
# aggregate_dashboard_norms — synthetic inputs
# ---------------------------------------------------------------------------


def test_aggregate_dashboard_norms_stratifies_by_canvas_width() -> None:
    records = [_dashboard_record(canvas_width=800, source="narrow0.twbx")]
    records += [_dashboard_record(canvas_width=1000, source=f"mid{i}.twbx") for i in range(3)]
    records += [_dashboard_record(canvas_width=1600, source=f"wide{i}.twbx") for i in range(2)]

    norms = ds.aggregate_dashboard_norms(records)
    assert norms["strata"]["<900"]["n"] == 1
    assert norms["strata"]["900-1400"]["n"] == 3
    assert norms["strata"][">1400"]["n"] == 2
    assert norms["n_dashboards"] == 6


def test_aggregate_dashboard_norms_min_n_confidence_guard() -> None:
    """A stratum with < 15 records is confidence: low; >= 15 is "ok"."""
    low_n = [_dashboard_record(canvas_width=1000, source=f"low{i}.twbx") for i in range(5)]
    high_n = [_dashboard_record(canvas_width=1000, source=f"high{i}.twbx") for i in range(15)]

    low_norms = ds.aggregate_dashboard_norms(low_n)
    high_norms = ds.aggregate_dashboard_norms(high_n)

    assert low_norms["strata"]["900-1400"]["confidence"] == "low"
    assert low_norms["strata"]["900-1400"]["title_fontsize"]["confidence"] == "low"
    assert high_norms["strata"]["900-1400"]["confidence"] == "ok"
    assert high_norms["strata"]["900-1400"]["title_fontsize"]["confidence"] == "ok"


def test_aggregate_dashboard_norms_title_fontsize_median() -> None:
    records = [
        _dashboard_record(
            canvas_width=1000,
            source=f"r{i}.twbx",
            title_zone={"height_ratio": 0.05, "fontsize": fs, "bold": True, "color": "#000"},
        )
        for i, fs in enumerate([10.0, 20.0, 30.0])
    ]
    norms = ds.aggregate_dashboard_norms(records)
    bucket = norms["strata"]["900-1400"]["title_fontsize"]
    assert bucket["median"] == 20.0
    assert bucket["n"] == 3
    assert len(bucket["citations"]) == 3


def test_aggregate_dashboard_norms_excludes_dashboards_with_no_title_zone_from_title_stats() -> (
    None
):
    records = [
        _dashboard_record(canvas_width=1000, source="has_title.twbx"),
        _dashboard_record(canvas_width=1000, source="no_title.twbx", title_zone=None),
    ]
    norms = ds.aggregate_dashboard_norms(records)
    bucket = norms["strata"]["900-1400"]
    assert bucket["n"] == 2  # both dashboards count toward the stratum...
    assert bucket["title_fontsize"]["n"] == 1  # ...but only one has a title zone


def test_aggregate_dashboard_norms_kpi_band_is_corpus_wide_not_stratified() -> None:
    records = [
        _dashboard_record(
            canvas_width=900 + i * 200,
            source=f"kpi{i}.twbx",
            kpi_band_rows=[{"height_ratio": 0.2, "child_count": 3}],
        )
        for i in range(4)
    ]
    norms = ds.aggregate_dashboard_norms(records)
    assert norms["kpi_band"]["n"] == 4
    assert norms["kpi_band"]["confidence"] == "low"  # n=4 < 15
    assert norms["kpi_band"]["height_ratio"]["median"] == pytest.approx(0.2)
    assert norms["kpi_band"]["child_count"]["median"] == 3.0
    assert len(norms["kpi_band"]["citations"]) == 4


def test_aggregate_dashboard_norms_usage_rates() -> None:
    records = [
        _dashboard_record(
            canvas_width=1000, source="f0.twbx", filter_zone_count=1, has_device_layouts=True
        ),
        _dashboard_record(
            canvas_width=1000, source="f1.twbx", filter_zone_count=0, has_device_layouts=False
        ),
    ]
    norms = ds.aggregate_dashboard_norms(records)
    bucket = norms["strata"]["900-1400"]
    assert bucket["filter_zone_usage_rate"]["count"] == 1
    assert bucket["filter_zone_usage_rate"]["rate"] == 0.5
    assert bucket["device_layout_usage_rate"]["rate"] == 0.5


# ---------------------------------------------------------------------------
# aggregate_story_norms — synthetic inputs
# ---------------------------------------------------------------------------


def test_aggregate_story_norms_usage_rate_gates_on_positive_sample_not_denominator() -> None:
    """94 workbooks with 0 stories -> usage_rate n=0 (low confidence), even though the
    denominator (n_workbooks) is far above the 15-sample floor."""
    records = [_story_record(source=f"w{i}.twbx") for i in range(94)]
    norms = ds.aggregate_story_norms(records)

    assert norms["n_workbooks"] == 94
    assert norms["usage_rate"]["count"] == 0
    assert norms["usage_rate"]["total"] == 94
    assert norms["usage_rate"]["rate"] == 0.0
    assert norms["usage_rate"]["n"] == 0
    assert norms["usage_rate"]["confidence"] == "low"


def test_aggregate_story_norms_with_some_stories() -> None:
    records = [_story_record(source=f"w{i}.twbx") for i in range(8)]
    records += [
        _story_record(
            source="s0.twbx",
            has_story=True,
            story_points_per_story=[4],
            caption_lengths=[10, 20, 30, 40],
            nav_types=["caption"],
        ),
        _story_record(
            source="s1.twbx",
            has_story=True,
            story_points_per_story=[2],
            caption_lengths=[5, 15],
            nav_types=["number"],
        ),
    ]
    norms = ds.aggregate_story_norms(records)

    assert norms["n_workbooks"] == 10
    assert norms["usage_rate"]["count"] == 2
    assert norms["usage_rate"]["rate"] == 0.2
    assert norms["points_per_story"]["n"] == 2
    assert norms["points_per_story"]["median"] == 3.0
    assert norms["caption_length"]["n"] == 6
    assert norms["nav_type_distribution"] == {"caption": 1, "number": 1}
    assert len(norms["citations"]) == 2


# ---------------------------------------------------------------------------
# End-to-end via the real small fixture (mine -> aggregate), + determinism
# ---------------------------------------------------------------------------


def test_end_to_end_fixture_dashboard_norms_has_provenance_citations() -> None:
    dashboard_records = ddm.mine_dashboards([FIXTURE_TWB])
    norms = ds.aggregate_dashboard_norms(dashboard_records)

    bucket = norms["strata"]["900-1400"]  # fixture canvas width is 1200
    assert bucket["n"] == 1
    assert bucket["confidence"] == "low"  # n=1 < 15
    citation = bucket["title_fontsize"]["citations"]
    assert citation == [
        {
            "source": "design_miner_fixture.twb",
            "sha256": dashboard_records[0]["sha256"],
            "xpath": "/workbook/dashboards/dashboard[1]",
        }
    ]


def test_end_to_end_fixture_story_norms_usage_rate() -> None:
    story_records = ddm.mine_stories([FIXTURE_TWB])
    norms = ds.aggregate_story_norms(story_records)
    assert norms["n_workbooks"] == 1
    assert norms["usage_rate"]["count"] == 1
    assert norms["usage_rate"]["rate"] == 1.0
    assert norms["citations"][0]["source"] == "design_miner_fixture.twb"


def test_aggregating_and_dumping_twice_is_byte_identical() -> None:
    dashboard_records = ddm.mine_dashboards([FIXTURE_TWB])
    story_records = ddm.mine_stories([FIXTURE_TWB])

    first = dm.dump_yaml(
        {
            "dashboards": ds.aggregate_dashboard_norms(dashboard_records),
            "stories": ds.aggregate_story_norms(story_records),
        }
    )
    second = dm.dump_yaml(
        {
            "dashboards": ds.aggregate_dashboard_norms(dashboard_records),
            "stories": ds.aggregate_story_norms(story_records),
        }
    )
    assert first == second

    # Also order-independent at the mining stage.
    reversed_dashboard_records = ddm.mine_dashboards(list(reversed([FIXTURE_TWB])))
    assert dashboard_records == reversed_dashboard_records


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def test_root_reference_paths_excludes_subdirectories_and_other_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.twbx").write_text("x")
    (tmp_path / "b.twb").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    subdir = tmp_path / "top100"
    subdir.mkdir()
    (subdir / "c.twbx").write_text("x")

    result = ds.root_reference_paths(tmp_path)
    assert [p.name for p in result] == ["a.twbx", "b.twb"]


def test_root_reference_paths_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert ds.root_reference_paths(tmp_path / "does_not_exist") == []


def test_top100_paths_filters_by_downloaded_status_and_skips_missing_on_disk(
    tmp_path: Path,
) -> None:
    top100_dir = tmp_path / "top100"
    top100_dir.mkdir()
    (top100_dir / "Present.twbx").write_text("x")
    # "MissingOnDisk" is in the manifest as downloaded, but no file exists for it.
    manifest = {
        "items": [
            {"repoUrl": "Present", "status": "downloaded"},
            {"repoUrl": "MissingOnDisk", "status": "downloaded"},
            {"repoUrl": "Disabled", "status": "download-disabled"},
        ]
    }
    manifest_path = top100_dir / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest))

    paths, n_downloaded, n_missing = ds.top100_paths(top100_dir, manifest_path)
    assert [p.name for p in paths] == ["Present.twbx"]
    assert n_downloaded == 2
    assert n_missing == 1


def test_top100_paths_missing_manifest_returns_empty(tmp_path: Path) -> None:
    paths, n_downloaded, n_missing = ds.top100_paths(tmp_path, tmp_path / "manifest.yaml")
    assert (paths, n_downloaded, n_missing) == ([], 0, 0)


def test_extra_reference_paths_skips_missing_if_present(tmp_path: Path) -> None:
    present = tmp_path / "present.twb"
    present.write_text("x")
    missing = str(tmp_path / "missing.twb")

    paths, skipped = ds.extra_reference_paths([str(present), missing])
    assert [p.name for p in paths] == ["present.twb"]
    assert skipped == [missing]


# ---------------------------------------------------------------------------
# append_notable_constructs
# ---------------------------------------------------------------------------


def _seed_recipe_files(corpus_dir: Path, entries_by_name: dict[str, list[dict[str, Any]]]) -> None:
    recipes_dir = corpus_dir / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    for name in dm.RECIPE_NAMES:
        (recipes_dir / f"{name}.yaml").write_text(
            dm.dump_recipe_yaml(entries_by_name.get(name, [])), encoding="utf-8"
        )


def test_append_notable_constructs_appends_new_high_frequency_and_skips_low_frequency(
    tmp_path: Path,
) -> None:
    corpus_dir = tmp_path / "corpus"
    _seed_recipe_files(corpus_dir, {})

    workbooks_dir = tmp_path / "workbooks"
    workbooks_dir.mkdir()
    paths = []
    # A common construct (border-color=#222222) present in 9 files -- clears the >=8 floor.
    for i in range(9):
        paths.append(_make_twb(workbooks_dir / f"common{i}.twb", canvas_width=1000))
    # A rare construct (border-color=#999999) present in only 2 files -- does NOT clear the bar.
    for i in range(2):
        paths.append(
            _make_twb(
                workbooks_dir / f"rare{i}.twb", canvas_width=1000, zone_style_border_color="#999999"
            )
        )

    report = ds.append_notable_constructs(paths, corpus_dir)

    assert report["zone_styles"]["found"] == 1
    assert report["zone_styles"]["appended"] == 1
    assert report["palettes"] == {"found": 0, "appended": 0}  # nothing clears the bar -- skipped

    zone_styles = yaml.safe_load((corpus_dir / "recipes" / "zone_styles.yaml").read_text())
    assert len(zone_styles) == 1
    assert zone_styles[0]["formats"]["border-color"] == "#222222"

    # Nothing rare made it in.
    rare_present = any(e["formats"].get("border-color") == "#999999" for e in zone_styles)
    assert rare_present is False


def test_append_notable_constructs_does_not_re_append_already_present_construct(
    tmp_path: Path,
) -> None:
    corpus_dir = tmp_path / "corpus"
    workbooks_dir = tmp_path / "workbooks"
    workbooks_dir.mkdir()
    paths = [_make_twb(workbooks_dir / f"w{i}.twb", canvas_width=1000) for i in range(9)]

    # Pre-seed zone_styles.yaml with the EXACT construct these 9 files would otherwise
    # contribute as "new" -- provenance from an allowlisted-style existing source.
    existing_entry = {
        "zone_type": "empty",
        "formats": {"border-color": "#222222", "margin": "4"},
        "source": "already_seeded.twbx",
        "sha256": "f" * 64,
        "xpath": "/workbook/dashboards/dashboard[1]/zones/zone[1]/zone-style",
    }
    _seed_recipe_files(corpus_dir, {"zone_styles": [existing_entry]})

    report = ds.append_notable_constructs(paths, corpus_dir)

    assert report["zone_styles"] == {"found": 0, "appended": 0}
    zone_styles = yaml.safe_load((corpus_dir / "recipes" / "zone_styles.yaml").read_text())
    assert zone_styles == [existing_entry]


def test_append_notable_constructs_caps_new_entries_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds, "NOTABLE_CONSTRUCT_MAX_NEW_PER_FILE", 1)
    corpus_dir = tmp_path / "corpus"
    _seed_recipe_files(corpus_dir, {})

    workbooks_dir = tmp_path / "workbooks"
    workbooks_dir.mkdir()
    paths = []
    for i in range(8):
        paths.append(
            _make_twb(
                workbooks_dir / f"a{i}.twb", canvas_width=1000, zone_style_border_color="#aaaaaa"
            )
        )
    for i in range(8):
        paths.append(
            _make_twb(
                workbooks_dir / f"b{i}.twb", canvas_width=1000, zone_style_border_color="#bbbbbb"
            )
        )

    report = ds.append_notable_constructs(paths, corpus_dir)
    assert report["zone_styles"]["found"] == 2
    assert report["zone_styles"]["appended"] == 1  # capped

    zone_styles = yaml.safe_load((corpus_dir / "recipes" / "zone_styles.yaml").read_text())
    assert len(zone_styles) == 1


def test_append_notable_constructs_finds_high_frequency_chrome_rule(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    _seed_recipe_files(corpus_dir, {})

    workbooks_dir = tmp_path / "workbooks"
    workbooks_dir.mkdir()
    paths = [
        _make_twb(workbooks_dir / f"w{i}.twb", canvas_width=1000, with_mark_labels=True)
        for i in range(10)
    ]

    report = ds.append_notable_constructs(paths, corpus_dir)
    assert report["chrome_rules"]["appended"] == 1

    chrome_rules = yaml.safe_load((corpus_dir / "recipes" / "chrome_rules.yaml").read_text())
    assert len(chrome_rules) == 1
    assert chrome_rules[0]["formats"] == [{"attr": "mark-labels-show", "value": "true"}]


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


def test_cli_main_writes_stats_files_and_appends_notable_constructs(tmp_path: Path) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    top100_dir = refs_dir / "top100"
    top100_dir.mkdir()
    corpus_dir = tmp_path / "corpus"
    _seed_recipe_files(corpus_dir, {})

    for i in range(9):
        _make_twb(top100_dir / f"T{i}.twb", canvas_width=1000)
    manifest = {"items": [{"repoUrl": f"T{i}", "status": "downloaded"} for i in range(9)]}
    (top100_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))

    exit_code = ds.main(
        [
            "--refs-dir",
            str(refs_dir),
            "--top100-dir",
            str(top100_dir),
            "--out",
            str(corpus_dir),
        ]
    )
    assert exit_code == 0

    dashboard_norms_path = corpus_dir / "stats" / "dashboard_norms.yaml"
    story_norms_path = corpus_dir / "stats" / "story_norms.yaml"
    assert dashboard_norms_path.exists()
    assert story_norms_path.exists()

    dashboard_norms = yaml.safe_load(dashboard_norms_path.read_text())
    assert dashboard_norms["corpus_size"] == 9
    assert dashboard_norms["n_dashboards"] == 9

    zone_styles = yaml.safe_load((corpus_dir / "recipes" / "zone_styles.yaml").read_text())
    assert len(zone_styles) == 1  # the shared construct across all 9 synthetic files


def test_cli_main_skip_notable_constructs_leaves_recipes_untouched(tmp_path: Path) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    top100_dir = refs_dir / "top100"
    top100_dir.mkdir()
    corpus_dir = tmp_path / "corpus"
    _seed_recipe_files(corpus_dir, {})

    for i in range(9):
        _make_twb(top100_dir / f"T{i}.twb", canvas_width=1000)
    manifest = {"items": [{"repoUrl": f"T{i}", "status": "downloaded"} for i in range(9)]}
    (top100_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))

    before = (corpus_dir / "recipes" / "zone_styles.yaml").read_text()
    exit_code = ds.main(
        [
            "--refs-dir",
            str(refs_dir),
            "--top100-dir",
            str(top100_dir),
            "--out",
            str(corpus_dir),
            "--skip-notable-constructs",
        ]
    )
    after = (corpus_dir / "recipes" / "zone_styles.yaml").read_text()
    assert exit_code == 0
    assert before == after


def test_cli_main_reports_error_when_no_inputs_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    top100_dir = refs_dir / "top100"
    top100_dir.mkdir()

    exit_code = ds.main(
        [
            "--refs-dir",
            str(refs_dir),
            "--top100-dir",
            str(top100_dir),
            "--out",
            str(tmp_path / "corpus"),
        ]
    )
    assert exit_code == 1
    assert "no input workbooks found" in capsys.readouterr().err
