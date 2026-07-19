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

## Discovered render constraints (Design Excellence, Slice D4 FINAL SHAPE)

A recipe/theme literal being schema-valid and mined-verbatim does not guarantee Tableau Cloud
actually *renders* it — the KPI-tile (BAN) `<customized-label>` mechanism took three live-probe
hotfix rounds plus a dedicated offline bisect ladder (a dozen `.twbx` variants, V0–V12, isolating
one variable at a time against WB-118's real, published "Sales KPI (BAN) New" worksheet,
`WB-118.twbx`) to get right. These constraints are **render-time**
facts about the Tableau Cloud engine, not corpus/provenance facts about the mined XML — they don't
fit the `recipes/`/`themes/` layers above, but belong here as the schema layer's own record of
"XSD-valid ≠ Cloud-renders", so a future slice doesn't have to re-discover them:

1. **A `<customized-label>` requires a pane-level `mark-labels-show`/`mark-labels-cull` style-rule
   to render at all.** Its absence doesn't just leave the label unstyled — it silently drops the
   whole `<customized-label>` back to the mark's plain default text-mark render. Worse: adding
   `mark-labels-show` to an OTHERWISE-non-matching label shape renders a completely BLANK mark
   (no fallback text either) — partial adoption of the proven shape is worse than none. The rule
   must coexist with the pane's OWN `element='cell'` `text-align` rule, both mined from the same
   worksheet's `<table><panes><pane><style>`.
2. **The label's run idiom must be reproduced structurally, not just semantically equivalent.**
   A caption run (letter-spaced uppercase field/tile name), literal glyph-prefixed `"Æ\n"`
   newline-separator runs (not a plain `"\n"` — semantically identical but NOT part of the working
   shape), and CDATA (not XML-escaped-text) placeholder runs (`<![CDATA[<[ds].[instance]>]]>`) are
   all part of ONE mechanism — omitting any one of them (independently verified via the bisect
   ladder) leaves the label non-rendering, even though every individual substitution produces an
   equally XSD-valid, semantically-identical XML info-set.
3. **A `default-format` on a RAW (non-calculated) field's worksheet-local
   `<datasource-dependencies><column>` is silently ignored by Tableau Cloud for a naked (rows/cols
   empty) BAN view**, even when the SAME field is referenced by a `<customized-label>` placeholder
   that IS rendering correctly. A CALCULATED field's own worksheet-local `default-format`
   (`<column><calculation class='tableau' formula='SUM([field])'/></column>`, `derivation='User'`)
   is NOT ignored — this is the only mechanism proven to make a compact/arrow number format visible
   inside a BAN tile. A plain `<text>` shelf encoding of an UNFORMATTED calculated field (with no
   working label at all) still renders that field's own compact value as the mark's plain default
   text — this is a DIFFERENT, unlabeled fallback rendering path, not evidence the label mechanism
   itself is working.
4. **A `<customized-label>` VALUE run that overflows the mark's cell renders as a literal `"###"`**
   (Tableau's standard numeric-cell-overflow placeholder), even though the label mechanism itself is
   rendering correctly — this is easy to misdiagnose as a repeat of constraint #1/#2's non-rendering
   failure, but it is a DIFFERENT problem. `fontsize` clamping (`sidecar/twb_builder.py`'s
   `_KPI_LABEL_VALUE_FONTSIZE_MAX = 26`, the largest BAN fontsize anywhere in the 10-workbook mined
   corpus) is still correct discipline for a *standalone* worksheet view — but constraint #5 below,
   found by a follow-up live-probe round, proved fontsize is NOT what causes `"###"` inside a real
   multi-zone DASHBOARD; do not assume shrinking fontsize alone fixes an overflow observed in a
   dashboard render.
5. **A KPI-tile Text mark's numeric value overflows to `"###"` inside ANY dashboard with 2+ zones,
   REGARDLESS of fontsize, formatting, or the customized-label mechanism — and this is NOT specific
   to the customized-label BAN mechanism at all.** A live-probe round (bisect ladder V13–V23,
   published to a real Tableau Cloud dev site, fresh-rendered at each step) methodically ruled out,
   one variable at a time, every plausible XML-level cause: `mark-labels-cull` (true vs false — no
   effect), the delta value line (present vs absent — no effect), the primary run's `fontsize`
   (17px vs 10px — no effect, and the rendered `"###"` glyph itself did not change width with
   fontsize, implying it is NOT rendering our run's actual font at all), the KPI band's own height
   allocation (20% vs 40% of a fixed-size dashboard canvas — no effect), tile width-sharing among
   sibling KPI tiles (4-way vs full-width alone — no effect), fixed-size zone sizing
   (`is-fixed='true' fixed-size='150'`, mirrored verbatim from a real mined exemplar zone —
   no effect), and dashboard
   zone-nesting depth (`kpi_band_over_charts`'s two nested flow levels vs the simplest possible
   single-level 2-sheet `tiled_vertical` dashboard — no effect). The ONE variable that flips the
   result: whether the worksheet is the dashboard's **sole** zone (works: caption + correctly
   compact-formatted big value, every time, V0–V13) vs **any** dashboard with a second zone present
   anywhere (`"###"`, every time, V14–V21 — even a *lone, full-width* KPI tile paired with just ONE
   sibling chart zone, no width-sharing at all). Critically, this ALSO affects a KPI tile with **no**
   customized-label and **no** calc column at all — i.e. this codebase's current, unmodified,
   untouched production output for an *untheme* `kpi_tile` sheet (raw `SUM(field)`, Tableau's own
   default general number format, zero compaction) overflows identically once placed in the SAME
   multi-zone dashboard (V22/V23). **Conclusion: this is a pre-existing Tableau Cloud
   dashboard-rendering characteristic of KPI-tile-shaped Text marks in multi-zone dashboards, not a
   defect introduced by the customized-label/calc-column BAN mechanism** — the mechanism does not
   make the real-world (multi-zone) case any worse than the pre-D4 baseline; it only *adds* value in
   contexts where the worksheet renders standalone (e.g. the "Sales" tab viewed directly, outside the
   dashboard). Fixing the multi-zone case is therefore NOT achievable through `sidecar/twb_builder.py`
   XML changes alone (every lever this corpus and the mined exemplars offer was tried and had zero
   effect) — it needs either Tableau-Cloud-side investigation (a support case, or discovery of an
   as-yet-untested rendering-mode difference, e.g. "Automatic" vs fixed dashboard sizing, viewport/
   device-designer differences, or a genuine product limitation) or a different UX approach for KPI
   tiles inside dense dashboards (e.g. accepting a smaller pre-truncated value format server-side,
   or steering users toward viewing KPI tiles as their own full worksheet tab).

   **Follow-up round, `sizing-mode='fixed'` hypothesis — tested, REFUTED.** Our emitted
   `<dashboard><size>` carried equal `min`/`max` but no `sizing-mode` attribute, unlike every mined
   exemplar dashboard (WB-118/WB-117 both carry `<size ... sizing-mode='fixed'/>`) — a
   plausible root cause, since Tableau's server-side image renderer is documented to treat an
   equal-min/max `<size>` WITHOUT `sizing-mode='fixed'` as range/automatic sizing, not truly fixed.
   `sizing-mode='fixed'` was added unconditionally to both `<size>` emission sites
   (`_build_dashboard` and `_build_story`; XSD-confirmed valid, `DashboardSizingMode-ST` enum includes
   `"fixed"`) and verified end-to-end against a REAL full-pipeline probe (exec audience, executive_dark
   theme, brand typography, the actual `generatePlan()`-produced 4-tile KPI band + 2-chart dashboard,
   published + fresh-rendered, `maxAge=1`, on the real Tableau Cloud dev site) — **the KPI band still
   rendered all four tiles as `"###"`, byte-for-byte the same failure mode as before the fix.**
   `sizing-mode='fixed'` is kept in the codebase regardless (mined-correct baseline hygiene, harmless
   per XSD validation and this same full-pipeline render — every OTHER dashboard element, including
   the two chart worksheets and the filled map, rendered correctly), but it is **not** the fix for
   this constraint. D4 is closed with this constraint recorded as a known, documented Tableau Cloud
   platform limitation rather than a remaining `twb_builder.py` defect.

See `sidecar/twb_builder.py`'s `_kpi_tile_customized_label`/`_append_kpi_ban_calc_column`/
`_kpi_tile_pane_style_rules`/`_build_dashboard` docstrings for the encoding of these constraints into
the builder, and `sidecar/tests/test_twb_kpi_styling_customized_label.py`'s module docstring for the
full bisect-ladder narrative (V0 control graft → V1–V5 individual-attribute isolation → V7 raw-vs-
calculated field format → V9/V10 mark-labels-show necessary-but-not-sufficient → V11/V12 proof →
V13–V23 multi-zone-dashboard isolation → `sizing-mode='fixed'` follow-up, refuted).
