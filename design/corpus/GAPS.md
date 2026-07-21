# `design/corpus/GAPS.md` — low-confidence norms & unimplemented high-frequency constructs

Routing target for two kinds of finding from `sidecar/design_stats.py`'s corpus aggregation
(Slice T1, PLAN.md's Top-100 Corpus plan) that must **never** be auto-applied to themes or the
builder:

1. A stats bucket with `n < 15` (`confidence: low` in `design/corpus/stats/*.yaml`) — the plan's
   minimum-sample guard. Listed here for a human/future slice's explicit judgment call.
2. A high-frequency real-world construct (the plan's own example: "filter zones in >40% of top
   dashboards") the codebase does not yet implement. T2's mandate is to implement at most the top
   ONE if cheap, else defer — documented here either way.

This file is **read, not auto-consumed** — nothing in `sidecar/` or `src/` imports it. It is a
durable record for T2/T3 to triage against, produced once (Slice T1) and updated by hand if a
future corpus refresh changes the picture.

## 1. Low-confidence stats buckets (n < 15)

| Bucket | n | Corpus | Note for T2/T3 |
|---|---|---|---|
| `dashboard_norms.yaml` → `strata."<900"` (every sub-bucket: `title_fontsize`, `title_height_ratio`, `canvas_width_px`, `margin_px`, usage rates, ...) | 3 | 110 workbooks, 201 dashboards | Almost no narrow-canvas (< 900 px, likely mobile-device-layout-style) dashboards in the mined corpus. **Do not derive a "narrow canvas" title-size norm from this stratum.** The plan's own fallback applies: use the 900-1400 stratum's stratified median (n=141, `confidence: ok`) or the corpus-wide p75 instead of this bucket. |
| `dashboard_norms.yaml` → `kpi_band` (corpus-wide, not stratified — see SCHEMA.md) | 3 | 110 workbooks, 201 dashboards | Only 3 real KPI-band-like rows (horz `layout-flow` + `distribute-evenly` + >=3 `is-fixed` children) found across the ENTIRE mined corpus, despite 110 workbooks. The shape SCHEMA.md's render-constraint #5 documents as "the working cascade" is real but rare in the wild — VOTD-curated art/analysis vizzes skew toward single-viz layouts, not multi-tile exec KPI rows. T2 should treat the mined `height_ratio` median (~0.15) and `child_count` median (4) as directional, not authoritative; do not hard-fail a design that deviates from it. |
| `story_norms.yaml` → everything (`usage_rate`, `points_per_story`, `caption_length`, `nav_type_distribution`) | 0 | 110 workbooks | **Zero storyboards found in the entire 110-workbook corpus** (3 exemplars + 100 VOTD top-100 + 7 pre-existing refs). This is the plan's own predicted "expected low-n case" for story buckets, now confirmed with a real number, not an assumption. T3's story-gating logic (plan bullet (d): "if the story bucket is low-confidence, gate on explicit ask/persona-preference only") is the ONLY defensible path — there is no mined evidence to inform a usage-rate-based default at all. A future corpus refresh targeting Tableau Public's dedicated "Stories" gallery (rather than VOTD, which evidently skews toward single-view vizzes) could fill this gap; not attempted in T1 (out of scope — VOTD is the plan's chosen "top" definition). |

## 2. High-frequency constructs found but not yet implemented

None found that clear the plan's own bar (its example: ">40% of top dashboards"). For reference,
the closest candidates and their actual corpus rates (all `confidence: ok`, n >= 15):

| Construct | Usage rate (900-1400 stratum, n=141) | Usage rate (>1400 stratum, n=57) | Already implemented? |
|---|---|---|---|
| `paramctrl` zone (parameter control) | 23.4% | 31.6% | Not yet a first-class builder construct. Below the 40% bar — **no T2 action mandated**, but worth a future look if a later corpus refresh pushes it over the line. |
| `filter` zone (explicit filter card) | 17.0% | 24.6% | Not yet a first-class builder construct. Below the 40% bar — **no T2 action mandated**. |
| Device layouts (`<devicelayouts>`) | 23.4% | — | Not implemented (single fixed-size canvas only). Below the 40% bar — **no T2 action mandated**; device layouts are also a materially larger feature (phone/tablet re-flow) than a single cheap construct, so even if it cleared the bar it would likely be a "defer, documented" case per the plan's own "implement only the top ONE if cheap" instruction. |
| `mark-labels-show` (data labels on marks) | 87.2% | 82.5% | **Already implemented** — this is exactly the D0 `chrome_rules.yaml` construct `sidecar/twb_builder.py` already emits (`showMarkLabels` in the theme schema). Not a gap; flagged here only to show the corpus independently confirms it as the single most common real-world chrome choice (>80% in every stratum), validating the existing default. |

**Conclusion**: no new construct implementation is triggered by this corpus run. T2 should focus
its "implement the top ONE if cheap" mandate on re-checking this table after any future corpus
refresh, rather than implementing something today that the mined evidence does not yet support at
the plan's stated bar.

## 3. Visual-layer norms awaiting application slices

`design/corpus/reviews/visual_reviews.json` (99/99 reviewable top-100 images) and
`design/corpus/stats/visual_norms.yaml` (produced by `scripts/aggregate-visual-norms.ts`) have
landed — see `design/corpus/SCHEMA.md`'s "Visual layer" section for the schema/provenance contract
and `design/corpus/VISUAL_REVIEW.md` for the full qualitative synthesis (method, coverage,
technique-tag frequency table, top-10 business-dashboard lessons, T2 title cross-check). This
section routes every visual norm the V-phase plan marks NOT auto-applied — the 3 auto-applied
stats-reading CI gates already live in `tests/designVisual.test.ts` and are exempt from this
routing table (they are additive citations of the mined evidence, not new constructs); everything
below is a candidate for a FUTURE slice's explicit judgment call, not something this slice applies.

**The `business_dashboard` stratum landed `n=36` (`confidence: ok`, above the `n>=15` floor)** —
the plan's own headlined worst case ("VOTD cannot source business-dashboard visual norms — curate
a business gallery") did NOT materialize; the norms below are usable, with the VOTD-skew caveat
`VISUAL_REVIEW.md`'s closing section documents (business_dashboard is only 36.4% of the reviewed
corpus — journalism is the plurality at 41.4%).

### `confidence: low` buckets (routed, not applied)

Every bucket in both strata landed `n >= 15` (`confidence: ok`) EXCEPT the `data_art` category
itself as an implicit sub-population: `category_distribution`'s `data_art` bucket is `n=7`
(count, not a distribution `n` — the categorical distribution's own top-level `n=99` is `ok`, but
if a future slice wanted an art-specific stratum the way `business_dashboard` gets one, `n=7`
would land `confidence: low`). No `visual_norms.yaml` bucket within `strata.business_dashboard` or
`strata.all_images` itself is `confidence: low` — both landed `n` at or above the floor for every
sub-bucket (`business_dashboard.layout_archetype` sub-`n=31` after the `truncated_long_scroll`
exclusion; `business_dashboard.ban_placement`/`ban_count` sub-`n=32` after the `bans.present`
numerator gate — both still `ok`). This is a stronger evidence base than T1's XML-mined
`dashboard_norms.yaml` strata (which DID hit `n<15` on the `<900`px canvas stratum and the
corpus-wide `kpi_band` bucket — see §1 above); no visual-layer bucket needs the same "treat as
directional only" caveat T1's low-n buckets require.

### Layout-default flips (candidate future slices)

- **`kpi_band_top` (58.1%, n=31 eligible) is the dominant business-dashboard layout archetype**,
  well ahead of `grid_of_charts` (19.4%) and `hero_chart_supporting` (12.9%) — this directly
  validates the existing `kpi_band_over_charts` builder default (a KPI band on top of supporting
  charts) rather than suggesting a flip. No action indicated; corroborates the current default.
- **`hero_chart_supporting` (12.9%) is a real, non-trivial minority pattern** — a single dominant
  chart with smaller supporting visuals around it, distinct from both `kpi_band_top` and
  `grid_of_charts`. Not currently an emittable builder archetype. A future slice could consider it
  as a second layout choice (e.g. for a "spotlight one KPI/chart" persona/audience combination),
  but at 12.9% (well under the plan's own >=40% high-frequency bar used elsewhere in this file) it
  does not clear the "implement automatically" threshold — candidate for future judgment, not a
  T-phase mandate.
- **`grid_of_charts` (19.4%) is a real secondary pattern** but, combined with `kpi_band_top`,
  already clears CI gate (a)'s >=40% joint-coverage bar (58.1% + 19.4% = 77.4% combined in the
  `business_dashboard` stratum) — no gap here, the builder's emittable vocabulary already covers
  the two dominant real-world shapes.

### Palette-mood -> theme-selection hints (candidate future slice)

`muted_plus_one_accent` is the dominant business-dashboard `palette_mood` (55.6%, n=36) — a
neutral/muted base with exactly one saturated accent color reserved for the metric that matters.
This is qualitatively consistent with the existing `executive_dark`/`executive_light` themes'
"one accent, mostly neutral" design language, but no theme currently encodes `palette_mood` as an
explicit selection axis (`selectTheme` only reads `audience`/`persona`/`artifact` tags — see
`SCHEMA.md`'s "Retrieval" section). A future slice could add a `palette_mood` tag to each theme
and let `selectTheme` prefer a `muted_plus_one_accent`-tagged theme by default for the
`business_dashboard` artifact, falling back to today's audience/persona-only resolution otherwise.
`single_hue_sequential` (19.4%) and `categorical_multi` (13.9%) are secondary but real patterns —
worth a second/third theme variant in a future slice, not a T-phase requirement today.

### Density/whitespace defaults (candidate future slice)

`moderate` density (52.8%) and `balanced` whitespace (75.0%) are the business-dashboard norms —
directionally consistent with this codebase's existing chrome defaults (no gap flagged; T1's own
`chrome_rules.yaml`/`mark-labels-show` precedent already leans toward a clean-but-not-sparse
default). `dense` (38.9%) is a real, sizeable minority — a future slice could offer a
`density: dense` builder variant (tighter KPI-band packing, more charts per row) for
data-team/analyst personas that specifically want higher information density, gated on explicit
persona choice rather than a silent default flip.

### Long-scroll grammar

Zero `business_dashboard` records use `long_scroll_infographic` (0/36) — the archetype exists
entirely in `data_journalism`/`data_art`/`personal_infographic` territory (long-form
storytelling), not business dashboards. No long-scroll grammar work is indicated for this
codebase's business-dashboard-only builder scope; this finding CLOSES that candidate rather than
opening it.

### BAN placement/style (validates existing construct, one new candidate)

`top_band` (68.8% of `bans.present===true` records, n=32) is the dominant BAN placement — directly
validates the CI-gated `kpi_band_over_charts` construct (see gate (b) above). `scattered_inline`
(21.9%) is a real secondary pattern (BANs distributed inline throughout a dashboard rather than
banded at top) — the qualitative lessons in `VISUAL_REVIEW.md` (particularly lesson #1, the
BAN+delta+sparkline atomic-card pattern) suggest this is less about WHERE the band sits and more
about what's INSIDE each tile; a future slice's highest-leverage single addition, per the top-10
lessons digest, would be embedding a delta badge + inline sparkline INTO the existing KPI tile
construct (lessons #1, #9) rather than a placement change.

### T2 title fix — cross-checked, not flagged

See `VISUAL_REVIEW.md`'s dedicated "T2 title cross-check" section: both the `_HEADER_ZONE_H`
pixel-ratio fix and the `top_left` placement independently agree with the vision-side evidence.
No action routed here — this is a confirmation, not a gap.

## 4. External skill enhancement backlog (analyzed 2026-07-20)

An external agent-skill review (six external Claude `SKILL.md` playbooks over the *official*
tableau-mcp's read-only surface — see `docs/adr/0014-external-skill-analysis.md` for the full
analysis and the applied/declined split) surfaced eight candidate enhancements. Three deterministic,
low-risk candidates were applied in the same slice this section was written (WCAG contrast checking
in `validate_brand`, a default descending-by-measure sort for ranking bar charts, and additive Pulse
enum/schema widening — see ADR-0014 + ADR-0011's 2026-07-20 update). This section records the
remaining candidates: those deferred to a future slice, and those declined outright with reasons.

> **Status update (2026-07-21).** Most of this backlog has since shipped. **#1 + #2** (YoY
> period-comparison delta calc + comparison-period heuristic) landed as **M2/M3**. **#6** (metric
> dictionary → `generate_metric_dictionary`), **#7** (sign-based delta color, `deltaColorBySign`,
> VERIFY-LIVE), a scoped **#8** (`scan_governance`), and the previously-declined VizCritique value
> (as the deterministic `critique_dashboard`) all landed in the **N-phase** — see
> `docs/adr/0015-deterministic-self-critique.md`. The table below is retained as the original
> analysis of record; only the genuinely-declined set (next section) remains unbuilt by design.

### Deferred candidates (original analysis — most now shipped, see status note above)

| # | Candidate | Effort | Hook | Why deferred |
|---|---|---|---|---|
| 1 | **YoY/period-comparison calc-column for KPI delta** — the row-level CY/PY recipe from Calc-Engine, generalizing `sidecar/twb_builder.py`'s hardcoded `_append_kpi_ban_calc_column` `SUM([field])` shape into a real prior-period comparison. This is the one shape that closes ACCEPTANCE.md's own NULL-delta residual for a dimensionless BAN (a KPI tile currently cannot show a real period-over-period delta without a pre-computed comparison measure supplied by the caller). | M | `sidecar/twb_builder.py` `_append_kpi_ban_calc_column` (generalize) + `src/planner/plan.ts` (emit the calc spec) | Real calc-formula generation touching the KPI-tile BAN mechanism, which already carries a documented 12-variant live-probe bisect history (Slice D4) — a change here needs its own live-probe cycle, not a same-slice addition alongside three independent, lower-risk deterministic changes. |
| 2 | **Comparison-period selection heuristic** (question keyword → YoY/MoM/vs-target) — pairs with #1; a planner-side heuristic mapping natural-language cues ("compared to last year", "month over month") to the right comparison calc. | S | `src/planner/plan.ts` | Depends on #1 landing first (no comparison calc column to select a period for yet). |
| 6 | **Metric-dictionary doc recipe** — Scribe's Metric-Builder pattern (a structured metric-definition writeup: name, formula, grain, owner) applied over this server's existing `get_datasource_fields` output. | S | new `docs/` generation helper, or a new tool surfacing structured field metadata | Pure-documentation value-add, not blocking any existing gap; lower priority than the three applied changes and the M-effort delta-calc work above. |
| 7 | **Sign-based conditional KPI delta color** — Calc-Engine's KPI-status pattern combined with `palette.semantic.good`/`bad`: color the delta value itself (not just an arrow) based on its sign. | L, RISKY | `sidecar/twb_builder.py`'s `_kpi_tile_customized_label` mechanism | The customized-label BAN mechanism already required a 12-variant live-probe bisect to prove working (Slice D4 — see its module-level "BAN mechanism" comment block). Dynamic per-value color inside a static `<customized-label>` run is a materially different mechanism than what was proven and needs its own live-probe cycle before it can ship with confidence, not a speculative attempt alongside this slice's three low-risk, previously-provable-by-XSD-and-math changes. |
| 8 | **Governance-scanner-lite** — stale-content, Default-project, and naming-convention checks built ONLY from what this server's existing `list_content`-family tools already return (no new read scope). | M | new tool over `src/tools/` list/get calls already present | Full Governance-Scanner parity needs read scope (workbook/view metadata introspection) this repo deliberately does not carry (see ADR-0014's "declined" list) — a lite variant scoped to existing list-content output is plausible future work, not attempted this slice to keep the applied bundle to purely deterministic, already-tested primitives (contrast math, XML structural predicates, additive schema fields). |

### Not worth pursuing (recorded so the decision doesn't get re-litigated)

- **VizCritique-Pro's full LLM-judged dashboard scoring as a server tool** — violates this
  project's A-01 invariant (no LLM call inside the server; see `CODEBASE.md`). The rubric's fixed
  NUMBERS (4.5:1/3:1 contrast ratios) were ported as pure math; the JUDGMENT mechanism was not.
- **Scribe's Auto-Doc and Governance-Scanner's full 7-domain audit** — both need the official
  server's read/introspection scope (`get_view_image`, full `list_content` metadata, workbook
  read-back) this repo deliberately does not carry; this server is publish-oriented, not a
  read/audit tool. (A read-scope-free LITE governance variant is deferred candidate #8 above, not
  declined outright.)
- **Calc-Engine's spatial/sets/zone-visibility/parameter-actions calc recipes** — no matching
  `twb_builder.py` construct exists for any of these (no zone-visibility toggle, no
  parameter-action wiring, no spatial calc emission path); YAGNI — a calc-formula recipe with no
  builder support to consume it would be dead code.
- **Dashboard-Blueprint's filter-card and device-layout tables** — already independently declined
  above in §2 from real mined evidence (filter zones 17.0-24.6%, device layouts 23.4% — both below
  the plan's own 40% "implement automatically" bar). This skill's guidance doesn't change that
  evidence-based conclusion.
- **Memory-file/scheduled-agent continuity patterns** — several of the analyzed skills assume a
  persistent agent session/memory file between runs; incompatible with this server's
  stateless-per-call architecture (ADR-0005/0007) — there is no server-side session to persist a
  memory file against.

See `docs/adr/0014-external-skill-analysis.md` for the full context, alternatives considered, and
consequences of this triage.
