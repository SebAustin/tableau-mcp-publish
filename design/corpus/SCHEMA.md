# `design/corpus/` — schema and provenance rules

This is the design-excellence knowledge corpus (ADR-0013): a **committed, human-reviewable,
deterministic** alternative to an embeddings/vector-store "RAG". It has two layers:

```
design/corpus/
  recipes/    mined raw constructs — produced by sidecar/design_miner.py, never hand-edited
  themes/     hand-curated theme presets — every literal traceable back to a recipes/ entry
  stats/      corpus-wide statistical norms — produced by sidecar/design_stats.py (Slice T1)
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
4. The **10-workbook allowlist was fixed for the D0 slice**: the 3 downloaded `.twbx` exemplars
   (`WB-117`, `WB-118`, `WB-114` — see `design/references/README.md`) plus the 7 pre-existing
   reference `.twb` files (`WB-015`, `WB-093`, `WB-095`, `WB-133`, `WB-058`, `WB-062`, `WB-063`).
   Source identities are redacted to these opaque, stable `WB-NNN` identifiers by policy (see
   "Source-identity redaction" below). **Slice T1 legitimately extends this
   allowlist** with 14 top-100-corpus sources (see "Notable-construct append" under `stats/`
   below) — `tests/designCorpus.test.ts`'s `WORKBOOK_ALLOWLIST` is the union of the D0 set and
   this T1 addition, and asserts every `source`/`source_file` value in the corpus is drawn from
   it. The 100-workbook top-100 corpus itself (`design/references/top100/`) is **not** added to
   this allowlist wholesale — only the specific sources the notable-construct append actually
   cited, keeping the provenance-checked allowlist a precise, auditable record rather than a
   rubber stamp.

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
  source: WB-117
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
  source: WB-117
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
  source: WB-117
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

## `stats/*.yaml` — mined-corpus statistical norms (Slice T1)

Produced by `python sidecar/design_stats.py --refs-dir ... --top100-dir ... [--extra-refs ...]
--out design/corpus` (see "Regenerating" below for the exact command). Unlike `recipes/*.yaml`
(individual mined constructs), `stats/*.yaml` holds **aggregated statistics** — distributions and
usage rates — over the full corpus: the 3 root exemplars, the `design/references/top100/`
top-100 corpus (every `manifest.yaml` item with `status: downloaded`), and any extra reference
workbooks passed via `--extra-refs`. **Never hand-edited**, exactly like `recipes/*.yaml`.

### Confidence guard (plan mandate — non-negotiable)

Every aggregated bucket carries `n` and `confidence`: `"ok"` when `n >= 15`, else `"low"`. A
`confidence: low` bucket is a signal for a human or a future slice's explicit judgment call —
**it is never auto-applied to themes or the builder.** A high-frequency real-world construct
whose bucket is `confidence: low` (e.g. KPI-band-like rows, or anything story-related) belongs
in a future slice's manual-judgment note, not a silent default.

### `dashboard_norms.yaml`

Top-level: `corpus_size` (workbook file count), `sources` (root/top100/extra-refs breakdown, for
reproducibility — see "No wall-clock timestamp" below), `n_dashboards`, `strata`, `kpi_band`.

`strata` has exactly 3 keys — `"<900"`, `"900-1400"`, `">1400"` (canvas width in px, the
plan-mandated buckets) — each holding `{n, confidence, title_fontsize, title_height_ratio,
canvas_width_px, canvas_height_px, margin_px, padding_px, sizing_mode_distribution,
filter_zone_usage_rate, paramctrl_zone_usage_rate, device_layout_usage_rate,
mark_label_usage_rate}`. Every distribution sub-object (`title_fontsize`, `title_height_ratio`,
`canvas_width_px`, `canvas_height_px`, `margin_px`, `padding_px`) is `{n, confidence, median,
p25, p75, citations}` — `citations` is up to 5 provenance entries (`{source, sha256, xpath}`),
one per distinct source workbook, deterministically sorted. Every usage-rate sub-object
(`filter_zone_usage_rate`, `paramctrl_zone_usage_rate`, `device_layout_usage_rate`,
`mark_label_usage_rate`) is `{n, confidence, count, total, rate}`.

`title_fontsize`/`title_height_ratio` are computed only over dashboards that have a text zone in
the **top 15%** of the canvas (`y < 15000` on the 0–100000 zone grid — see
`sidecar/design_miner.py`'s `TITLE_ZONE_TOP_BAND_Y_MAX`); a dashboard with no such zone
contributes to the stratum's `n` (canvas size, sizing-mode, usage rates) but not to these two
title buckets specifically — its own `title_fontsize`/`title_height_ratio` sub-`n` is smaller
than the stratum `n`, by design.

`margin_px`/`padding_px` are the raw (non-deduplicated) pixel values from every `<zone-style>`
nested anywhere under each stratum's dashboards — deliberately **not** reusing
`recipes/zone_styles.yaml`'s content-deduplicated entries, since a per-occurrence distribution
(not a set of distinct values) is what a "median margin" needs.

`kpi_band` (`{n, confidence, height_ratio, child_count, citations}`) is **corpus-wide, not
stratified by canvas width** — a deliberate deviation from the literal per-stratum reading: the
real corpus yields very few KPI-band-like rows in total (a horz `layout-flow` with
`layout-strategy-id='distribute-evenly'` and >= 3 `is-fixed='true'` children — see
`design/corpus/SCHEMA.md`'s render-constraint #5), too few to meaningfully sub-divide by stratum
on top of the existing `confidence: low` guard this small a sample already triggers.

### `story_norms.yaml`

Top-level: `corpus_size`, `sources`, `n_workbooks`, `usage_rate`, `points_per_story`,
`caption_length`, `nav_type_distribution`, `citations`.

`usage_rate` (`{n, confidence, count, total, rate}`) deliberately gates `n`/`confidence` on the
**numerator** (`count` — workbooks that actually have a story) rather than the (much larger)
`total` corpus denominator: a rate estimated from zero or a handful of positive examples is
exactly as unreliable to extrapolate from as any other n<15 bucket, regardless of how large the
denominator is. `points_per_story`/`caption_length` are flattened across every story point in
every storyboard found (so a workbook with 2 stories of 3 points each contributes 6 caption
lengths, not 2). This is a deliberate interpretation, documented here per this corpus's existing
"flag every deviation" discipline (see the D0 dedup-policy deviations above for precedent).

**The real corpus found ZERO storyboards** across all 110 mined workbooks (`n_workbooks: 110`,
`usage_rate: {count: 0, rate: 0.0, confidence: low}`, every distribution `n: 0`) — this is the
plan's own predicted "expected low-n case" for story-related buckets, now confirmed with a real
number rather than an assumption. T2/T3 must treat every story norm as `confidence: low` until a
future corpus refresh finds real storyboard examples (VOTD galleries evidently skew toward
single-view dashboards/vizzes, not multi-point stories).

### Notable-construct append (Slice T1 — `design/corpus/recipes/*.yaml` mutation)

`design_stats.py`'s `append_notable_constructs` step mines the FULL input corpus for
`zone_styles`/`chrome_rules`/`palettes` — the three **content-deduplicated** recipe types (see
the dedup-policy note above) — and appends any construct that is (a) not already present in the
committed `recipes/*.yaml` and (b) independently observed in at least 8 distinct source files (a
deliberate frequency floor: roughly 8.5% of the 110-file T1 corpus, comfortably above one-off
noise while low enough to surface genuinely-common real-world constructs). Capped at 200 new
entries per file (not hit in practice — see below), deterministically sorted, and re-appending
against an already-updated corpus is a no-op (verified: running `design_stats.py` twice produces
byte-identical `recipes/*.yaml`, not a growing file).

`actions.yaml`/`text_zones.yaml` are **intentionally excluded** from this step — both are
already NOT content-deduplicated by design (every action/text-zone is individually meaningful),
so a "how many files repeat this exact content" frequency floor does not apply to them the same
way; appending every top-100 action/text-zone verbatim would be exactly the "repo bloat" the
plan explicitly warns against.

**T1 run result** (110-workbook corpus: 3 root exemplars + 100 top-100 + 7 extra refs):
`zone_styles.yaml` gained 3 entries (234 → 237: a `filter`-zone box-model, a `layout-basic`
box-model, and a `color`-zone box-model — all previously-unseen `type-v2` zone kinds in the D0
corpus), `chrome_rules.yaml` gained 22 entries (597 → 619: number/percent `text-format` cell
rules, axis/gridline chrome-hiding rules, and a `datalabel font-size: 8` rule — real, common
"clean dashboard" chrome the D0 10-workbook corpus happened not to contain). `palettes.yaml`
gained **0** entries — no custom color-palette construct in the full corpus cleared the
frequency bar, so it is untouched (stayed at its original 12 entries; the "skip entirely,
document" branch of the plan mandate). This added exactly the 14 new `WORKBOOK_ALLOWLIST`
sources documented in `tests/designCorpus.test.ts`.

### No wall-clock timestamp (determinism)

Neither stats file embeds a "generated at" timestamp — unlike a typical aggregation header,
this is a deliberate omission so that `aggregate the same corpus twice -> byte-identical output`
holds (verified in `sidecar/tests/test_design_stats.py`), mirroring `design_miner.py`'s own
zero-wall-clock-fields precedent. The `sources` block (which root/top100/extra-refs files
contributed) is the reproducibility record instead.

### Regenerating

```bash
cd sidecar
uv run python design_stats.py \
  --refs-dir ../design/references \
  --top100-dir ../design/references/top100 \
  --extra-refs <path-to>/<WB-058-file> <path-to>/<WB-133-file> <path-to>/<WB-095-file> \
    <path-to>/<WB-062-file> <path-to>/<WB-063-file> <path-to>/<WB-015-file> <path-to>/<WB-093-file> \
  --out ../design/corpus
```

`--extra-refs` is optional — omit it (or pass only the paths that are actually present) to
aggregate over just the root exemplars + top-100 corpus. Pass `--skip-notable-constructs` to
regenerate only `stats/*.yaml` without touching `recipes/*.yaml`.

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
| `executive_dark` | navy KPI band/header, white BANs | `WB-117` | no |
| `executive_light` | white cards, hairline borders | `WB-118` + `WB-114` | no |
| `analyst_clean` | light, minimal chrome | `WB-015` + `WB-062` | **yes** |
| `operational_plain` | high-density plain | `WB-133` + `WB-095` | no |

### Retrieval (implemented in D5 — `src/design/selectTheme.ts`)

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
one variable at a time against `WB-118`'s real, published "Sales KPI (BAN) New" worksheet) to
get right. These constraints are **render-time**
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

6. **Bracketed section colors in a number format (`[Green]…;[Red]…`) are STRIPPED in the
   `<customized-label>` CDATA-placeholder context on Tableau Cloud — and they take the `▲`/`▼`
   arrow down with them.** N4 (sign-based delta color, the external skill suite's Calc-Engine "KPI Status") tried to
   color the KPI delta by sign via Tableau's documented bracketed number-format section colors:
   `[Green]*▲ #,##;[Red]▼ #,##`. Live probe (2026-07-21, workbook 2527341): the format publishes and
   XSD-validates, but Cloud renders the delta with **neither** the color **nor** the arrow — strictly
   worse than the plain `*▲ #,##;▼ #,##` arrow format, which renders the arrow (see M2). No reference
   workbook in the corpus uses bracketed-color formats, so there was nothing to mirror — this was
   built on documented Desktop behavior and refuted live. **Resolution:** the `deltaColorBySign`
   theme flag is retained (additive, default off) but the builder falls back to the arrow-only
   format when it is set — it never emits the broken construct. The correct `_nearest_tableau_format_color`
   mapping is kept + unit-tested, ready if a future Cloud release renders section colors here. Color-
   by-sign in a KPI tile would need a different mechanism (e.g. a calculated boolean + a value→color
   `<encoding>`, which this builder does not emit) — recorded for a future slice, not shipped.

   **Follow-up round, `sizing-mode='fixed'` hypothesis — tested, REFUTED.** Our emitted
   `<dashboard><size>` carried equal `min`/`max` but no `sizing-mode` attribute, unlike every mined
   exemplar dashboard (`WB-118`/`WB-117` both carry `<size ... sizing-mode='fixed'/>`) — a
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

   **BEAUTY-GATE hotfix round — REVISED, root cause FOUND.** A user reported "I can't see the
   numbers" after opening a themed dashboard *interactively* in a browser — the invisibility
   reproduced live on Tableau Cloud, not just in static image renders, reopening this constraint.
   The **decisive experiment never previously run**: publishing the UNTOUCHED `WB-118` exemplar
   workbook as-is to our OWN Tableau Cloud dev site. Its real "Superstore Dashboard" (mixing BAN tiles with charts) rendered every
   number PERFECTLY on our site — conclusively ruling out "pre-existing Tableau Cloud
   dashboard-rendering characteristic... not achievable through `twb_builder.py` XML changes alone"
   as the conclusion. The failure was always in OUR dashboard's zone XML specifically; the prior
   round's V13–V23 ladder simply hadn't tried the right variable yet (it never tried removing the
   `layout-basic` wrapper, and only ever tested a LEAF zone's `is-fixed`/`fixed-size` in isolation,
   never a full CASCADE through every ancestor level).

   An exhaustive live diff of the exemplar's real KPI-row zone tree against ours, plus a second
   bisect ladder (V24–V25, then three more full-pipeline probe rounds against the real exec-audience
   4-tile pipeline), found the true shape requires cascading `is-fixed='true'`/`fixed-size='<N>'`
   through **three** nesting levels, not one:
   1. The **KPI band CONTAINER** itself (the `param='horz'` flow holding all the tiles) —
      `is-fixed='true' fixed-size='140'`, PLUS `layout-strategy-id='distribute-evenly'` (an
      XSD-valid `ZoneLayoutType-ST` enum value — `basic`/`free-form`/`flow`/`distribute-evenly`/
      `trivial` — mined verbatim from the exemplar's own KPI row, zone id 9, and never previously
      emitted by this builder at all).
   2. An intermediate **wrapper** flow around each tile — `is-fixed='true' fixed-size='210'`.
   3. The tile's own **leaf** worksheet zone — `is-fixed='true' fixed-size='150'`.

   A single-tile isolated test (V25) with only levels 2–3 rendered correctly — but that test's
   wrapper was a DIRECT child of the dashboard's outer `vert` flow; the real pipeline nests an
   EXTRA dedicated KPI-band container (level 1) between the outer flow and each tile's wrapper,
   and a live probe of the real 4-tile exec pipeline with only levels 2–3 applied still rendered
   every value as a static `"####"` placeholder — byte-for-byte IDENTICAL regardless of BAN font
   size (10px/17px/36px tested) or workbook identity (ruling out both a font-fit and a render-cache
   explanation). Only adding level 1 (the band container's own cascade + `distribute-evenly`) fixed
   it: a live render of the full exec pipeline (4 KPI tiles, `executive_dark` theme, real brand
   typography, real 9994-row Superstore data) showed all four values clearly — `"$2,297.4K"` /
   `"$286.3K"` / `"37.9K"` / `"1,561"`. Also mirrors the exemplar's own pattern of leaving exactly
   ONE tile in a multi-tile row unwrapped (non-fixed), as its flow's flexible anchor — wrapping
   *every* tile (no flexible sibling at all) was independently tested and also failed.

   One known, deferred, SEPARATE issue: the BAN delta line (the secondary "vs. prior period" value)
   still does not render in the real exec pipeline. Root-caused this round to the underlying
   dataset, not the zone mechanism: the "Sales Difference" field the planner selects as
   `delta_measure` for this specific CSV is 100% `NULL` across all 9994 rows (verified locally), so
   `SUM([Sales Difference])` is a `NULL` aggregate — Tableau shows nothing for that one run's arrow
   -formatted CDATA placeholder rather than a rendering failure. This is an upstream field-selection
   heuristic concern (the planner should not select an all-NULL field as a delta measure), not a
   `twb_builder.py` XML-mechanism defect, and is out of scope for this constraint.

   `sidecar/twb_builder.py`'s `_kpi_wrapped_indices`, `_append_worksheet_zones`'s
   `wrap_fixed_size`/`wrapper_id_start` parameters, and `_build_dashboard`'s KPI-band container zone
   attributes now encode all three cascade levels. See
   `sidecar/tests/test_twb_kpi_styling_beauty_gate.py`'s module docstring for the full round-by-round
   probe narrative (rounds 1–6).

See `sidecar/twb_builder.py`'s `_kpi_tile_customized_label`/`_append_kpi_ban_calc_column`/
`_kpi_tile_pane_style_rules`/`_build_dashboard` docstrings for the encoding of these constraints into
the builder, and `sidecar/tests/test_twb_kpi_styling_customized_label.py`'s module docstring for the
full bisect-ladder narrative (V0 control graft → V1–V5 individual-attribute isolation → V7 raw-vs-
calculated field format → V9/V10 mark-labels-show necessary-but-not-sufficient → V11/V12 proof →
V13–V23 multi-zone-dashboard isolation → `sizing-mode='fixed'` follow-up, refuted → BEAUTY-GATE
hotfix round, V24–V25 + three-level cascade, root cause found and fixed — see
`test_twb_kpi_styling_beauty_gate.py`).

## Visual layer (reviews + visual norms) — Slice V

A second, independent evidence layer alongside `stats/*.yaml`'s XML-mined norms: a **vision**
review of the top-100 VOTD corpus's rendered PNG snapshots, which sees layout gestalt, palette
mood, and title treatment the way a human viewer does — structure the XML miner cannot express
and a cross-check on T2's title fix from the pixel side.

```
design/corpus/
  reviews/    design/corpus/reviews/visual_reviews.json  — raw resolved vision-agent reviews, never hand-edited
  stats/      design/corpus/stats/visual_norms.yaml       — aggregated visual norms, produced by scripts/aggregate-visual-norms.ts
```

Both files are produced by a separate (concurrent) vision-review workflow slice; this section
documents the CONTRACT `scripts/aggregate-visual-norms.ts` and `tests/designVisual.test.ts`
enforce against them — the schemas exported from `scripts/aggregate-visual-norms.ts` are the
single source of truth for both files' shapes (imported directly by the test, never
re-implemented) — until the data lands, `tests/designVisual.test.ts` skips its entire suite
(with a console warning) rather than failing.

### Per-image rubric summary

Each vision agent reads one top-100 corpus PNG and produces one `ReviewRecord` (see
`scripts/aggregate-visual-norms.ts`'s `ReviewRecordSchema` for the exact zod shape):

- `category` (`business_dashboard | data_journalism | personal_infographic | data_art`) +
  `category_confidence` (`high | medium | low`) + `category_rationale` (<=140 chars) +
  `disputed` (category disagreement across the full pass and the classification-only verify
  pass — see "Dispute semantics" below).
- `quality_flags` (`image_unreadable`, `truncated_long_scroll`, `marketing_frame_chrome`) —
  honesty flags, never silently dropped.
- `layout_archetype` (`kpi_band_top | grid_of_charts | hero_chart_supporting | single_viz |
  small_multiples | long_scroll_infographic | map_centric`) + `sidebar_panel` (boolean modifier).
- `background` (`{tone: light|dark|mid|image, tint: neutral|tinted|saturated}`).
- `palette_mood` — exactly 6 enums: `monochrome | single_hue_sequential |
  muted_plus_one_accent | two_tone | categorical_multi | full_color_illustrative`.
- `accent_count` (integer), `density` (`minimal | moderate | dense`), `whitespace` (`generous |
  balanced | tight`).
- `title` (`{position: top_left|top_center|top_right|overlay_on_viz|none, relative_size:
  dominant|prominent|modest|minimal (anchored: dominant >15% canvas height, prominent 8-15%,
  modest 4-8%, minimal <4%), case: all_caps|title_case|sentence_case|mixed, style:
  plain_sans|serif_display|condensed_bold|script_decorative}`).
- `bans` (`{present: boolean, count, placement: top_band|left_column|right_column|bottom_band|
  scattered_inline|none, style: boxed_tiles|open_numbers|none}`) — `count`, `placement`, `style`
  are only meaningful (non-null-and-non-"none") when `present` is `true`; `placement`'s `"none"`
  value is a REAL observation on every `present === false` record (verified against the data),
  not a schema escape hatch — see the `BanPlacementEnum` docstring in
  `scripts/aggregate-visual-norms.ts`.
- `chart_types` (<=5, open-ended lowercase snake_case tags — not a closed enum) +
  `chart_count_visible` (integer).
- `standout_techniques` (<=3 of `{tag: snake_case, note}`) + `lesson` (one actionable takeaway).

Where the V-phase plan left an enum's exact option set unspecified (`palette_mood`'s "6 enums",
`density`/`whitespace`, `title.case`/`title.style`, `bans.placement`/`bans.style`), the vocabulary
above is this codebase's fixed, documented choice — every review record must conform to it
exactly; there is no free-text escape hatch in these fields (see "no-literal boundary rule"
below for why that matters).

### Provenance triad (non-negotiable, orchestrator-injected)

Every `ReviewRecord` carries `repoUrl` (an opaque, stable `WB-NNN` identifier matching a
`design/references/top100/manifest.yaml` entry — see "Source-identity redaction" below),
`imageSha256` (of the reviewed PNG), and `workbookSha256` (of the source `.twb`/`.twbx`, from the
manifest) — injected by the review orchestrator, never agent-guessed. `imageUrl` (the original
tableau.com-hosted snapshot link) was part of this record until the anonymization pass dropped it
— it identified the source workbook's public feed entry and carried no aggregation value the
sha256 pair doesn't already provide. Every aggregated bucket in `visual_norms.yaml` carries up to
5 `citations`, each the full provenance triad for one distinct `repoUrl`, deterministically sorted
— the same "top-5 exemplar citations" discipline `sidecar/design_stats.py`'s `citations()` uses
for the XML-mined stats, reimplemented 1:1 in `scripts/aggregate-visual-norms.ts`.

### Source-identity redaction (policy)

The corpus is mined from real, public Tableau Public workbooks. Source identities (author
display names, profile handles, workbook titles, and the original `repoUrl`/filename) are
intentionally redacted to opaque, stable `WB-NNN` identifiers everywhere in the committed corpus —
`sha256` remains the integrity/provenance anchor (every citation is still independently
verifiable against the exact mined bytes), and the corpus stays honest about its scale ("mined
from N real public workbooks, identities redacted") without publishing a lookup back to any real
person's public profile.

### No-literal boundary rule (machine-enforced)

Visual-layer files describe **observations**, never literals a theme could cite directly:
`palette_mood`/`background.tone`/etc. are closed enums, never a hex string, and no field in
either `visual_reviews.json` or `visual_norms.yaml` may contain a `#rrggbb`-shaped substring —
enforced by `tests/designVisual.test.ts`'s hex-literal guard (the same `#[0-9a-fA-F]{3,8}`
pattern `tests/designCorpus.test.ts`'s theme-cites-recipe check uses). The guard blanks the
handful of known free-text fields (`category_rationale`, `lesson`,
`standout_techniques[].note`, `runMeta.failures[].reason`) before scanning `visual_reviews.json`
— those are the only fields where a hex-shaped substring could appear by pure English-prose
coincidence, never by design — so the guard only ever flags a real structured-field leak.
`visual_norms.yaml` has no free-text fields at all (aggregated buckets only), so it is scanned
unmodified. This keeps the visual layer strictly on the "observations, not literals" side of the
corpus's provenance discipline (see the top of this file) — application happens only through the
stats-reading CI gates below and `GAPS.md` §3 routing, never through a theme citing a visual-review
literal directly.

### Dispute semantics

The V-phase plan's reliability design: one full pass over all reviewed images, plus a
classification-only second ("verify") pass over `business_dashboard ∪ category_confidence=low`
— category errors are the only ones that corrupt the norm-driving `business_dashboard` stratum;
other field noise is absorbed by the existing `n < 15` confidence guard. `visual_reviews.json` is
the review workflow's **already-resolved** output — `scripts/aggregate-visual-norms.ts` performs
no dispute resolution of its own, it only reads the resolved flags:

- **Category disagreement** across the two passes -> `disputed: true` on the record. Its
  best-effort `category` value is retained for reference, but the record is EXCLUDED from the
  `business_dashboard` stratum (an uncertain category cannot safely gate the norm-driving
  stratum) and is counted in `category_distribution`'s own `disputed` bucket instead of its
  `category` value's bucket. It is NOT excluded from `strata.all_images` (a disputed record's
  non-category fields — layout, palette, density, ... — are still real observations; only its
  category is uncertain).
- **Field-level disagreement or a per-agent extraction failure** ("catch -> null -> filter")
  resolves to `null` for that one field only — dropped from just that field's own `n`, never
  from the record's other fields or the stratum's overall `n`. `truncated_long_scroll` is a
  special case of this: it excludes a record from `layout_archetype`'s `n` ONLY (a partially
  captured long-scroll infographic cannot be reliably shape-classified, but every other field
  is still a valid observation).
- `quality_flags.image_unreadable === true` excludes the record ENTIRELY — every bucket, every
  stratum, `category_distribution` included.

### `visual_norms.yaml` structure

```yaml
corpus_images: <int>       # reviews.length + runMeta.failures.length (derived, not copied)
n_reviewed: <int>          # reviews.length
method:
  model: <string>
  date: <string>           # the review run's date (provenance, not a "generated at" field)
  passes: <int>
  disputed_excluded_from_strata: true
category_distribution:     # {n, confidence, buckets: {business_dashboard|data_journalism|
  ...                      #  personal_infographic|data_art|disputed: {count, rate}}, citations}
strata:
  business_dashboard:      # PRIMARY — category === business_dashboard AND NOT disputed
    n: <int>
    confidence: ok|low
    layout_archetype: {...}            # categorical; truncated_long_scroll excluded from n only
    sidebar_panel_usage_rate: {...}    # rate bucket (no citations, matches design_stats.py's rate())
    ban_usage_rate: {...}              # rate bucket
    ban_placement: {...}               # categorical, gated on bans.present === true (numerator)
    ban_count: {...}                   # numeric, gated on bans.present === true (numerator)
    title_position: {...}              # categorical
    title_relative_size: {...}         # categorical
    title_case: {...}                  # categorical
    title_style: {...}                 # categorical
    background_tone: {...}             # categorical
    palette_mood: {...}                # categorical
    accent_count: {...}                # numeric
    density: {...}                     # categorical
    whitespace: {...}                  # categorical
    chart_type_prevalence: {...}       # MULTI-label categorical (rates need not sum to 1)
    chart_count_visible: {...}         # numeric
  all_images:               # context stratum — every readable review, any category/dispute status
    <same shape as business_dashboard>
```

Every numeric (`{n, confidence, median, p25, p75, citations}`) and categorical
(`{n, confidence, buckets: {value: {count, rate}}, citations}`) bucket carries up to 5
citations; simple usage-rate buckets (`{n, confidence, count, total, rate}`) do not — the exact
same split `sidecar/design_stats.py`'s `distribution()`/`rate()` precedent establishes for
`dashboard_norms.yaml`. `n < 15` -> `confidence: "low"` everywhere (`MIN_CONFIDENT_N`, identical
to the T1 stats floor) — a `confidence: low` bucket is never auto-applied, exactly like the
XML-mined stats.

### Auto-applied CI gates (T2 precedent)

`tests/designVisual.test.ts` auto-applies exactly 3 stats-reading gates, each individually
skipped (not failed) when its stratum bucket is `confidence: low`:

1. **Layout-vocabulary coverage** — `kpi_band_top` + `grid_of_charts` (this codebase's
   emittable archetypes) must jointly cover >=40% of `strata.business_dashboard.layout_archetype`.
2. **BAN placement** — the modal `strata.business_dashboard.ban_placement` bucket must be
   `top_band` (validates the `kpi_band_over_charts` construct).
3. **Title position sanity** — the builder's title placement (`top_left`, per the current header
   zone emission) must fall within the `strata.business_dashboard.title_position` buckets that
   jointly cover >=50% cumulative rate (sorted by rate, descending).

Everything else (layout-default flips, density/gutter px defaults, palette-mood -> theme
hints, long-scroll grammar, any low-confidence bucket) routes to `GAPS.md` §3 instead of being
auto-applied — see that file.

### Regenerating

```bash
npx tsx scripts/aggregate-visual-norms.ts \
  --reviews design/corpus/reviews/visual_reviews.json \
  --out design/corpus/stats/visual_norms.yaml
```

Both flags default to those exact paths (repo-root-relative) — `npx tsx
scripts/aggregate-visual-norms.ts` with no arguments regenerates in place. `aggregateVisualNorms()`
itself is a pure function (no filesystem or wall-clock access) exported for
`tests/designVisual.test.ts`'s REGENERATION EQUALITY check: it imports the function, runs it
in-process against the committed `visual_reviews.json`, and deep-equals the result against the
committed `visual_norms.yaml` — the same "aggregate twice -> byte-identical output" contract
`sidecar/design_stats.py` documents for the T1 stats files (see "No wall-clock timestamp" above),
just enforced by a TS unit test instead of a Python one.
