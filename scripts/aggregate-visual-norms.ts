/**
 * Design Excellence, Slice V (PLAN.md's "V-phase" — parallel visual design review of the
 * top-100 VOTD snapshots) — offline CLI: aggregate the vision-agent review corpus into
 * deterministic visual-design-norm statistics.
 *
 * Mirrors `sidecar/design_stats.py`'s aggregation semantics EXACTLY (same `MIN_CONFIDENT_N`
 * floor, same numerator-gated conditional-bucket confidence, same 5-citation cap, same
 * stable-sorted output, same "no wall-clock timestamp" determinism discipline) but operates
 * over `design/corpus/reviews/visual_reviews.json` (produced by a separate vision-review
 * workflow slice — see that file's own schema, `VisualReviewsFileSchema` below) instead of
 * mined `.twb`/`.twbx` XML.
 *
 * Usage
 * -----
 * ::
 *
 *     npx tsx scripts/aggregate-visual-norms.ts \
 *       [--reviews design/corpus/reviews/visual_reviews.json] \
 *       [--out design/corpus/stats/visual_norms.yaml]
 *
 * Both flags default to the committed paths (repo-root-relative), matching
 * `scripts/fetch-top100.ts`'s zero-required-arg CLI precedent.
 *
 * Determinism
 * -----------
 * `aggregateVisualNorms` is a **pure function** (no filesystem, no wall-clock reads) so that
 * `tests/designVisual.test.ts`'s "regeneration equality" check can import it directly, run it
 * in-process against the committed reviews JSON, and deep-equal the result against the
 * committed `visual_norms.yaml` — the same "aggregate twice -> byte-identical output" contract
 * `sidecar/design_stats.py` documents for the T1 stats files, just enforced by a TS unit test
 * instead of a Python one. The `main()` CLI wrapper is the only I/O-performing code in this
 * file (reads the reviews JSON, validates it, writes the YAML) and only runs when this module
 * is executed directly, never on `import` (so the test suite can import the pure pieces without
 * triggering a file read that would fail before the data lands).
 *
 * Dispute semantics (mirrors the V-phase plan's "Reliability" section)
 * ----------------------------------------------------------------------
 * The reviews JSON is the ALREADY-RESOLVED output of the review workflow's deterministic
 * dispute rules — this aggregator does no dispute resolution of its own:
 *   - A `category` disagreement across passes resolves to `disputed: true` on the record (its
 *     best-effort `category` value is kept for reference, but the record is EXCLUDED from the
 *     `business_dashboard` stratum and counted in its own `disputed` bucket in
 *     `category_distribution`).
 *   - A field-level disagreement or per-agent extraction failure resolves to `null` for that
 *     one field only ("catch -> null -> filter") — this aggregator drops such records from just
 *     that field's own `n`, never from the record's other fields or from the stratum's overall
 *     `n`.
 *   - `quality_flags.image_unreadable === true` excludes the record ENTIRELY (every bucket,
 *     every stratum, `category_distribution` included) — an unreadable image contributes no
 *     signal at all, not even to its own classification.
 *   - `quality_flags.truncated_long_scroll === true` excludes the record from `layout_archetype`
 *     ONLY (a partially-captured long-scroll infographic cannot be reliably classified by
 *     layout shape, but its other fields — palette, density, BAN usage, etc. — are still real
 *     observations).
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { stringify as stringifyYaml } from "yaml";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(MODULE_DIR, "..");
const DEFAULT_REVIEWS_PATH = resolve(REPO_ROOT, "design/corpus/reviews/visual_reviews.json");
const DEFAULT_OUT_PATH = resolve(REPO_ROOT, "design/corpus/stats/visual_norms.yaml");

/** Same floor as `sidecar/design_stats.py`'s `MIN_CONFIDENT_N` — never re-derive independently. */
const MIN_CONFIDENT_N = 15;

/** Same cap as `sidecar/design_stats.py`'s `citations()` — top-5 exemplar citations, one per image. */
const CITATION_LIMIT = 5;

const SHA256_RE = /^[0-9a-f]{64}$/;
const REPO_URL_RE = /^[A-Za-z0-9._-]+$/;
/** Rubric tag vocabulary (`chart_types`, `standout_techniques[].tag`) is open-ended but must be
 * a lowercase snake_case token — never a hex/pixel literal, never free English prose. */
const SNAKE_TAG_RE = /^[a-z][a-z0-9_]*$/;

// ---------------------------------------------------------------------------
// design/corpus/reviews/visual_reviews.json schema
// ---------------------------------------------------------------------------
//
// Per-image rubric vocabulary (field names + enum options) mirrors the V-phase plan's "Per-image
// rubric" section verbatim where the plan names a field/option explicitly; where the plan leaves
// an enum's exact option set unspecified (`palette_mood` "(6 enums)", `density`/`whitespace`,
// `title.case`/`title.style`, `bans.placement`/`bans.style`), this schema fixes a concrete,
// documented vocabulary (see `design/corpus/SCHEMA.md`'s "Visual layer" section) — every
// downstream vision-review record must conform to it exactly, no free text in these fields.

export const CategoryEnum = z.enum([
  "business_dashboard",
  "data_journalism",
  "personal_infographic",
  "data_art",
]);

export const LayoutArchetypeEnum = z.enum([
  "kpi_band_top",
  "grid_of_charts",
  "hero_chart_supporting",
  "single_viz",
  "small_multiples",
  "long_scroll_infographic",
  "map_centric",
]);

export const PaletteMoodEnum = z.enum([
  "monochrome",
  "single_hue_sequential",
  "muted_plus_one_accent",
  "two_tone",
  "categorical_multi",
  "full_color_illustrative",
]);

export const TitlePositionEnum = z.enum([
  "top_left",
  "top_center",
  "top_right",
  "overlay_on_viz",
  "none",
]);

const CategoryConfidenceEnum = z.enum(["high", "medium", "low"]);
const BackgroundToneEnum = z.enum(["light", "dark", "mid", "image"]);
const BackgroundTintEnum = z.enum(["neutral", "tinted", "saturated"]);
const DensityEnum = z.enum(["minimal", "moderate", "dense"]);
const WhitespaceEnum = z.enum(["generous", "balanced", "tight"]);
const TitleRelativeSizeEnum = z.enum(["dominant", "prominent", "modest", "minimal"]);
const TitleCaseEnum = z.enum(["all_caps", "title_case", "sentence_case", "mixed"]);
const TitleStyleEnum = z.enum(["plain_sans", "serif_display", "condensed_bold", "script_decorative"]);
/**
 * `"none"` is a REAL, expected value (every `bans.present === false` record carries
 * `placement: "none"`, never `null` — verified against the data) rather than a schema escape
 * hatch. The aggregator does not need to special-case it: `ban_placement`'s distribution is
 * already computed over `banPresentRecords()` (the `bans.present === true` numerator, per the
 * module docstring's "Conditional buckets" note) — no `present === false` record, and therefore
 * no `"none"` placement, ever reaches that distribution in practice. Keeping `"none"` in the
 * enum (rather than mapping it to `null`) keeps the schema an honest mirror of the data.
 */
export const BanPlacementEnum = z.enum([
  "top_band",
  "left_column",
  "right_column",
  "bottom_band",
  "scattered_inline",
  "none",
]);
const BanStyleEnum = z.enum(["boxed_tiles", "open_numbers", "none"]);

const QualityFlagsSchema = z.object({
  image_unreadable: z.boolean(),
  truncated_long_scroll: z.boolean(),
  marketing_frame_chrome: z.boolean(),
});

const BackgroundSchema = z.object({
  tone: BackgroundToneEnum,
  tint: BackgroundTintEnum,
});

const TitleSchema = z.object({
  position: TitlePositionEnum,
  relative_size: TitleRelativeSizeEnum,
  case: TitleCaseEnum,
  style: TitleStyleEnum,
});

const BansSchema = z.object({
  present: z.boolean(),
  count: z.number().int().min(0).nullable(),
  placement: BanPlacementEnum.nullable(),
  style: BanStyleEnum.nullable(),
});

const StandoutTechniqueSchema = z.object({
  tag: z.string().regex(SNAKE_TAG_RE),
  note: z.string().min(1).max(200),
});

/** Provenance quad — orchestrator-injected, never agent-guessed (V-phase plan mandate). */
export const ProvenanceQuadSchema = z.object({
  repoUrl: z.string().regex(REPO_URL_RE),
  imageUrl: z.string().url(),
  imageSha256: z.string().regex(SHA256_RE),
  workbookSha256: z.string().regex(SHA256_RE),
});
export type ProvenanceQuad = z.infer<typeof ProvenanceQuadSchema>;

export const ReviewRecordSchema = ProvenanceQuadSchema.extend({
  imageWidth: z.number().int().positive(),
  imageHeight: z.number().int().positive(),

  category: CategoryEnum,
  category_confidence: CategoryConfidenceEnum,
  category_rationale: z.string().min(1).max(140),
  /** Category disagreement across the full pass + classification-only verify pass. */
  disputed: z.boolean(),

  quality_flags: QualityFlagsSchema,

  layout_archetype: LayoutArchetypeEnum.nullable(),
  sidebar_panel: z.boolean().nullable(),
  background: BackgroundSchema.nullable(),
  palette_mood: PaletteMoodEnum.nullable(),
  accent_count: z.number().int().min(0).nullable(),
  density: DensityEnum.nullable(),
  whitespace: WhitespaceEnum.nullable(),
  title: TitleSchema.nullable(),
  bans: BansSchema.nullable(),
  chart_types: z.array(z.string().regex(SNAKE_TAG_RE)).max(5),
  chart_count_visible: z.number().int().min(0).nullable(),
  standout_techniques: z.array(StandoutTechniqueSchema).max(3),
  lesson: z.string().min(1).max(280).nullable(),
});
export type ReviewRecord = z.infer<typeof ReviewRecordSchema>;

const RunFailureSchema = z.object({
  repoUrl: z.string().regex(REPO_URL_RE),
  reason: z.string().min(1),
});

export const RunMetaSchema = z.object({
  model: z.string().min(1),
  /** ISO-8601 run date — provenance, not a "generated at" field (this aggregator never writes
   * its own wall-clock timestamp; see the module docstring's Determinism section). */
  date: z.string().min(1),
  /** Number of review passes (>=1): the full pass, plus the classification-only verify pass on
   * (business_dashboard union category_confidence=low), per the V-phase plan's "Reliability". */
  passes: z.number().int().min(1),
  failures: z.array(RunFailureSchema),
});
export type RunMeta = z.infer<typeof RunMetaSchema>;

export const VisualReviewsFileSchema = z.object({
  runMeta: RunMetaSchema,
  reviews: z.array(ReviewRecordSchema).min(1),
});
export type VisualReviewsFile = z.infer<typeof VisualReviewsFileSchema>;

// ---------------------------------------------------------------------------
// design/corpus/stats/visual_norms.yaml schema (output)
// ---------------------------------------------------------------------------

const ConfidenceEnum = z.enum(["ok", "low"]);

const CategoricalBucketSchema = z.object({
  count: z.number().int().min(0),
  rate: z.number().min(0),
});

const CategoricalDistributionSchema = z.object({
  n: z.number().int().min(0),
  confidence: ConfidenceEnum,
  buckets: z.record(z.string(), CategoricalBucketSchema),
  citations: z.array(ProvenanceQuadSchema).max(CITATION_LIMIT),
});
export type CategoricalDistribution = z.infer<typeof CategoricalDistributionSchema>;

const NumericDistributionSchema = z.object({
  n: z.number().int().min(0),
  confidence: ConfidenceEnum,
  median: z.number().nullable(),
  p25: z.number().nullable(),
  p75: z.number().nullable(),
  citations: z.array(ProvenanceQuadSchema).max(CITATION_LIMIT),
});
export type NumericDistribution = z.infer<typeof NumericDistributionSchema>;

const RateBucketSchema = z.object({
  n: z.number().int().min(0),
  confidence: ConfidenceEnum,
  count: z.number().int().min(0),
  total: z.number().int().min(0),
  rate: z.number().min(0).max(1).nullable(),
});
export type RateBucket = z.infer<typeof RateBucketSchema>;

const StratumSchema = z.object({
  n: z.number().int().min(0),
  confidence: ConfidenceEnum,
  layout_archetype: CategoricalDistributionSchema,
  sidebar_panel_usage_rate: RateBucketSchema,
  ban_usage_rate: RateBucketSchema,
  ban_placement: CategoricalDistributionSchema,
  ban_count: NumericDistributionSchema,
  title_position: CategoricalDistributionSchema,
  title_relative_size: CategoricalDistributionSchema,
  title_case: CategoricalDistributionSchema,
  title_style: CategoricalDistributionSchema,
  background_tone: CategoricalDistributionSchema,
  palette_mood: CategoricalDistributionSchema,
  accent_count: NumericDistributionSchema,
  density: CategoricalDistributionSchema,
  whitespace: CategoricalDistributionSchema,
  chart_type_prevalence: CategoricalDistributionSchema,
  chart_count_visible: NumericDistributionSchema,
});
export type Stratum = z.infer<typeof StratumSchema>;

export const VisualNormsSchema = z.object({
  corpus_images: z.number().int().min(0),
  n_reviewed: z.number().int().min(0),
  method: z.object({
    model: z.string().min(1),
    date: z.string().min(1),
    passes: z.number().int().min(1),
    /** Always `true`: disputed (category-uncertain) records never enter a stratum's norms. */
    disputed_excluded_from_strata: z.literal(true),
  }),
  category_distribution: CategoricalDistributionSchema,
  strata: z.object({
    business_dashboard: StratumSchema,
    all_images: StratumSchema,
  }),
});
export type VisualNorms = z.infer<typeof VisualNormsSchema>;

// ---------------------------------------------------------------------------
// Small stats primitives (mirrors sidecar/design_stats.py's primitives 1:1)
// ---------------------------------------------------------------------------

/** `"ok"` when `n` meets the plan's minimum-sample floor, else `"low"`. */
function confidence(n: number): "ok" | "low" {
  return n >= MIN_CONFIDENT_N ? "ok" : "low";
}

function round4(x: number): number {
  return Math.round(x * 10_000) / 10_000;
}

/** Linear-interpolation percentile (the common "linear" method) — mirrors `design_stats.py`'s
 * `percentile()`. `values` must be non-empty; callers only invoke this after an `n > 0` check. */
function percentile(values: readonly number[], pct: number): number {
  const ordered = [...values].sort((a, b) => a - b);
  const n = ordered.length;
  const first = ordered[0];
  if (first === undefined) {
    throw new Error("percentile: values must be non-empty");
  }
  if (n === 1) return first;
  const rank = (pct / 100) * (n - 1);
  const lo = Math.floor(rank);
  const hi = Math.min(lo + 1, n - 1);
  const frac = rank - lo;
  const loVal = ordered[lo];
  const hiVal = ordered[hi];
  if (loVal === undefined || hiVal === undefined) {
    throw new Error("percentile: computed index out of range");
  }
  return loVal + (hiVal - loVal) * frac;
}

/** `{n, confidence, median, p25, p75}` over `values` (n=0 -> all quantiles `null`). */
function numericDistribution(values: readonly number[]): Omit<NumericDistribution, "citations"> {
  const n = values.length;
  if (n === 0) {
    return { n: 0, confidence: "low", median: null, p25: null, p75: null };
  }
  return {
    n,
    confidence: confidence(n),
    median: round4(percentile(values, 50)),
    p25: round4(percentile(values, 25)),
    p75: round4(percentile(values, 75)),
  };
}

/** `{n, confidence, count, total, rate}` — a usage-rate bucket over `total` samples. No
 * `citations` field, mirroring `design_stats.py`'s `rate()` (usage-rate buckets in the T1 stats
 * precedent never carry citations; only `distribution()`-shaped buckets do). */
function rateBucket(count: number, total: number): RateBucket {
  return {
    n: total,
    confidence: confidence(total),
    count,
    total,
    rate: total > 0 ? round4(count / total) : null,
  };
}

function bucketsFromCounts(
  counts: ReadonlyMap<string, number>,
  n: number,
): Record<string, { count: number; rate: number }> {
  const entries = [...counts.entries()].sort(([a], [b]) => a.localeCompare(b));
  const buckets: Record<string, { count: number; rate: number }> = {};
  for (const [value, count] of entries) {
    buckets[value] = { count, rate: n > 0 ? round4(count / n) : 0 };
  }
  return buckets;
}

/** Single-label categorical distribution: `n` = number of non-null observations, one label per
 * observation, bucket rates sum to 1 (mod rounding). */
function singleLabelDistribution(
  values: readonly string[],
): Omit<CategoricalDistribution, "citations"> {
  const n = values.length;
  const counts = new Map<string, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  return { n, confidence: confidence(n), buckets: bucketsFromCounts(counts, n) };
}

/** Multi-label categorical distribution (e.g. `chart_type_prevalence`): `n` = total records
 * considered (the fixed denominator), each record may contribute to zero or more buckets, so
 * bucket rates need NOT sum to 1. */
function multiLabelDistribution(
  valueLists: readonly (readonly string[])[],
): Omit<CategoricalDistribution, "citations"> {
  const n = valueLists.length;
  const counts = new Map<string, number>();
  for (const list of valueLists) {
    for (const v of list) counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  return { n, confidence: confidence(n), buckets: bucketsFromCounts(counts, n) };
}

/** Up to `CITATION_LIMIT` provenance citations, one per DISTINCT `repoUrl`, sorted by `repoUrl`
 * — "top-5 exemplar citations", mirroring `design_stats.py`'s `citations()`. */
function citations(records: readonly ReviewRecord[]): ProvenanceQuad[] {
  const sorted = [...records].sort((a, b) => a.repoUrl.localeCompare(b.repoUrl));
  const seen = new Set<string>();
  const out: ProvenanceQuad[] = [];
  for (const r of sorted) {
    if (seen.has(r.repoUrl)) continue;
    seen.add(r.repoUrl);
    out.push({
      repoUrl: r.repoUrl,
      imageUrl: r.imageUrl,
      imageSha256: r.imageSha256,
      workbookSha256: r.workbookSha256,
    });
    if (out.length >= CITATION_LIMIT) break;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Per-field aggregation helpers
// ---------------------------------------------------------------------------

function numericField(
  records: readonly ReviewRecord[],
  selector: (r: ReviewRecord) => number | null,
): NumericDistribution {
  const withValue = records.filter((r) => selector(r) !== null);
  const dist = numericDistribution(withValue.map((r) => selector(r) as number));
  return { ...dist, citations: citations(withValue) };
}

function categoricalField(
  records: readonly ReviewRecord[],
  selector: (r: ReviewRecord) => string | null,
): CategoricalDistribution {
  const withValue = records.filter((r) => selector(r) !== null);
  const dist = singleLabelDistribution(withValue.map((r) => selector(r) as string));
  return { ...dist, citations: citations(withValue) };
}

function multiLabelField(
  records: readonly ReviewRecord[],
  selector: (r: ReviewRecord) => readonly string[],
): CategoricalDistribution {
  const dist = multiLabelDistribution(records.map((r) => selector(r)));
  return { ...dist, citations: citations(records) };
}

/** `bans.present === true` records — the numerator every ban-conditional bucket gates on. */
function banPresentRecords(records: readonly ReviewRecord[]): ReviewRecord[] {
  return records.filter((r) => r.bans !== null && r.bans.present === true);
}

// ---------------------------------------------------------------------------
// Stratum aggregation
// ---------------------------------------------------------------------------

function aggregateStratum(records: readonly ReviewRecord[]): Stratum {
  const n = records.length;

  // `truncated_long_scroll` is excluded from `layout_archetype`'s n ONLY (see module docstring).
  const layoutEligible = records.filter((r) => !r.quality_flags.truncated_long_scroll);

  const banPresent = banPresentRecords(records);

  return {
    n,
    confidence: confidence(n),
    layout_archetype: categoricalField(layoutEligible, (r) => r.layout_archetype),
    sidebar_panel_usage_rate: rateBucket(
      records.filter((r) => r.sidebar_panel === true).length,
      records.filter((r) => r.sidebar_panel !== null).length,
    ),
    ban_usage_rate: rateBucket(
      banPresent.length,
      records.filter((r) => r.bans !== null).length,
    ),
    // Conditional buckets: gated on the NUMERATOR (bans.present === true), not the stratum n.
    ban_placement: categoricalField(banPresent, (r) => r.bans?.placement ?? null),
    ban_count: numericField(banPresent, (r) => r.bans?.count ?? null),
    title_position: categoricalField(records, (r) => r.title?.position ?? null),
    title_relative_size: categoricalField(records, (r) => r.title?.relative_size ?? null),
    title_case: categoricalField(records, (r) => r.title?.case ?? null),
    title_style: categoricalField(records, (r) => r.title?.style ?? null),
    background_tone: categoricalField(records, (r) => r.background?.tone ?? null),
    palette_mood: categoricalField(records, (r) => r.palette_mood),
    accent_count: numericField(records, (r) => r.accent_count),
    density: categoricalField(records, (r) => r.density),
    whitespace: categoricalField(records, (r) => r.whitespace),
    chart_type_prevalence: multiLabelField(records, (r) => r.chart_types),
    chart_count_visible: numericField(records, (r) => r.chart_count_visible),
  };
}

// ---------------------------------------------------------------------------
// Top-level aggregation (the pure function the regeneration-equality test imports)
// ---------------------------------------------------------------------------

/**
 * Aggregates a validated `VisualReviewsFile` into `visual_norms.yaml`'s body. Pure — no
 * filesystem or wall-clock access (see the module docstring's Determinism section).
 */
export function aggregateVisualNorms(data: VisualReviewsFile): VisualNorms {
  // `image_unreadable` records are excluded ENTIRELY — every bucket, every stratum.
  const readable = data.reviews.filter((r) => !r.quality_flags.image_unreadable);

  const categoryLabels = readable.map((r) => (r.disputed ? "disputed" : r.category));
  const categoryDist = singleLabelDistribution(categoryLabels);

  const businessDashboardRecords = readable.filter(
    (r) => r.category === "business_dashboard" && !r.disputed,
  );

  return {
    // Derived from the arrays themselves (not copied from run metadata) so the output can never
    // drift from what `reviews`/`runMeta.failures` actually contain.
    corpus_images: data.reviews.length + data.runMeta.failures.length,
    n_reviewed: data.reviews.length,
    method: {
      model: data.runMeta.model,
      date: data.runMeta.date,
      passes: data.runMeta.passes,
      disputed_excluded_from_strata: true,
    },
    category_distribution: { ...categoryDist, citations: citations(readable) },
    strata: {
      business_dashboard: aggregateStratum(businessDashboardRecords),
      // Context stratum: every readable image, regardless of category or dispute status — a
      // disputed record's CATEGORY is unreliable, but its other rubric fields (layout, palette,
      // density, ...) are still real observations, so it is NOT excluded here (only from the
      // category-filtered `business_dashboard` stratum above).
      all_images: aggregateStratum(readable),
    },
  };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function argFlag(args: readonly string[], flag: string): string | undefined {
  const idx = args.indexOf(flag);
  if (idx === -1) return undefined;
  return args[idx + 1];
}

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function main(): void {
  const args = process.argv.slice(2);
  const reviewsPath = argFlag(args, "--reviews") ?? DEFAULT_REVIEWS_PATH;
  const outPath = argFlag(args, "--out") ?? DEFAULT_OUT_PATH;

  if (!existsSync(reviewsPath)) {
    console.error(`aggregate-visual-norms: reviews file not found at ${reviewsPath}`);
    process.exit(1);
  }

  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(reviewsPath, "utf8"));
  } catch (err: unknown) {
    console.error(`aggregate-visual-norms: invalid JSON in ${reviewsPath}: ${getErrorMessage(err)}`);
    process.exit(1);
  }

  const parsed = VisualReviewsFileSchema.safeParse(raw);
  if (!parsed.success) {
    console.error(
      `aggregate-visual-norms: ${reviewsPath} failed schema validation:\n` +
        JSON.stringify(parsed.error.issues.slice(0, 10), null, 2),
    );
    process.exit(1);
  }

  const norms = aggregateVisualNorms(parsed.data);

  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, stringifyYaml(norms, { sortMapEntries: true }));

  console.log(`corpus_images: ${norms.corpus_images}`);
  console.log(`n_reviewed: ${norms.n_reviewed}`);
  console.log(
    `strata.business_dashboard: n=${norms.strata.business_dashboard.n} ` +
      `confidence=${norms.strata.business_dashboard.confidence}`,
  );
  console.log(
    `strata.all_images: n=${norms.strata.all_images.n} confidence=${norms.strata.all_images.confidence}`,
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}
