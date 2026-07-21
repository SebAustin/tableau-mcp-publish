# `design/corpus/VISUAL_REVIEW.md` — Slice V qualitative synthesis

Companion narrative to `design/corpus/reviews/visual_reviews.json` (raw resolved vision-agent
reviews) and `design/corpus/stats/visual_norms.yaml` (deterministic aggregation, produced by
`scripts/aggregate-visual-norms.ts`) — see `design/corpus/SCHEMA.md`'s "Visual layer" section for
the machine-enforced schema/provenance contract both files share. This file is the **human-read**
half: method, coverage, a technique-tag frequency table, the top-10 transferable lessons for
business dashboards, a T2 title cross-check, and the VOTD-skew caveat every number below inherits.

## Method

Two vision-agent passes over the `design/references/top100/manifest.yaml` corpus's rendered PNG
snapshots (VOTD — "Viz of the Day" — marketing thumbnails, `claude-fable-5 (vision subagents, low
effort)`, run date `2026-07-20`):

1. **Full pass** — every reviewable image scored against the full per-image rubric (see
   `SCHEMA.md`'s "Per-image rubric summary"): category, layout archetype, palette/background/
   density/whitespace, title treatment, BAN (Big-Ass-Number/KPI-tile) usage, chart-type mix,
   standout techniques, and one actionable lesson.
2. **Classification-only verify pass** — a second, independent look at
   `business_dashboard ∪ category_confidence=low` records specifically, to catch category
   misclassifications before they corrupt the norm-driving `business_dashboard` stratum (the one
   field-level error the `n>=15` confidence guard does not protect against, since a wrong
   *category* silently moves a record into or out of a stratum rather than just nulling one
   field).

`visual_reviews.json` is this workflow's **already-resolved** output — `scripts/
aggregate-visual-norms.ts` performs no dispute resolution of its own (see that file's module
docstring's "Dispute semantics" section, mirrored in `SCHEMA.md`).

## Coverage

- **99/99 reviewed** of the 100 workbooks the top-100 manifest marks `status: downloaded`. The
  1 gap is `"WB-001"` ("Superstore Regional Analysis", downloaded successfully as a `.twbx`) —
  its VOTD marketing-feed image URL had rotated out of `tableau.com`'s feed by review time
  (`runMeta.images_of_manifest: "99/100 (missing: \"WB-001\")"`), so no reviewable PNG existed
  for it. This is a source-feed availability gap, not a review failure — `runMeta.failures` is
  empty (zero agent-side errors across all 99 reviewed images).
- **Category split** (`category_distribution` in `visual_norms.yaml`, `n=99`):

  | Category | Count | Rate |
  |---|---|---|
  | `data_journalism` | 41 | 41.4% |
  | `business_dashboard` | 36 | 36.4% |
  | `personal_infographic` | 11 | 11.1% |
  | `data_art` | 7 | 7.1% |
  | `disputed` | 4 | 4.0% |

  4 records disputed (category disagreement between the full pass and the verify pass) — retained
  in `strata.all_images` (their non-category fields are still real observations) but excluded from
  `strata.business_dashboard` (see "Dispute semantics" in `SCHEMA.md`).

## Technique-tag frequency table (by category, business_dashboard-first)

Every `standout_techniques[].tag` observed, grouped by category, most-cited first within each
group (exemplar `repoUrl`s truncated to 3; full lists are in `visual_reviews.json`). Only tags with
>=2 citations are shown per category (the long single-citation tail is real but not tabulated here
— see the raw JSON for the complete, unabridged list).

### `business_dashboard` (n=36, the norm-driving stratum)

| Tag | Count | Exemplars |
|---|---|---|
| `dark_canvas_light_cards` | 16 | WB-021, WB-023, WB-034 |
| `muted_base_single_accent` | 15 | WB-005, WB-027, WB-029 |
| `color_encoded_headline` | 10 | WB-005, WB-023, WB-040 |
| `integrated_legend` | 9 | WB-029, WB-030, WB-050 |
| `big_number_hero` | 7 | WB-021, WB-040, WB-050 |
| `sparkline_rows` | 7 | WB-023, WB-027, WB-035 |
| `custom_iconography` | 5 | WB-029, WB-060, WB-101 |
| `minimal_axis_chrome` | 3 | WB-030, WB-064, WB-143 |
| `callout_boxes` | 3 | WB-065, WB-103, WB-139 |
| `progress_tracker_motif` | 2 | WB-040, WB-120 |
| `small_multiples_rhythm` | 2 | WB-052, WB-075 |
| `annotation_layer_storytelling` | 2 | WB-052, WB-143 |

### `data_journalism` (n=41, the largest single category)

| Tag | Count | Exemplars |
|---|---|---|
| `annotation_layer_storytelling` | 19 | WB-009, WB-012, WB-013 |
| `color_encoded_headline` | 17 | WB-004, WB-006, WB-009 |
| `integrated_legend` | 11 | WB-033, WB-049, WB-071 |
| `custom_iconography` | 9 | WB-006, WB-043, WB-048 |
| `muted_base_single_accent` | 8 | WB-012, WB-013, WB-048 |
| `small_multiples_rhythm` | 8 | WB-033, WB-071, WB-061 |
| `radial_composition` | 7 | WB-069, WB-070, WB-087 |
| `chart_as_texture` | 7 | WB-069, WB-072, WB-088 |
| `dark_canvas_light_cards` | 5 | WB-026, WB-055, WB-090 |
| `section_divider_rules` | 3 | WB-048, WB-096, WB-131 |
| `long_form_sectioning` | 2 | WB-009, WB-033 |
| `sparkline_rows` | 2 | WB-059, WB-129 |
| `minimal_axis_chrome` | 2 | WB-084, WB-096 |

### `personal_infographic` (n=11)

| Tag | Count | Exemplars |
|---|---|---|
| `integrated_legend` | 4 | WB-019, WB-028, WB-105 |
| `dark_canvas_light_cards` | 3 | WB-003, WB-019, WB-126 |
| `muted_base_single_accent` | 2 | WB-002, WB-113 |
| `photo_integration` | 2 | WB-016, WB-017 |
| `custom_iconography` | 2 | WB-017, WB-111 |

### `data_art` (n=7, smallest stratum — every count below is directional, not `confidence: ok`)

| Tag | Count | Exemplars |
|---|---|---|
| `chart_as_texture` | 5 | WB-014, WB-018, WB-031 |
| `dark_canvas_light_cards` | 4 | WB-014, WB-042, WB-067 |
| `radial_composition` | 3 | WB-031, WB-042, WB-053 |
| `integrated_legend` | 2 | WB-042, WB-067 |
| `generous_header_whitespace` | 2 | WB-053, WB-141 |

## Top-10 transferable lessons for BUSINESS dashboards

Mined from the `business_dashboard` stratum's (`n=36`, `category === "business_dashboard" && !
disputed`) `lesson` fields and `standout_techniques`, clustered by recurring theme (not simply the
36 lessons verbatim — several restate the same pattern in different words). Every lesson below
cites >=2 real `repoUrl`s; the full per-record `lesson` text is in `visual_reviews.json`.

1. **Make the KPI tile an atomic BAN + delta + inline trend card, not three separate elements.**
   The single most common real pattern in the corpus: a big number, a colored vs-prior-period
   delta badge, and a small sparkline/trend line inside ONE card, so magnitude, direction, and
   history read in one glance instead of requiring the eye to cross-reference a separate chart.
   *Exemplars:* `WB-065`, `WB-116`,
   `WB-050`, `WB-040`,
   `WB-102`.
2. **Keep the base palette to one muted/neutral hue and reserve exactly one saturated accent for
   the metric that needs attention.** Color reads as *meaning* (an alert, a highlight), not
   decoration, when it is scarce. *Exemplars:* `WB-023`,
   `WB-027`, `WB-029`,
   `WB-035`.
3. **Dark canvas + light/white content cards (or the tinted-canvas variant) is the single most
   common chrome technique in the stratum** (`dark_canvas_light_cards`, 16/36 records — the
   highest-frequency tag of any kind in this stratum). Layering a neutral dark field under
   brighter foreground cards creates depth and pulls the eye to the cards' content.
   *Exemplars:* `WB-076`, `WB-078`,
   `WB-021`, `WB-123`.
4. **Let color-coded labels double as the legend, instead of a separate legend box.** Coloring a
   value label or series name to match its own mark (rather than a neutral text color + a
   detached legend) cuts visual clutter and keeps the reading path linear.
   *Exemplars:* `WB-064` (labels colored to match marks, "eliminating separate legend
   boxes"), `WB-005`, `WB-050`.
5. **Pair every KPI or chart panel with a one-line plain-language takeaway, not just a number.**
   Several records explicitly favor a short interpretive caption ("above/below expected range",
   "read the conclusion, not just the number") over a bare metric — directly relevant to this
   codebase's own `footer_takeaway_captions` template work (see the technique-tag table: this tag
   appears once in this stratum but the *pattern* — captioning a number — recurs across multiple
   independent lessons below). *Exemplars:* `WB-098`,
   `WB-139`,
   `WB-143`, `WB-109`.
6. **Give each KPI/category a small custom icon alongside its label.** A compact glyph next to a
   caps-case label makes a KPI band scannable without reading every word.
   *Exemplars:* `WB-029`, `WB-060`,
   `WB-101`, `WB-109`,
   `WB-123`.
7. **Repeat one uniform KPI-card template across every metric (small-multiples for KPIs, not just
   for charts).** A consistent vertical stack — label, period, accent-colored number, delta,
   trend — repeated identically per metric reads faster than N differently-styled cards.
   *Exemplars:* `WB-144` ("repeated identically across
   metrics"), `WB-103` ("Structure each KPI as a uniform card"),
   `WB-052`.
8. **Group related content on tinted or rounded "section" cards to create visual chunking.**
   A soft background tint (not just whitespace) around a related cluster of charts/KPIs signals
   "this is one analytical block" without needing a rule line.
   *Exemplars:* `WB-021` (`color_zoned_cards`), `WB-134`
   (`tinted_section_cards`), `WB-034` (white rounded
   card).
9. **Pair a headline BAN with an adjacent context chart or drill-down panel, not just a bare
   number.** A small distribution chart, trend, or selectable detail view next to the KPI answers
   "why" alongside "what."
   *Exemplars:* `WB-051` (`ban_with_context_chart`),
   `WB-030` ("Pair each top BAN with a supporting chart column
   below it"), `WB-142` (overview KPI band + selectable detail
   drill-down).
10. **Reserve a dedicated callout box / delta badge for the ONE number that most needs attention**,
    distinct from the rest of the KPI band's plainer styling — a visual escalation for the
    single most important metric rather than uniform emphasis everywhere.
    *Exemplars:* `WB-065`, `WB-103`,
    `WB-139`, `WB-005`
    ("high-contrast callout strip").

## T2 title cross-check

T2 (PLAN.md's Top-100 Corpus plan) shipped two title fixes from the **XML-mined** side
(`dashboard_norms.yaml`, `sidecar/twb_builder.py`):

- **Header zone height**: `_HEADER_ZONE_H = 6960` (0.0696 of the 0-100000 canvas grid — the T1
  mined stratified median `title_height_ratio` for dashboard-sized (900-1400px) canvases, n=107,
  `confidence: ok`), replacing a single-exemplar value that beauty-gate review flagged as visually
  too large.
- **Auto-shortened title text** (`shortenDashboardTitle` in `src/planner/plan.ts`) to keep a long,
  question-derived title from overflowing that header zone.
- **Header zone placement**: full-width (`w=100000`), `x=0, y=0` (top of canvas), left-aligned
  title run — i.e. `top_left`, per `_build_header_zone`'s current emission.

**Does the vision-side (pixel) evidence agree?**

| Norm (business_dashboard stratum, n=36 unless noted) | Value | Confidence |
|---|---|---|
| `title_position` mode | `top_left` — 86.1% (31/36) | `ok` |
| `title_relative_size` mode | `modest` (4-8% of canvas height) — 55.6% (20/36); `minimal` (<4%) 27.8%; `prominent` (8-15%) 16.7%; **`dominant` (>15%) — 0%** | `ok` |

**Yes, both fixes agree with the vision evidence, independently:**

1. **Position** — `top_left` is not just the modal bucket, it alone clears the CI gate's >=50%
   cumulative-coverage bar (86.1% > 50%) with room to spare. The builder's `top_left` header
   placement is squarely the real-world norm, not an outlier choice.
2. **Size** — the vision rubric's `modest` band (4-8% canvas height) is defined to bracket exactly
   the mined `title_height_ratio` fix's value: `0.0696` (6.96%) sits inside `modest`'s 4-8% range.
   More strikingly, **zero of the 36 business-dashboard images were rated `dominant` (>15%)** —
   the two independent evidence sources (XML-mined pixel-ratio and human-eye relative-size
   judgment) converge on the same conclusion from two different angles: a business dashboard's
   title reads as a *label for the content*, not the content's own hero element. The
   beauty-gate's original "the D5 9722-unit header is too big" complaint (occupying ~9.7% of
   canvas — squarely inside the `prominent` 8-15% band, i.e. bordering on hero-sized by the vision
   rubric's own thresholds) is corroborated, not merely assumed fixed, by this independent
   pixel-review sample: the T2 fix's 6.96% pulls the header from `prominent` down into `modest`,
   the vision-observed norm.

No revision to the T2 fix is indicated by this cross-check — it is confirmed, not contradicted, by
a second, methodologically-independent evidence source.

## VOTD-skew statement (read before generalizing any number in this file)

**`business_dashboard` is only 36.4% of the reviewed VOTD corpus** (`category_distribution`,
n=99) — `data_journalism` is the plurality category at 41.4%. Tableau Public's "Viz of the Day"
gallery is curated for visual/storytelling impact across ALL of Tableau Public, not specifically
for exec-facing business dashboards; this codebase's builder only emits `business_dashboard`-shaped
output, so **every norm in this file is deliberately scoped to the `business_dashboard` stratum
alone** (n=36) — the `all_images` stratum (n=99, dominated by journalism/art/personal pieces) is
provided in `visual_norms.yaml` for context only and must never be read as "what a business
dashboard should look like." A future corpus refresh that specifically curates or filters for a
business-dashboard-labeled gallery (rather than reusing the general VOTD feed) would raise this
stratum's `n` and, more importantly, remove the systematic selection bias of "the pieces that
happened to also be visually impressive enough for a general-audience VOTD feed" — the current
n=36 sample is real and useful (well above the `n>=15` confidence floor) but is not a
purpose-built business-dashboard gallery sample.
