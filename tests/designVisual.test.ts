/**
 * Tests for the design-excellence V-phase visual review layer:
 *   - `design/corpus/reviews/visual_reviews.json` (raw resolved vision-agent reviews)
 *   - `design/corpus/stats/visual_norms.yaml` (deterministic aggregation, produced by
 *     `scripts/aggregate-visual-norms.ts`)
 *
 * Both files are produced by a separate (vision-review) workflow slice running concurrently
 * with this one. **Until that data lands, every check in this file SKIPS** (with a console
 * warning, never a failure) via `describe.skipIf` — this suite activates automatically the
 * moment both files exist on disk, no code change required.
 *
 * Implementation note: `describe.skipIf`'s factory callback is still INVOKED during test
 * collection even when the condition is true (only `it`/`beforeAll` callback BODIES are
 * deferred) — so all filesystem access happens at module scope, guarded by a plain `if
 * (dataAvailable)`, never directly inside a `describe(...)` factory body. The 3 application
 * gates (which need their `it.skipIf` condition synchronously, at collection time) read from a
 * module-level `businessStratum` that safely falls back to an all-`confidence: "low"` stub
 * (which skips every gate) when the data files don't exist, so collection never throws.
 *
 * Covers (per the V-phase plan's "Tests" section):
 *  1. Both files zod-validate against `scripts/aggregate-visual-norms.ts`'s schemas (the single
 *     source of truth for both the producer CLI and this test — no schema duplication).
 *  2. Every review's `repoUrl` (an opaque, stable `WB-NNN` identifier — see
 *     `design/corpus/SCHEMA.md`'s provenance section) is a real top-100 manifest entry.
 *  3. Every review carries the full provenance triad (repoUrl, imageSha256, workbookSha256).
 *     `imageUrl` was dropped in the anonymization pass (identifying tableau.com link).
 *  4. n/confidence discipline: n<15 => confidence "low"; every rate is in [0,1]; single-label
 *     categorical bucket counts sum to their distribution's n (multi-label
 *     `chart_type_prevalence` is exempt by design — see its own docstring).
 *  5. REGENERATION EQUALITY: `aggregateVisualNorms()` run in-process against the committed
 *     reviews JSON deep-equals the committed `visual_norms.yaml`.
 *  6. No-hex-literal boundary guard: neither file contains a `#rrggbb`-shaped literal (the
 *     corpus's "observations, not literals" discipline — see SCHEMA.md).
 *  7. The 3 auto-applied stats-reading CI gates (each individually `it.skipIf`'d when its
 *     stratum bucket is `confidence: low`, mirroring the T2 Python gate precedent in
 *     `sidecar/tests/test_design_norms_gates.py`).
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { describe, it, expect } from "vitest";

import {
  VisualReviewsFileSchema,
  VisualNormsSchema,
  aggregateVisualNorms,
  type VisualReviewsFile,
  type VisualNorms,
  type Stratum,
  type CategoricalDistribution,
  type NumericDistribution,
  type RateBucket,
} from "../scripts/aggregate-visual-norms.js";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const REVIEWS_PATH = join(REPO_ROOT, "design", "corpus", "reviews", "visual_reviews.json");
const NORMS_PATH = join(REPO_ROOT, "design", "corpus", "stats", "visual_norms.yaml");
const MANIFEST_PATH = join(REPO_ROOT, "design", "references", "top100", "manifest.yaml");

const reviewsExist = existsSync(REVIEWS_PATH);
const normsExist = existsSync(NORMS_PATH);
const dataAvailable = reviewsExist && normsExist;

if (!dataAvailable) {
  console.warn(
    "[designVisual.test.ts] design/corpus/reviews/visual_reviews.json and/or " +
      "design/corpus/stats/visual_norms.yaml not found yet -- skipping the V-phase visual " +
      "review test suite. This is expected until the vision-review workflow slice lands its " +
      "data; the suite activates automatically once both files exist.",
  );
}

// ---------------------------------------------------------------------------
// Module-scope data load (guarded by a plain `if`, NEVER inside a describe
// factory body — see the module docstring's Implementation note).
// ---------------------------------------------------------------------------

let loadedReviewsRaw: unknown = null;
let loadedNormsRaw: unknown = null;
let loadedNormsRawText = "";
let loadedReviews: VisualReviewsFile | null = null;
let loadedNorms: VisualNorms | null = null;
let manifestRepoUrls = new Set<string>();

if (dataAvailable) {
  const reviewsRawText = readFileSync(REVIEWS_PATH, "utf8");
  loadedNormsRawText = readFileSync(NORMS_PATH, "utf8");
  loadedReviewsRaw = JSON.parse(reviewsRawText);
  loadedNormsRaw = parseYaml(loadedNormsRawText);
  loadedReviews = VisualReviewsFileSchema.parse(loadedReviewsRaw);
  loadedNorms = VisualNormsSchema.parse(loadedNormsRaw);

  const manifestRaw = parseYaml(readFileSync(MANIFEST_PATH, "utf8")) as {
    items: { repoUrl: string }[];
  };
  manifestRepoUrls = new Set(manifestRaw.items.map((item) => item.repoUrl));
}

// Fallback stub (all buckets confidence: "low") so the 3 application gates' `it.skipIf`
// conditions can be evaluated synchronously at collection time without ever throwing when the
// data files don't exist yet — every gate simply skips against this stub.
const FALLBACK_CATEGORICAL: CategoricalDistribution = { n: 0, confidence: "low", buckets: {}, citations: [] };
const FALLBACK_NUMERIC: NumericDistribution = {
  n: 0,
  confidence: "low",
  median: null,
  p25: null,
  p75: null,
  citations: [],
};
const FALLBACK_RATE: RateBucket = { n: 0, confidence: "low", count: 0, total: 0, rate: null };
const FALLBACK_STRATUM: Stratum = {
  n: 0,
  confidence: "low",
  layout_archetype: FALLBACK_CATEGORICAL,
  sidebar_panel_usage_rate: FALLBACK_RATE,
  ban_usage_rate: FALLBACK_RATE,
  ban_placement: FALLBACK_CATEGORICAL,
  ban_count: FALLBACK_NUMERIC,
  title_position: FALLBACK_CATEGORICAL,
  title_relative_size: FALLBACK_CATEGORICAL,
  title_case: FALLBACK_CATEGORICAL,
  title_style: FALLBACK_CATEGORICAL,
  background_tone: FALLBACK_CATEGORICAL,
  palette_mood: FALLBACK_CATEGORICAL,
  accent_count: FALLBACK_NUMERIC,
  density: FALLBACK_CATEGORICAL,
  whitespace: FALLBACK_CATEGORICAL,
  chart_type_prevalence: FALLBACK_CATEGORICAL,
  chart_count_visible: FALLBACK_NUMERIC,
};

const businessStratum: Stratum = loadedNorms?.strata.business_dashboard ?? FALLBACK_STRATUM;

// ---------------------------------------------------------------------------
// No-hex-literal boundary guard
// ---------------------------------------------------------------------------

const HEX_LITERAL_RE = /#[0-9a-fA-F]{3,8}\b/;

/**
 * `visual_reviews.json`'s STRUCTURED fields never contain a hex literal by schema construction
 * (palette/background/etc. are closed enums, never free color text) — the only fields that
 * accept free-form English prose are `category_rationale`, `lesson`, and
 * `standout_techniques[].note` (plus `runMeta.failures[].reason`), and a hex-shaped substring
 * could in principle appear there by pure coincidence (e.g. "workbook #123abc" — never expected
 * in practice, but not schema-impossible). Per the plan's "guard on the structured fields or
 * strip known-safe metadata fields and document" instruction, this guard blanks those specific
 * known-free-text fields before scanning, so it only ever flags a REAL structured-field leak.
 */
function stripKnownFreeTextFields(raw: unknown): unknown {
  const data = JSON.parse(JSON.stringify(raw)) as {
    runMeta?: { failures?: { reason?: string }[] };
    reviews?: Record<string, unknown>[];
  };
  for (const failure of data.runMeta?.failures ?? []) {
    if (failure) failure.reason = "";
  }
  for (const review of data.reviews ?? []) {
    if (typeof review.category_rationale === "string") review.category_rationale = "";
    if (typeof review.lesson === "string") review.lesson = "";
    if (Array.isArray(review.standout_techniques)) {
      review.standout_techniques = (review.standout_techniques as { note?: string }[]).map(
        (t) => ({ ...t, note: "" }),
      );
    }
  }
  return data;
}

// ---------------------------------------------------------------------------
// n/confidence discipline helpers — walk the norms tree collecting every
// {n, confidence} bucket and every rate value, regardless of nesting depth.
// ---------------------------------------------------------------------------

interface ConfidenceBucket {
  n: number;
  confidence: "ok" | "low";
}

function collectConfidenceBuckets(node: unknown, out: ConfidenceBucket[] = []): ConfidenceBucket[] {
  if (node === null || typeof node !== "object") return out;
  const obj = node as Record<string, unknown>;
  if (
    typeof obj.n === "number" &&
    (obj.confidence === "ok" || obj.confidence === "low") &&
    Object.keys(obj).length > 0
  ) {
    out.push({ n: obj.n, confidence: obj.confidence });
  }
  for (const value of Object.values(obj)) {
    if (value !== null && typeof value === "object") collectConfidenceBuckets(value, out);
  }
  return out;
}

function collectRates(node: unknown, out: number[] = []): number[] {
  if (node === null || typeof node !== "object") return out;
  const obj = node as Record<string, unknown>;
  if (typeof obj.rate === "number") out.push(obj.rate);
  for (const value of Object.values(obj)) {
    if (value !== null && typeof value === "object") collectRates(value, out);
  }
  return out;
}

/** Single-label categorical distributions whose bucket counts must sum to `n` exactly.
 * `chart_type_prevalence` is deliberately excluded (multi-label; a record may contribute to
 * zero, one, or several buckets, so its bucket counts need NOT sum to its `n`). */
const SINGLE_LABEL_DISTRIBUTION_PATHS: readonly (readonly string[])[] = [
  ["category_distribution"],
  ["strata", "business_dashboard", "layout_archetype"],
  ["strata", "business_dashboard", "ban_placement"],
  ["strata", "business_dashboard", "title_position"],
  ["strata", "business_dashboard", "title_relative_size"],
  ["strata", "business_dashboard", "title_case"],
  ["strata", "business_dashboard", "title_style"],
  ["strata", "business_dashboard", "background_tone"],
  ["strata", "business_dashboard", "palette_mood"],
  ["strata", "business_dashboard", "density"],
  ["strata", "business_dashboard", "whitespace"],
  ["strata", "all_images", "layout_archetype"],
  ["strata", "all_images", "ban_placement"],
  ["strata", "all_images", "title_position"],
  ["strata", "all_images", "title_relative_size"],
  ["strata", "all_images", "title_case"],
  ["strata", "all_images", "title_style"],
  ["strata", "all_images", "background_tone"],
  ["strata", "all_images", "palette_mood"],
  ["strata", "all_images", "density"],
  ["strata", "all_images", "whitespace"],
];

function getAtPath(obj: unknown, path: readonly string[]): CategoricalDistribution {
  let cursor: unknown = obj;
  for (const key of path) {
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return cursor as CategoricalDistribution;
}

// ---------------------------------------------------------------------------
// Suite (skips entirely until both files land)
// ---------------------------------------------------------------------------

describe.skipIf(!dataAvailable)(
  "design/corpus/reviews/visual_reviews.json + design/corpus/stats/visual_norms.yaml",
  () => {
    // Safe casts: this describe's `it` callback BODIES only ever run when `dataAvailable` is
    // true (the describe factory body itself, above, never dereferences these), so by the time
    // any callback below actually executes, `loadedReviews`/`loadedNorms` are guaranteed
    // populated. See the module docstring's Implementation note.
    const reviewsRaw = loadedReviewsRaw;
    const normsRaw = loadedNormsRaw;
    const normsRawText = loadedNormsRawText;
    const reviews = loadedReviews as VisualReviewsFile;
    const norms = loadedNorms as VisualNorms;

    // -------------------------------------------------------------------
    // 1 — zod schema validation
    // -------------------------------------------------------------------

    it("visual_reviews.json zod-validates against VisualReviewsFileSchema", () => {
      const result = VisualReviewsFileSchema.safeParse(reviewsRaw);
      if (!result.success) {
        throw new Error(
          `visual_reviews.json failed schema validation: ` +
            JSON.stringify(result.error.issues.slice(0, 5), null, 2),
        );
      }
    });

    it("visual_norms.yaml zod-validates against VisualNormsSchema", () => {
      const result = VisualNormsSchema.safeParse(normsRaw);
      if (!result.success) {
        throw new Error(
          `visual_norms.yaml failed schema validation: ` +
            JSON.stringify(result.error.issues.slice(0, 5), null, 2),
        );
      }
    });

    // -------------------------------------------------------------------
    // 2 & 3 — manifest membership + provenance quad
    // -------------------------------------------------------------------

    it("every review's repoUrl (WB-id) is a real design/references/top100/manifest.yaml entry", () => {
      for (const review of reviews.reviews) {
        expect(
          manifestRepoUrls.has(review.repoUrl),
          `repoUrl "${review.repoUrl}" not found in manifest.yaml`,
        ).toBe(true);
      }
    });

    it("every review carries the full provenance triad (repoUrl, imageSha256, workbookSha256)", () => {
      for (const review of reviews.reviews) {
        expect(review.repoUrl.length).toBeGreaterThan(0);
        expect(review.imageSha256).toMatch(/^[0-9a-f]{64}$/);
        expect(review.workbookSha256).toMatch(/^[0-9a-f]{64}$/);
      }
    });

    // -------------------------------------------------------------------
    // 4 — n/confidence discipline
    // -------------------------------------------------------------------

    it("n<15 => confidence low, n>=15 => confidence ok, for every bucket in visual_norms.yaml", () => {
      const buckets = collectConfidenceBuckets(norms);
      expect(buckets.length).toBeGreaterThan(0);
      for (const bucket of buckets) {
        expect(bucket.confidence).toBe(bucket.n >= 15 ? "ok" : "low");
      }
    });

    it("every rate value in visual_norms.yaml is within [0, 1]", () => {
      const rates = collectRates(norms);
      expect(rates.length).toBeGreaterThan(0);
      for (const rate of rates) {
        expect(rate).toBeGreaterThanOrEqual(0);
        expect(rate).toBeLessThanOrEqual(1);
      }
    });

    it("single-label categorical distribution bucket counts sum to their n", () => {
      for (const path of SINGLE_LABEL_DISTRIBUTION_PATHS) {
        const dist = getAtPath(norms, path);
        const total = Object.values(dist.buckets).reduce((sum, b) => sum + b.count, 0);
        expect(total, `${path.join(".")}: bucket counts do not sum to n`).toBe(dist.n);
      }
    });

    // -------------------------------------------------------------------
    // 5 — REGENERATION EQUALITY
    // -------------------------------------------------------------------

    it("REGENERATION EQUALITY: aggregateVisualNorms(reviews) deep-equals the committed visual_norms.yaml", () => {
      const regenerated = aggregateVisualNorms(reviews);
      expect(regenerated).toEqual(norms);
    });

    // -------------------------------------------------------------------
    // 6 — no-hex-literal boundary guard
    // -------------------------------------------------------------------

    it("visual_reviews.json's structured fields contain no hex color literal", () => {
      const stripped = stripKnownFreeTextFields(reviewsRaw);
      expect(HEX_LITERAL_RE.test(JSON.stringify(stripped))).toBe(false);
    });

    it("visual_norms.yaml contains no hex color literal (aggregated buckets only, no free text)", () => {
      expect(HEX_LITERAL_RE.test(normsRawText)).toBe(false);
    });

    // -------------------------------------------------------------------
    // 7 — the 3 auto-applied stats-reading CI gates
    // -------------------------------------------------------------------

    describe("application gates (T2 precedent — each skips unless its stratum bucket is confidence: ok)", () => {
      it.skipIf(businessStratum.layout_archetype.confidence !== "ok")(
        "gate (a): kpi_band_top + grid_of_charts jointly cover >=40% of the business stratum's layout archetypes",
        () => {
          const buckets = businessStratum.layout_archetype.buckets;
          const combinedRate = (buckets.kpi_band_top?.rate ?? 0) + (buckets.grid_of_charts?.rate ?? 0);
          expect(combinedRate).toBeGreaterThanOrEqual(0.4);
        },
      );

      it.skipIf(businessStratum.ban_placement.confidence !== "ok")(
        "gate (b): the modal business-stratum BAN placement is top_band (validates kpi_band_over_charts)",
        () => {
          const entries = Object.entries(businessStratum.ban_placement.buckets);
          const modal = entries.reduce((best, cur) => (cur[1].rate > best[1].rate ? cur : best));
          expect(modal[0]).toBe("top_band");
        },
      );

      it.skipIf(businessStratum.title_position.confidence !== "ok")(
        "gate (c): the builder's title position (top_left) is within the positions covering >=50% cumulative rate",
        () => {
          const sorted = Object.entries(businessStratum.title_position.buckets).sort(
            (a, b) => b[1].rate - a[1].rate,
          );
          let cumulative = 0;
          const covered: string[] = [];
          for (const [position, bucket] of sorted) {
            cumulative += bucket.rate;
            covered.push(position);
            if (cumulative >= 0.5) break;
          }
          expect(covered).toContain("top_left");
        },
      );
    });
  },
);
