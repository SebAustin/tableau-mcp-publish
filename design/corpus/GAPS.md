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
