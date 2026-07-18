# `design/corpus/` — schema and provenance rules

This is the design-excellence knowledge corpus (ADR-0013): a **committed, human-reviewable,
deterministic** alternative to an embeddings/vector-store "RAG". It has two layers:

```
design/corpus/
  recipes/    mined raw constructs — produced by sidecar/design_miner.py, never hand-edited
  themes/     hand-curated theme presets — every literal traceable back to a recipes/ entry
```

## Provenance rule (non-negotiable)

Every recipe entry and every theme literal must be traceable to a real, mined Tableau workbook
construct. **Nothing in this corpus is invented.** Concretely:

1. Every `recipes/*.yaml` entry carries `source` (input file basename), `sha256` (of the *inner*
   `.twb` XML bytes — for a `.twbx` input, the extracted member's bytes, not the zip), and `xpath`
   (`ElementTree.getpath()` from the parsed document). `actions.yaml` entries carry the basename
   under `source_file` instead of `source`, because `source` is already used for the mined
   `<source dashboard/type/worksheet>` XML sub-object.
2. Every hex color / pixel literal that appears in a `themes/*.yaml` file must appear somewhere in
   `recipes/*.yaml` — enforced by `tests/designCorpus.test.ts`'s "theme-cites-recipe" check (hex
   colors specifically; the check is regex-based over `#[0-9a-fA-F]{3,8}`).
3. Every `themes/*.yaml`'s `provenance` list documents, per literal/construct, which of the
   10-workbook allowlist it came from, that source's sha256, and a short description of the exact
   construct (often including the recipe's `xpath`, for direct auditability).
4. The **10-workbook allowlist** is fixed for this D0 slice: the 3 downloaded `.twbx` exemplars
   (`WB-117.twbx`, `WB-118.twbx`,
   `WB-114.twbx` — see `design/references/README.md`) plus the 7 pre-existing
   reference `.twb` files (`WB-015`, `WB-093`, `WB-095`, `WB-133`,
   `WB-058`, `WB-062`, `WB-063`). `tests/designCorpus.test.ts` asserts every
   `source`/`source_file` value in the corpus is drawn from this allowlist.

## `recipes/*.yaml` — mined raw constructs

Produced by `python sidecar/design_miner.py <inputs>... --out design/corpus` (see
`design/references/README.md` for the exact reproduction command). **Never hand-edited** — if a
value needs to change, re-run the miner (possibly against a wider input set) and re-curate the
themes that cite it.

### `zone_styles.yaml`

Every **distinct** (deduplicated by `(zone_type, formats)` content) `<zone-style>` block found
anywhere in the document:

```yaml
- zone_type: empty          # parent <zone type-v2="...">, or "worksheet" if the parent isn't a <zone>
  formats: { background-color: "#2f2e41", margin: "0", ... }   # verbatim <format attr/value> pairs
  source: WB-117.twbx
  sha256: 5665d0ae...
  xpath: /workbook/dashboards/dashboard[1]/zones/zone[1]/zone[1]/zone-style
```

**Dedup policy:** the plan text explicitly calls this recipe's blocks "distinct" — real workbooks
repeat trivial box-model boilerplate (the same `border-color:#000000;border-style:none;...`
zone-style appears dozens of times per file), so entries are deduplicated by content
(`zone_type` + the full `formats` dict), keeping the first occurrence (in sorted-input, document
order) as the canonical provenance. 234 distinct entries were mined from the 10-workbook input set.

### `chrome_rules.yaml`

Every **distinct** `<style-rule>` under the workbook-level `<workbook><style>` (`scope: workbook`)
and **anywhere under a `<worksheet>`** (`scope: worksheet` — see "Deviation" below):

```yaml
- scope: worksheet
  element: mark
  formats:
    - { attr: mark-labels-show, value: "true" }
  source: WB-015
  sha256: 3f0c8616...
  xpath: /workbook/worksheets/worksheet[13]/table/panes/pane[1]/style/style-rule[1]
```

Each `formats` list item carries `attr`/`value` and an optional `scope` (the format element's own
`scope="cols"|"rows"` attribute, e.g. on axis-range encodings — unrelated to the entry-level
`scope` field). `field`/`class`/`data-class` attributes are intentionally dropped (see Deviation 2
below). 597 distinct entries were mined.

**Deviation 1 (widened worksheet path):** the plan described the worksheet path as
`<worksheet><table><style>`. In every mined exemplar, a large share of real worksheet chrome —
critically, `mark-labels-show`/`mark-labels-cull` and mark size/transparency/stroke — actually
lives one level deeper, at `<table><panes><pane><style>`. Restricting to the literal
`table/style` path would have silently produced a `chrome_rules.yaml` with **zero**
`mark-labels-show` entries, missing exactly the construct the plan calls out as "a major
readability win." The miner therefore sweeps `.//style-rule` anywhere under each `<worksheet>`,
not just the direct `table/style` child. Workbook scope is unchanged (exactly `<workbook><style>`'s
direct children).

**Deviation 2 (content-dedup + dropped `field`/`class`/`data-class`):** the plan's wording did not
mark `chrome_rules` as "distinct" the way `zone_styles` was. A fully literal "every occurrence"
extraction produces ~1171 raw worksheet-scoped style-rules across the 10-workbook set, the large
majority of which are per-calculated-field formatting noise (an axis-range override tied to one
specific `Calculation_785878179731587115` GUID, unrepeatable in any future generated workbook) that
adds size without adding signal to a corpus meant to be human-reviewable. `chrome_rules.yaml`
therefore drops the `field`/`class`/`data-class` attributes (the theme layer only ever needs
"font-size 9 was used as a datalabel size somewhere real", not which calculated field it was
attached to) and deduplicates by the remaining content (`scope` + `element` + the reduced `formats`
list) the same way `zone_styles.yaml` does. This is a deliberate interpretation, not a literal
reading of the plan text — flagged explicitly per the D0 slice report.

### `actions.yaml`

Every `<action>` and `<edit-parameter-action>` under `<workbook><actions>`, **verbatim**, not
content-deduplicated (every real action is individually meaningful — two actions with identical
`command`/`params` shapes but different captions/sources must both survive):

```yaml
- kind: filter                     # filter | brush | edit-parameter | url (classified, not invented)
  caption: "Clear Selection - Fixture"
  activation: { type: on-select, auto_clear: "true" }
  source: { type: sheet, worksheet: "Fixture Sheet", dashboard: "Fixture Dashboard" }
  command: { command: "tsc:tsl-filter", params: { target: "Fixture Sheet" } }
  link: { caption: ..., delimiter: ",", escape: "\\", expression: ..., include-null: "true", multi-select: "true", url-escape: "true" }
  source_file: WB-015
  sha256: 3f0c8616...
  xpath: /workbook/actions/action[1]
```

`edit-parameter-action` entries use `kind: edit-parameter` and an `edit_parameter: {agg_type?,
clear_option?, params: {source-field, target-parameter}}` block instead of `command`/`link` (an
explicit, documented extension beyond the plan's literal field list, needed to losslessly carry
this action type's real substructure — the plan commits to `kind: edit-parameter` existing but
does not spell out its payload shape). **Known shapes verified present** (the D0 gate): 57 total
action entries, including real `command="tsc:tsl-filter"` (with `exclude`/`special-fields`/`target`
params) and `command="tsc:brush"` (with `field-captions`/`target` params) — see
`design/corpus/recipes/actions.yaml`. No `kind: url` action was found in any of the 10 inputs
(never invented, so none is emitted).

Deduplication here is identity-only: `(sha256, xpath)` — this only ever collapses `WB-063`'s
actions against `WB-062`'s (the two files are byte-identical).

### `palettes.yaml`

Every `<color-palette custom="true">`, deduplicated by `(name, type, colors)` content (the same
palette is often copy-pasted across several dashboards/worksheets within one workbook):

```yaml
- name: ""
  type: ordered-sequential
  colors: ["#f1f1f1", "#d9d8df", ..., "#2f2e41"]
  source: WB-117.twbx
  sha256: 5665d0ae...
  xpath: /workbook/datasources/datasource[1]/color-palette
```

12 distinct entries were mined (includes palettes nested under `<datasource>` and under
`<worksheet>/table/style/style-rule/encoding/color-palette`).

### `text_zones.yaml`

Every dashboard text zone's `<formatted-text>` (i.e. `type-v2="text"` zones under
`<dashboard><zones>`), **verbatim**, not content-deduplicated (two titles with identical
formatting but different text must both survive):

```yaml
- runs:
    - { bold: "true", fontcolor: "#ffffff", fontname: "Tableau Semibold", fontsize: "20", text: "Superstore |" }
    - { fontcolor: "#ffffff", fontname: "Tableau Light", fontsize: "14", text: "ORDER DETAILS" }
  zone_role_hint: title
  source: WB-117.twbx
  sha256: 5665d0ae...
  xpath: /workbook/dashboards/dashboard[2]/zones/zone[4]/zone[1]/zone[1]
```

`zone_role_hint` heuristic (documented, not mined — it is a classification the miner applies to
real mined text, never invented text): a zone is `title` if it is the **first** dashboard text zone
encountered (per-dashboard, document order) **or** any of its runs is bold with `fontsize >= 14`;
otherwise `subtitle` if any run has `fontsize >= 11` or is bold; otherwise `body`. This heuristic is
known-imperfect (e.g. a small attribution/footer run that happens to be the first text zone in
document order is tagged `title`) — it is a best-effort classification hint for future slices, not
a guarantee. 82 entries were mined.

## `themes/*.yaml` — hand-curated presets

Every theme file has this top-level shape (all keys optional except `name`, `tags`, `provenance`):

```yaml
name: string
tags: { audiences: [exec|analyst|operational|mixed, ...], personas: [string, ...], artifacts: [dashboard|story, ...], mood: dark|light }
provenance: [{ source: string, sha256: string, construct: string }]
dashboardBackground: "#rrggbb"
spacing: { outerMargin: "px", gutter: "px" }
chartCard: { background?, borderColor?, borderStyle?, borderWidth?, padding?, margin?, cornerRadius? }
kpiTile: { background?, banColor?, padding?, useSemanticDeltaColors: bool }
header: { background?, titleColor?, subtitleColor? }
chrome: { hideGridlines, hideZeroline, hideAxisTicks, showMarkLabels, datalabel: { fontSize, fontWeight, colorMode } }
```

`cornerRadius` is a legal plain `<format attr="corner-radius">` per the TWB XSD (verified in
PLAN.md), but **no mined zone-style in the 10-workbook corpus uses it** — only the XSD-illegal
`_.fcp.DashboardRoundedCorners...` *element* form appears in the source exemplars, and that form
must never be mirrored (it fails schema validation). Every D0 theme therefore omits `cornerRadius`
entirely rather than inventing a pixel value; each theme file documents this inline.

### The 4 themes (exactly 4 for D0 — enforced by `tests/designCorpus.test.ts`)

| Theme | Direction | Primary source(s) | Default fallback? |
|---|---|---|---|
| `executive_dark` | navy KPI band/header, white BANs | WB-117 (`WB-117.twbx`) | no |
| `executive_light` | white cards, hairline borders | WB-118 (`WB-118.twbx`) + WB-114 (`WB-114.twbx`) | no |
| `analyst_clean` | light, minimal chrome | WB-015 + WB-062 | **yes** |
| `operational_plain` | high-density plain | WB-133 + WB-095 | no |

### Retrieval (future slice — D5)

`selectTheme(audience, personaName?, artifact)` is a pure function over each theme's `tags`:
persona match > audience match > default `analyst_clean`, alphabetical theme-name tie-break. Every
`audience` value (`exec`, `analyst`, `operational`, `mixed`) must resolve to at least one theme by
tag — enforced by `tests/designCorpus.test.ts`. This resolution function itself is **out of scope
for D0** (no `src/design/selectTheme.ts` is added in this slice); D0 only guarantees the corpus has
enough tag coverage for it to be implementable deterministically.
