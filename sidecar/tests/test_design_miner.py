"""Tests for sidecar/design_miner.py (D0 — design corpus foundation).

Covers:
- Round-trip extraction of all 5 constructs (zone-style, style-rule, action,
  custom palette, dashboard text zone) from the committed fixture
  (``tests/fixtures/design_miner_fixture.twb``), with correct provenance
  (``source``/``source_file``, ``sha256``, ``xpath``) on every entry.
- ``.twbx`` (zip) input support, reading the inner ``.twb`` member into memory.
- Zip-slip rejection: a crafted in-memory ``.twbx`` with a path-traversal
  member name must be rejected outright (the whole archive, not just the bad
  member).
- Determinism: mining + serializing the same input set twice (in any order)
  produces byte-identical YAML.

Every construct in the fixture is copied verbatim from a real mined exemplar
(see ``design/corpus/recipes/*.yaml`` for the equivalent real entries):
- zone-style: WB-117 (WB-117), navy
  band background-color #2f2e41.
- style-rule (workbook, element=title): same source, font-size 11 / color
  #2f2e41.
- style-rule (worksheet, element=mark): WB-015, mark-labels-show.
- action (tsc:tsl-filter): WB-117's
  "Clear Selection - Pag All Left" action shape.
- custom palette: same source's ordered-sequential navy palette.
- text zone: same source's "Superstore |" header run (bold, #ffffff, 20pt,
  Tableau Semibold).

Slice T1's dashboard-level / story-level mining (``design_dashboard_miner.mine_dashboards()`` /
``mine_stories()``) is covered in the sibling test file ``test_design_dashboard_miner.py``,
against this same fixture's "Fixture Dashboard" (canvas size, KPI-band-like row,
filter/paramctrl zones, device layouts) and "Fixture Story" (storyboard, 2 story-points)
dashboards -- both added to the fixture verbatim from the real shapes documented in
``design/corpus/SCHEMA.md`` and ``sidecar/twb_builder.py``'s ``_build_story`` docstring.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
import yaml

import design_miner as dm

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_TWB = FIXTURES_DIR / "design_miner_fixture.twb"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fixture_sha256() -> str:
    return hashlib.sha256(FIXTURE_TWB.read_bytes()).hexdigest()


def _only(entries: list[dict[str, object]]) -> dict[str, object]:
    assert len(entries) == 1, f"expected exactly one entry, got {len(entries)}: {entries}"
    return entries[0]


# ---------------------------------------------------------------------------
# Round-trip: every construct, with correct provenance
# ---------------------------------------------------------------------------


def test_zone_style_round_trips_with_provenance() -> None:
    recipes = dm.mine_workbooks([FIXTURE_TWB])
    entry = _only(recipes["zone_styles"])

    assert entry["zone_type"] == "empty"
    assert entry["formats"]["background-color"] == "#2f2e41"
    assert entry["formats"]["margin"] == "0"
    assert entry["source"] == "design_miner_fixture.twb"
    assert entry["sha256"] == _fixture_sha256()
    # dashboard[1] (not bare "dashboard") since the fixture now has a 2nd <dashboard> sibling
    # (the T1 storyboard fixture, added for test_design_stats.py's mine_stories() coverage).
    assert entry["xpath"] == "/workbook/dashboards/dashboard[1]/zones/zone[1]/zone-style"


def test_chrome_rules_capture_both_workbook_and_worksheet_scope() -> None:
    recipes = dm.mine_workbooks([FIXTURE_TWB])
    entries = recipes["chrome_rules"]
    assert len(entries) == 2

    by_scope = {e["scope"]: e for e in entries}
    assert set(by_scope) == {"workbook", "worksheet"}

    workbook_rule = by_scope["workbook"]
    assert workbook_rule["element"] == "title"
    assert {"attr": "color", "value": "#2f2e41"} in workbook_rule["formats"]
    assert workbook_rule["xpath"] == "/workbook/style/style-rule"

    worksheet_rule = by_scope["worksheet"]
    assert worksheet_rule["element"] == "mark"
    assert {"attr": "mark-labels-show", "value": "true"} in worksheet_rule["formats"]
    # Lives under <table><panes><pane><style>, not the shallower <table><style> —
    # this is exactly the real-world nesting the miner's widened sweep exists for.
    assert "panes/pane/style/style-rule" in worksheet_rule["xpath"]

    for entry in entries:
        assert entry["source"] == "design_miner_fixture.twb"
        assert entry["sha256"] == _fixture_sha256()


def test_action_round_trips_tsl_filter_shape_with_provenance() -> None:
    recipes = dm.mine_workbooks([FIXTURE_TWB])
    entry = _only(recipes["actions"])

    assert entry["kind"] == "filter"
    assert entry["caption"] == "Clear Selection - Fixture"
    assert entry["activation"] == {"type": "on-select", "auto_clear": "true"}
    assert entry["source"] == {
        "type": "sheet",
        "worksheet": "Fixture Sheet",
        "dashboard": "Fixture Dashboard",
    }
    assert entry["command"] == {
        "command": "tsc:tsl-filter",
        "params": {"target": "Fixture Sheet"},
    }
    assert entry["link"]["multi-select"] == "true"
    # Provenance uses source_file (not source) because `source` already holds
    # the mined <source> XML sub-object.
    assert entry["source_file"] == "design_miner_fixture.twb"
    assert entry["sha256"] == _fixture_sha256()
    assert entry["xpath"] == "/workbook/actions/action"


def test_palette_round_trips_with_provenance() -> None:
    recipes = dm.mine_workbooks([FIXTURE_TWB])
    entry = _only(recipes["palettes"])

    assert entry["type"] == "ordered-sequential"
    assert entry["colors"] == ["#f1f1f1", "#2f2e41"]
    assert entry["source"] == "design_miner_fixture.twb"
    assert entry["sha256"] == _fixture_sha256()
    assert entry["xpath"] == "/workbook/datasources/datasource/color-palette"


def test_text_zone_round_trips_title_hint_and_provenance() -> None:
    recipes = dm.mine_workbooks([FIXTURE_TWB])
    entry = _only(recipes["text_zones"])

    assert entry["runs"] == [
        {
            "bold": "true",
            "fontcolor": "#ffffff",
            "fontname": "Tableau Semibold",
            "fontsize": "20",
            "text": "Fixture Title",
        }
    ]
    # First (and only) dashboard text zone -> title, by the "first zone" heuristic.
    assert entry["zone_role_hint"] == "title"
    assert entry["source"] == "design_miner_fixture.twb"
    assert entry["sha256"] == _fixture_sha256()


# ---------------------------------------------------------------------------
# .twbx support (zip extraction, in-memory only)
# ---------------------------------------------------------------------------


def test_twbx_input_mines_the_same_entries_as_the_raw_twb(tmp_path: Path) -> None:
    twbx_path = tmp_path / "fixture.twbx"
    with zipfile.ZipFile(twbx_path, "w") as zf:
        zf.writestr("design_miner_fixture.twb", FIXTURE_TWB.read_bytes())

    from_twbx = dm.mine_workbooks([twbx_path])
    from_twb = dm.mine_workbooks([FIXTURE_TWB])

    # sha256/source differ (basename), everything else must be identical.
    assert from_twbx["zone_styles"][0]["formats"] == from_twb["zone_styles"][0]["formats"]
    assert from_twbx["zone_styles"][0]["source"] == "fixture.twbx"
    assert (
        from_twbx["zone_styles"][0]["sha256"] == from_twb["zone_styles"][0]["sha256"]
    ), "sha256 must be of the inner .twb bytes, not the .twbx zip"


def test_twbx_with_no_twb_member_raises(tmp_path: Path) -> None:
    twbx_path = tmp_path / "empty.twbx"
    with zipfile.ZipFile(twbx_path, "w") as zf:
        zf.writestr("readme.txt", "not a workbook")
    with pytest.raises(ValueError, match="no .twb member"):
        dm.read_twb_bytes(twbx_path)


# ---------------------------------------------------------------------------
# Zip-slip rejection
# ---------------------------------------------------------------------------


def test_zip_slip_member_rejects_whole_archive(tmp_path: Path) -> None:
    evil_path = tmp_path / "evil.twbx"
    with zipfile.ZipFile(evil_path, "w") as zf:
        zf.writestr("../evil", "pwned")
        zf.writestr("good.twb", "<workbook/>")

    with pytest.raises(dm.ZipSlipError, match=r"\.\./evil"):
        dm.read_twb_bytes(evil_path)


def test_zip_slip_absolute_path_member_rejects_whole_archive(tmp_path: Path) -> None:
    evil_path = tmp_path / "evil_abs.twbx"
    with zipfile.ZipFile(evil_path, "w") as zf:
        zf.writestr("/etc/evil.twb", "<workbook/>")

    with pytest.raises(dm.ZipSlipError):
        dm.read_twb_bytes(evil_path)


def test_zip_slip_guard_does_not_reject_safe_archives(tmp_path: Path) -> None:
    safe_path = tmp_path / "safe.twbx"
    with zipfile.ZipFile(safe_path, "w") as zf:
        zf.writestr("Image/logo.png", b"\x89PNG")
        zf.writestr("safe.twb", "<workbook/>")

    # Must not raise.
    raw = dm.read_twb_bytes(safe_path)
    assert raw == b"<workbook/>"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_mining_is_deterministic_across_repeated_runs() -> None:
    first = dm.mine_workbooks([FIXTURE_TWB])
    second = dm.mine_workbooks([FIXTURE_TWB])
    assert first == second

    first_yaml = {name: dm.dump_recipe_yaml(entries) for name, entries in first.items()}
    second_yaml = {name: dm.dump_recipe_yaml(entries) for name, entries in second.items()}
    assert first_yaml == second_yaml


def test_write_recipes_produces_byte_identical_output_on_repeated_runs(tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    recipes = dm.mine_workbooks([FIXTURE_TWB])
    dm.write_recipes(recipes, out1)
    dm.write_recipes(dm.mine_workbooks([FIXTURE_TWB]), out2)

    for name in dm.RECIPE_NAMES:
        bytes1 = (out1 / "recipes" / f"{name}.yaml").read_bytes()
        bytes2 = (out2 / "recipes" / f"{name}.yaml").read_bytes()
        assert bytes1 == bytes2, f"{name}.yaml is not byte-identical across runs"


def test_input_order_does_not_affect_output(tmp_path: Path) -> None:
    """Determinism holds regardless of the order paths are passed on the CLI."""
    second_twb = tmp_path / "zzz_another.twb"
    second_twb.write_text(FIXTURE_TWB.read_text().replace("Fixture Title", "Another Title"))

    forward = dm.mine_workbooks([FIXTURE_TWB, second_twb])
    reversed_order = dm.mine_workbooks([second_twb, FIXTURE_TWB])
    assert forward == reversed_order


# ---------------------------------------------------------------------------
# YAML output shape (sorted keys, sorted entries)
# ---------------------------------------------------------------------------


def test_dumped_yaml_is_valid_and_sorted_by_source_and_xpath(tmp_path: Path) -> None:
    second_twb = tmp_path / "aaa_first.twb"
    second_twb.write_text(FIXTURE_TWB.read_text())

    recipes = dm.mine_workbooks([FIXTURE_TWB, second_twb])
    dumped = dm.dump_recipe_yaml(recipes["zone_styles"])
    loaded = yaml.safe_load(dumped)

    assert isinstance(loaded, list)
    sources = [e["source"] for e in loaded]
    assert sources == sorted(sources), "entries must be sorted by (source, xpath)"


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------


def test_cli_main_writes_recipes_and_reports_missing_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "corpus"
    exit_code = dm.main([str(FIXTURE_TWB), "--out", str(out_dir)])
    assert exit_code == 0

    for name in dm.RECIPE_NAMES:
        assert (out_dir / "recipes" / f"{name}.yaml").exists()

    captured = capsys.readouterr()
    assert "zone_styles: 1 entries" in captured.out


def test_cli_main_reports_missing_input_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.twb"
    exit_code = dm.main([str(missing), "--out", str(tmp_path / "out")])
    assert exit_code == 1
