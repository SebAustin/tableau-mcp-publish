"""Offline CLI: mine Tableau design vocabulary from real ``.twb``/``.twbx`` workbooks.

Part of the design-excellence corpus (ADR-0013). This is a **dev-only** tool —
it is never imported by ``server.py``, ``twb_builder.py``, or any runtime path.
Its output (deterministic YAML under ``design/corpus/recipes/``) is what gets
committed and consumed by hand-curated themes; the ``.twb``/``.twbx`` inputs
themselves are never committed (covered by the repo-root ``*.twb``/``*.twbx``
``.gitignore`` entries).

Usage
-----
::

    python design_miner.py <twb-or-twbx>... --out design/corpus

Every mined entry carries ``source`` (the input file's basename), ``sha256``
(of the *inner* ``.twb`` XML bytes — for a ``.twbx`` input this is the hash of
the extracted member, not the zip), and ``xpath`` (``ElementTree.getpath()``
from the parsed document). The miner copies attribute values **verbatim**: it
never invents, renames, or normalizes a value beyond a whitespace strip. This
mirrors the "never invent XML" discipline used throughout ``twb_builder.py``.

Security posture
-----------------
- **XXE-safe parsing**: the same hardened ``lxml.etree.XMLParser`` flags used
  by ``tests/test_twb_schema_validation.py`` (``resolve_entities=False``,
  ``no_network=True``, ``load_dtd=False``). ``huge_tree=True`` is set here
  (unlike the XSD-validation parser) because real exemplar workbooks run into
  the multi-megabyte range and lxml's default depth/size guards reject them;
  the trade-off is acceptable because this tool only ever runs offline,
  locally, against files the operator chose to mine.
- **Zip-slip guard**: every member name in a ``.twbx`` archive is validated
  *before* any member is read. If **any** member name looks like a path
  traversal (absolute path, or a ``..`` path segment), the **entire archive**
  is rejected — we fail closed rather than silently skipping just the bad
  member and processing the rest, since a crafted archive that mixes one
  malicious entry with otherwise-valid entries is exactly the attack this
  guard exists to catch. Only the first ``*.twb`` member found is ever read,
  and only into memory; nothing is extracted to disk.

Determinism
-----------
Input paths are sorted by basename before processing (independent of the
order given on the command line or via shell globbing), and every recipe's
output is dict/list-sorted before being dumped. Running the miner twice with
the same input set produces byte-identical YAML.

Dedup policy (documented deviation from a fully literal "mine everything")
----------------------------------------------------------------------------
Real workbooks repeat trivial box-model boilerplate (e.g. the same
``border-color:#000000;border-style:none;border-width:0;margin:0`` zone-style
appears dozens of times per file). Emitting every raw occurrence would make
``zone_styles.yaml`` and ``chrome_rules.yaml`` enormous and useless as a
*human-reviewable* corpus (the stated design goal — see ADR-0013), so:

- ``zone_styles`` / ``chrome_rules`` / ``palettes`` are deduplicated **by
  content** (the format/attribute set, not just file identity) — one entry
  per distinct construct, with the first occurrence (in sorted-input,
  document order) kept as the canonical provenance. This directly matches the
  plan's explicit "every *distinct* ``<zone-style>`` block" wording; the same
  content-based policy is extended to ``chrome_rules``/``palettes`` for the
  same human-reviewability reason (a deliberate, documented interpretation —
  see the D0 slice report).
- ``actions`` / ``text_zones`` are **not** content-deduplicated (every real
  action and every real text run is individually meaningful — two titles with
  identical formatting but different text must both survive). These are only
  deduplicated when the *same* file is mined twice under different names
  (matched by ``(sha256, xpath)``, e.g. the repo's ``WB-062`` /
  ``WB-063`` pair, which are byte-identical).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from lxml import etree  # type: ignore[import-untyped]

RECIPE_NAMES = ("zone_styles", "chrome_rules", "actions", "palettes", "text_zones")

# Text-zone role heuristic thresholds (documented, not mined — see module docstring).
TITLE_FONT_SIZE_MIN = 14.0
SUBTITLE_FONT_SIZE_MIN = 11.0

# <command command="..."> -> action "kind". Only the two shapes verified present
# in the mined exemplars are mapped explicitly; anything else falls back to "url"
# (the third schema-legal <action> kind — never invented, only classified).
ACTION_KIND_BY_COMMAND = {
    "tsc:tsl-filter": "filter",
    "tsc:brush": "brush",
}


class ZipSlipError(ValueError):
    """Raised when a ``.twbx`` archive contains a path-traversal member name."""


# ---------------------------------------------------------------------------
# Parsing / extraction plumbing
# ---------------------------------------------------------------------------


def safe_parser() -> Any:
    """Hardened lxml parser: entities OFF, network OFF, DTD OFF, huge_tree ON.

    Mirrors ``tests/test_twb_schema_validation.py``'s ``_safe_parser()`` except
    ``huge_tree=True`` — real exemplar workbooks exceed lxml's default
    depth/size ceiling; this tool only ever runs offline against
    operator-chosen files, so the trade-off is documented, not silent.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=True,
    )


def _is_unsafe_member(name: str) -> bool:
    """True if a zip member name could escape the extraction target (zip-slip)."""
    if name.startswith("/") or name.startswith("\\"):
        return True
    normalized = name.replace("\\", "/")
    return ".." in normalized.split("/")


def read_twb_bytes(path: Path) -> bytes:
    """Return the raw ``.twb`` XML bytes for a ``.twb`` or ``.twbx`` input path.

    For ``.twbx``, every archive member name is validated against zip-slip
    *before* any member is read; if any member is unsafe the whole archive is
    rejected (``ZipSlipError``). Only the first ``*.twb`` member is read, and
    only into memory — nothing is ever written to disk.
    """
    if path.suffix.lower() != ".twbx":
        return path.read_bytes()

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        unsafe = [n for n in names if _is_unsafe_member(n)]
        if unsafe:
            raise ZipSlipError(f"{path}: unsafe member path(s) in archive: {unsafe!r}")
        twb_names = [n for n in names if n.lower().endswith(".twb")]
        if not twb_names:
            raise ValueError(f"{path}: no .twb member found in archive")
        return zf.read(twb_names[0])


# ---------------------------------------------------------------------------
# zone_styles.yaml
# ---------------------------------------------------------------------------


def _zone_style_formats(zone_style: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in zone_style.findall("format"):
        attr, value = f.get("attr"), f.get("value")
        if attr is None or value is None:
            continue
        out[attr.strip()] = value.strip()
    return out


def _mine_zone_styles(
    tree: Any, root: Any, source: str, sha256: str, out: dict[tuple[Any, ...], dict[str, Any]]
) -> None:
    for zs in root.findall(".//zone-style"):
        formats = _zone_style_formats(zs)
        if not formats:
            continue
        parent = zs.getparent()
        zone_type = parent.get("type-v2", "worksheet") if parent is not None else "worksheet"
        key = ("zone_style", zone_type, tuple(sorted(formats.items())))
        if key in out:
            continue
        out[key] = {
            "zone_type": zone_type,
            "formats": formats,
            "source": source,
            "sha256": sha256,
            "xpath": tree.getpath(zs),
        }


# ---------------------------------------------------------------------------
# chrome_rules.yaml
# ---------------------------------------------------------------------------


def _style_rule_formats(style_rule: Any) -> list[dict[str, str]]:
    formats: list[dict[str, str]] = []
    for f in style_rule.findall("format"):
        attr, value = f.get("attr"), f.get("value")
        if attr is None or value is None:
            continue
        entry = {"attr": attr.strip(), "value": value.strip()}
        scope = f.get("scope")
        if scope:
            entry["scope"] = scope.strip()
        formats.append(entry)
    return formats


def _add_chrome_rule(
    tree: Any,
    style_rule: Any,
    scope: str,
    source: str,
    sha256: str,
    out: dict[tuple[Any, ...], dict[str, Any]],
) -> None:
    formats = _style_rule_formats(style_rule)
    if not formats:
        return
    element = style_rule.get("element", "")
    formats_sig = tuple(tuple(sorted(f.items())) for f in formats)
    key = ("chrome_rule", scope, element, formats_sig)
    if key in out:
        return
    out[key] = {
        "scope": scope,
        "element": element,
        "formats": formats,
        "source": source,
        "sha256": sha256,
        "xpath": tree.getpath(style_rule),
    }


def _mine_chrome_rules(
    tree: Any, root: Any, source: str, sha256: str, out: dict[tuple[Any, ...], dict[str, Any]]
) -> None:
    """Mine workbook-level and worksheet-level ``<style-rule>`` chrome constructs.

    Workbook scope is exactly ``<workbook><style>``'s direct ``<style-rule>``
    children. Worksheet scope is every ``<style-rule>`` anywhere under a
    ``<worksheet>`` element — not just the direct ``<table><style>`` child.
    Real workbooks nest a good deal of worksheet chrome one level deeper, at
    ``<table><panes><pane><style>`` (this is where ``mark-labels-show``,
    ``mark-labels-cull``, and mark size/transparency style-rules actually
    live in every mined exemplar) — restricting to the shallower path would
    silently drop exactly the "bar labels" chrome construct the plan calls
    out as a major readability win, so the wider ``.//style-rule`` sweep is
    used deliberately (see the D0 slice report for the full rationale).
    """
    wb_style = root.find("style")
    if wb_style is not None:
        for sr in wb_style.findall("style-rule"):
            _add_chrome_rule(tree, sr, "workbook", source, sha256, out)
    for ws in root.findall(".//worksheet"):
        for sr in ws.findall(".//style-rule"):
            _add_chrome_rule(tree, sr, "worksheet", source, sha256, out)


# ---------------------------------------------------------------------------
# palettes.yaml
# ---------------------------------------------------------------------------


def _mine_palettes(
    tree: Any, root: Any, source: str, sha256: str, out: dict[tuple[Any, ...], dict[str, Any]]
) -> None:
    for cp in root.findall(".//color-palette[@custom='true']"):
        colors = [c.text.strip() for c in cp.findall("color") if c.text and c.text.strip()]
        if not colors:
            continue
        name = (cp.get("name") or "").strip()
        palette_type = cp.get("type", "")
        key = ("palette", name, palette_type, tuple(colors))
        if key in out:
            continue
        out[key] = {
            "name": name,
            "type": palette_type,
            "colors": colors,
            "source": source,
            "sha256": sha256,
            "xpath": tree.getpath(cp),
        }


# ---------------------------------------------------------------------------
# actions.yaml
# ---------------------------------------------------------------------------


def _activation_block(el: Any) -> dict[str, str]:
    act = el.find("activation")
    if act is None:
        return {"type": ""}
    result = {"type": act.get("type", "")}
    auto_clear = act.get("auto-clear")
    if auto_clear is not None:
        result["auto_clear"] = auto_clear
    return result


def _source_block(el: Any) -> dict[str, str]:
    src = el.find("source")
    if src is None:
        return {"type": "", "worksheet": ""}
    result = {"type": src.get("type", ""), "worksheet": src.get("worksheet", "")}
    dashboard = src.get("dashboard")
    if dashboard is not None:
        result["dashboard"] = dashboard
    return result


def _link_block(el: Any) -> dict[str, str] | None:
    link = el.find("link")
    if link is None:
        return None
    return {str(k): str(v) for k, v in link.attrib.items()}


def _command_block(el: Any) -> dict[str, Any] | None:
    cmd = el.find("command")
    if cmd is None:
        return None
    params = {p.get("name"): p.get("value", "") for p in cmd.findall("param") if p.get("name")}
    return {"command": cmd.get("command", ""), "params": params}


def _build_action_entry(tree: Any, action: Any, source: str, sha256: str) -> dict[str, Any]:
    command = _command_block(action)
    kind = ACTION_KIND_BY_COMMAND.get(command["command"], "url") if command else "url"
    entry: dict[str, Any] = {
        "kind": kind,
        "caption": action.get("caption", ""),
        "activation": _activation_block(action),
        "source": _source_block(action),
        "source_file": source,
        "sha256": sha256,
        "xpath": tree.getpath(action),
    }
    if command is not None:
        entry["command"] = command
    link = _link_block(action)
    if link is not None:
        entry["link"] = link
    return entry


def _build_edit_parameter_entry(tree: Any, action: Any, source: str, sha256: str) -> dict[str, Any]:
    agg = action.find("agg-type")
    clear = action.find("clear-option")
    params_el = action.find("params")
    params: dict[str, str] = {}
    if params_el is not None:
        for p in params_el.findall("param"):
            name = p.get("name")
            if name is not None:
                params[name] = p.get("value", "")

    edit_parameter: dict[str, Any] = {"params": params}
    if agg is not None and agg.get("type") is not None:
        edit_parameter["agg_type"] = agg.get("type")
    if clear is not None:
        clear_dict = {"type": clear.get("type", "")}
        clear_value = clear.get("value")
        if clear_value is not None:
            clear_dict["value"] = clear_value
        edit_parameter["clear_option"] = clear_dict

    return {
        "kind": "edit-parameter",
        "caption": action.get("caption", ""),
        "activation": _activation_block(action),
        "source": _source_block(action),
        "edit_parameter": edit_parameter,
        "source_file": source,
        "sha256": sha256,
        "xpath": tree.getpath(action),
    }


def _mine_actions(
    tree: Any, root: Any, source: str, sha256: str, out: dict[tuple[Any, ...], dict[str, Any]]
) -> None:
    actions_el = root.find("actions")
    if actions_el is None:
        return
    for action in actions_el.findall("action"):
        entry = _build_action_entry(tree, action, source, sha256)
        key = ("action", sha256, entry["xpath"])
        out.setdefault(key, entry)
    for action in actions_el.findall("edit-parameter-action"):
        entry = _build_edit_parameter_entry(tree, action, source, sha256)
        key = ("action", sha256, entry["xpath"])
        out.setdefault(key, entry)


# ---------------------------------------------------------------------------
# text_zones.yaml
# ---------------------------------------------------------------------------


def _run_dict(run: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for attr in ("bold", "italic", "fontcolor", "fontname", "fontsize"):
        value = run.get(attr)
        if value is not None:
            out[attr] = value
    out["text"] = (run.text or "").strip()
    return out


def _max_fontsize(runs: list[Any]) -> float:
    sizes: list[float] = []
    for r in runs:
        fs = r.get("fontsize")
        if not fs:
            continue
        try:
            sizes.append(float(fs))
        except ValueError:
            continue
    return max(sizes) if sizes else 0.0


def _has_bold(runs: list[Any]) -> bool:
    return any(r.get("bold") == "true" for r in runs)


def _zone_role_hint(is_first: bool, runs: list[Any]) -> str:
    """Heuristic: first zone or bold >=14pt -> title; >=11pt or bold -> subtitle; else body."""
    max_fs = _max_fontsize(runs)
    if is_first or (max_fs >= TITLE_FONT_SIZE_MIN and _has_bold(runs)):
        return "title"
    if max_fs >= SUBTITLE_FONT_SIZE_MIN or _has_bold(runs):
        return "subtitle"
    return "body"


def _mine_text_zones(
    tree: Any, root: Any, source: str, sha256: str, out: dict[tuple[Any, ...], dict[str, Any]]
) -> None:
    for dashboard in root.findall(".//dashboard"):
        zones_el = dashboard.find("zones")
        if zones_el is None:
            continue
        first_seen = False
        for zone in zones_el.iter("zone"):
            if zone.get("type-v2") != "text":
                continue
            formatted_text = zone.find("formatted-text")
            if formatted_text is None:
                continue
            run_els = formatted_text.findall("run")
            if not run_els:
                continue
            is_first = not first_seen
            first_seen = True
            xpath = tree.getpath(zone)
            key = ("text_zone", sha256, xpath)
            if key in out:
                continue
            out[key] = {
                "runs": [_run_dict(r) for r in run_els],
                "zone_role_hint": _zone_role_hint(is_first, run_els),
                "source": source,
                "sha256": sha256,
                "xpath": xpath,
            }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _sorted_entries(mapping: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort entries by (source basename, xpath) for deterministic output.

    ``actions`` entries key their own mined ``<source>`` XML sub-object under
    the ``source`` field, so their provenance basename lives under
    ``source_file`` instead (see module docstring / SCHEMA.md); every other
    recipe type uses ``source`` directly.
    """

    def _provenance_key(entry: dict[str, Any]) -> tuple[str, str]:
        basename = entry["source_file"] if "source_file" in entry else entry["source"]
        return (str(basename), str(entry["xpath"]))

    return sorted(mapping.values(), key=_provenance_key)


def mine_workbooks(paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    """Mine one or more ``.twb``/``.twbx`` paths into the 5 recipe collections.

    Deterministic: ``paths`` are processed sorted by basename regardless of
    the order given, so re-running with the same input set (in any order)
    yields byte-identical output.
    """
    ordered = sorted(paths, key=lambda p: p.name)

    zone_styles: dict[tuple[Any, ...], dict[str, Any]] = {}
    chrome_rules: dict[tuple[Any, ...], dict[str, Any]] = {}
    palettes: dict[tuple[Any, ...], dict[str, Any]] = {}
    actions: dict[tuple[Any, ...], dict[str, Any]] = {}
    text_zones: dict[tuple[Any, ...], dict[str, Any]] = {}

    for path in ordered:
        raw = read_twb_bytes(path)
        sha256 = hashlib.sha256(raw).hexdigest()
        root = etree.fromstring(raw, parser=safe_parser())
        tree = root.getroottree()
        source = path.name

        _mine_zone_styles(tree, root, source, sha256, zone_styles)
        _mine_chrome_rules(tree, root, source, sha256, chrome_rules)
        _mine_palettes(tree, root, source, sha256, palettes)
        _mine_actions(tree, root, source, sha256, actions)
        _mine_text_zones(tree, root, source, sha256, text_zones)

    return {
        "zone_styles": _sorted_entries(zone_styles),
        "chrome_rules": _sorted_entries(chrome_rules),
        "actions": _sorted_entries(actions),
        "palettes": _sorted_entries(palettes),
        "text_zones": _sorted_entries(text_zones),
    }


def dump_recipe_yaml(entries: list[dict[str, Any]]) -> str:
    """Deterministically serialize one recipe's entries: sorted keys, stable dump."""
    result: str = yaml.safe_dump(
        entries,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    return result


def write_recipes(recipes: dict[str, list[dict[str, Any]]], out_dir: Path) -> None:
    recipes_dir = out_dir / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    for name in RECIPE_NAMES:
        (recipes_dir / f"{name}.yaml").write_text(dump_recipe_yaml(recipes[name]), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="design_miner.py",
        description=(
            "Mine zone-style / chrome / action / palette / text-zone constructs "
            "from .twb or .twbx workbooks into a deterministic YAML corpus."
        ),
    )
    parser.add_argument("inputs", nargs="+", help="One or more .twb or .twbx paths to mine.")
    parser.add_argument(
        "--out", required=True, help="Output corpus directory (recipes/ is created under it)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    paths = [Path(p) for p in args.inputs]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"error: input file(s) not found: {missing}", file=sys.stderr)
        return 1

    recipes = mine_workbooks(paths)
    write_recipes(recipes, Path(args.out))
    for name in RECIPE_NAMES:
        print(f"{name}: {len(recipes[name])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
