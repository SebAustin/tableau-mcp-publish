/**
 * Planning pipeline: fieldHints → marks → clamp → DashboardPlan.
 *
 * This is the entry point used by `design_dashboard`. It wires the pure
 * sub-functions together and enforces the schemaVersion guard.
 *
 * Phase-1 additions (Slice 4):
 * - For exec audience: emit KPI strip (≤4 tiles) + chart sheets; set
 *   layoutGrammar.kind="kpi_band_over_charts".
 * - Derive dashboardTitle from the business question; dashboardSubtitle from
 *   audience + implied period.
 * - Populate color/geo/scatter on the relevant sheets.
 *
 * Phase E4 addition:
 * - When the resolved persona prefers "story" artifacts OR the business
 *   question uses story/narrative/presentation language, emit a deterministic
 *   storyArc over the finalized sheet list (see buildStoryArc below).
 *
 * Slice T2/T3 (PLAN.md's Top-100 Corpus plan) additions:
 * - Auto-shorten a long question-derived dashboardTitle to a
 *   "{Measure} Performance" headline, promoting the full question to
 *   dashboardSubtitle (see TITLE_AUTO_SHORTEN_THRESHOLD below).
 * - storyArc captions use takeaway-style template v2 (see the Caption
 *   template v2 block below) instead of v1's bare-label captions.
 */

import { classifyFields, usableFields } from "./fields.js";
import type { FieldHint, FieldClassification } from "./fields.js";
import { applyMarkHeuristic, buildKpiStrip, appendSmallMultiplesHintIfApplicable } from "./marks.js";
import { applyAudienceClamps } from "./audience.js";
import type { AudienceConstraintOverrides } from "./audience.js";
import {
  assertSchemaVersion,
  SCHEMA_VERSION,
  type Audience,
  type DashboardPlan,
  type DashboardLayout,
  type SheetSpec,
  type DatasourceSpec,
  type LayoutGrammar,
  type PersonaPreferredArtifact,
  type PersonaTone,
  type StoryArcPoint,
  DashboardPlanSchema,
} from "./schema.js";
import { selectQuestions, type InterviewInput } from "./questions.js";
import type { ClarifyingQuestions } from "./schema.js";

// ---------------------------------------------------------------------------
// Audience notes (BI_DESIGN §3.3)
// ---------------------------------------------------------------------------

const AUDIENCE_NOTES: Record<Audience, string> = {
  exec: "Executive view: maximum 3 sheets, leading KPI, large text. Axis labels are minimal. Annotations on the primary KPI should be added manually in Tableau.",
  analyst:
    "Analyst view: up to 8 sheets, dense layout, full axis labels. Reference lines (average, target) should be added manually post-publish.",
  operational:
    "Operational view: single-column mobile-friendly layout, status marks prominent, action-oriented KPIs. Dashboard is optimized for 800px width.",
  mixed:
    "General audience: balanced 4-6 sheet layout with one summary KPI and standard density.",
};

// ---------------------------------------------------------------------------
// L-01 layout gap: analyst > 4 sheets → tiled_vertical
// ---------------------------------------------------------------------------

const L01_NOTE =
  "Switched to tiled_vertical because more than 4 sheets exceed the tiled_horizontal threshold (L-01).";

function resolveAnalystLayout(
  audience: Audience,
  sheetCount: number,
  layout: DashboardLayout,
): { layout: DashboardLayout; note: string | null } {
  if (audience === "analyst" && sheetCount > 4 && layout === "tiled_horizontal") {
    return { layout: "tiled_vertical", note: L01_NOTE };
  }
  return { layout, note: null };
}

// ---------------------------------------------------------------------------
// Derive workbook name from business question
// ---------------------------------------------------------------------------

function deriveWorkbookName(
  businessQuestion: string,
  audience: Audience,
): string {
  const words = businessQuestion
    .replace(/[^a-zA-Z0-9\s]/g, "")
    .split(/\s+/)
    .filter((w): w is string => w.length > 0)
    .slice(0, 6);
  const base = words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  return base || `${audience.charAt(0).toUpperCase()}${audience.slice(1)} Dashboard`;
}

// ---------------------------------------------------------------------------
// Derive dashboard title from business question
// ---------------------------------------------------------------------------

function deriveDashboardTitle(businessQuestion: string, audience: Audience): string {
  const trimmed = businessQuestion.trim();
  if (!trimmed) {
    const label = audience.charAt(0).toUpperCase() + audience.slice(1);
    return `${label} Dashboard`;
  }
  // Remove question marks, truncate to ~60 chars, title-case
  const clean = trimmed
    .replace(/[?!]+$/, "")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .slice(0, 60)
    .trim();
  return clean;
}

// ---------------------------------------------------------------------------
// Derive dashboard subtitle
// ---------------------------------------------------------------------------

function deriveDashboardSubtitle(audience: Audience): string {
  switch (audience) {
    case "exec":
      return "Executive Summary · Period-over-Period";
    case "analyst":
      return "Analyst Detail View";
    case "operational":
      return "Operational Dashboard";
    case "mixed":
      return "Summary Dashboard";
  }
}

// ---------------------------------------------------------------------------
// T2 auto-shorten (PLAN.md's Top-100 Corpus plan) — beauty-gate round 2
// "title too big" closure.
//
// `design/corpus/stats/dashboard_norms.yaml` (T1 corpus mining, 110
// workbooks) shows the pre-existing themed-header title fontsize (20pt) is
// already AT/UNDER the mined 900-1400-stratum median (22pt, n=95,
// confidence "ok") — fontsize was never the defect. The real driver was
// LENGTH: a long question-derived title (e.g. "How Are Sales And Profit
// Performing Across Categories And States?") overflows the header zone.
// dashboard_norms.yaml mined title fontsize and title-zone height ratio but
// NEVER a title character-length bucket, so there is no mined p75 to defer
// to for the shortening threshold below.
// ---------------------------------------------------------------------------

/**
 * 40 chars: a documented judgment call (see ASSUMPTIONS.md's
 * "Design-Excellence Top-100 Corpus" section), NOT a mined statistic — there
 * is no title-length bucket in `dashboard_norms.yaml` to defer to. Chosen to
 * keep a shortened title comfortably on one line at the mined 900-1400-
 * stratum title fontsize (median 22pt). `tests/planner-titleShorten.test.ts`
 * re-parses the committed stats file at test time, so a future corpus
 * refresh that DOES add a title-length bucket is caught as a test failure
 * (norm drift) instead of this constant silently going stale.
 */
export const TITLE_AUTO_SHORTEN_THRESHOLD = 40;

/** Truncate `text` to at most `maxLen` chars, backing off to the previous word boundary. */
function truncateAtWordBoundary(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const slice = text.slice(0, maxLen);
  const lastSpace = slice.lastIndexOf(" ");
  return (lastSpace > 0 ? slice.slice(0, lastSpace) : slice).trim();
}

/**
 * Distinct measure field names referenced by the plan's own (post-clamp)
 * sheets, in first-occurrence order. For exec plans the KPI-tile sheets
 * come first and are already ranked by `rankMeasures` inside
 * `buildExecKpiBandPlan`, so this preserves that relevance order; for other
 * audiences it's simply shelf order. Used by `shortenDashboardTitle` to
 * recognize "key measure nouns" without re-deriving field classification.
 */
function planMeasureNames(sheets: readonly SheetSpec[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const sheet of sheets) {
    for (const measure of sheet.measures) {
      if (!seen.has(measure)) {
        seen.add(measure);
        ordered.push(measure);
      }
    }
  }
  return ordered;
}

/**
 * Shorten a long, question-derived dashboard title to a "{Measure(s)}
 * Performance"-style headline, e.g. "How Are Sales And Profit Performing
 * Across Categories And States?" -> "Sales & Profit Performance".
 *
 * Algorithm: extract the plan's own measure names that are actually
 * mentioned in the business question (`isMentioned` — the same
 * word-boundary, case-insensitive matcher `rankMeasures`/`rankDims` use), in
 * their existing plan order, and join up to 2 of them with " & ", then
 * append " Performance". Falls back to `fallbackTitle` truncated to
 * `TITLE_AUTO_SHORTEN_THRESHOLD` chars at a word boundary when no plan
 * measure is mentioned (e.g. a placeholder plan with no real fields, or a
 * question that never names a measure).
 *
 * Pure and deterministic — same inputs, same output.
 */
export function shortenDashboardTitle(
  businessQuestion: string,
  fallbackTitle: string,
  measureNames: readonly string[],
): string {
  const mentioned = measureNames.filter((name) => isMentioned(name, businessQuestion));
  if (mentioned.length > 0) {
    const label = mentioned.slice(0, 2).map(toLabel).join(" & ");
    return `${label} Performance`;
  }
  return truncateAtWordBoundary(fallbackTitle, TITLE_AUTO_SHORTEN_THRESHOLD);
}

// ---------------------------------------------------------------------------
// Placeholder-sheet builder (no fieldHints supplied)
// ---------------------------------------------------------------------------

function buildPlaceholderSheets(_audience: Audience): SheetSpec[] {
  return [
    {
      title: "Overview",
      markType: "bar",
      cols: ["<dimension>"],
      rows: [],
      measures: ["<measure>"],
      rationale:
        "Field names are placeholders — supply fieldHints or edit before calling build_from_plan.",
    },
  ];
}

// ---------------------------------------------------------------------------
// Phase-1: Exec KPI-band plan assembly
//
// For exec audience with real field hints, we produce:
//   1. KPI strip (≤4 kpi_tile sheets) — the top band
//   2. A color-encoded bar (Sales by Category or equivalent)
//   3. A filled map (Sales by State) if a state field is present
//   4. One additional chart (trend or scatter) if room remains
// Then layoutGrammar = { kind: "kpi_band_over_charts" }
// ---------------------------------------------------------------------------

/** Max exec chart sheets below the KPI band (so total stays ≤ maxSheets). */
/**
 * Canonical business-measure priority: when the business question doesn't name
 * a measure, prefer revenue-like measures over operational ones. Lower index =
 * higher priority; unlisted measures rank after all listed ones.
 */
const CANONICAL_MEASURE_PRIORITY: readonly string[] = [
  "sales",
  "revenue",
  "profit",
  "margin",
  "ratio",
  "quantity",
  "orders",
  "units",
  "customers",
  "discount",
  "returns",
  "ship",
];

/** True when the field name (or any of its words) appears as a word in the question. */
export function isMentioned(fieldName: string, questionText: string): boolean {
  const q = questionText.toLowerCase();
  const tokens = fieldName.toLowerCase().split(/[\s_-]+/).filter((t) => t.length > 2);
  if (tokens.length === 0) return false;
  return tokens.every((t) => new RegExp(`\\b${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(q));
}

function canonicalRank(measureName: string): number {
  const lower = measureName.toLowerCase();
  const idx = CANONICAL_MEASURE_PRIORITY.findIndex((k) => lower.includes(k));
  return idx === -1 ? CANONICAL_MEASURE_PRIORITY.length : idx;
}

/**
 * Rank measures deterministically: question-mentioned first, then canonical
 * business priority (sales/revenue/profit before operational metrics), then
 * the original field order as a stable tiebreak. Pure — same inputs, same order.
 */
export function rankMeasures(measures: readonly string[], questionText: string): string[] {
  return measures
    .map((name, index) => ({ name, index }))
    .sort((a, b) => {
      const mentionDelta =
        Number(isMentioned(b.name, questionText)) - Number(isMentioned(a.name, questionText));
      if (mentionDelta !== 0) return mentionDelta;
      const canonDelta = canonicalRank(a.name) - canonicalRank(b.name);
      if (canonDelta !== 0) return canonDelta;
      return a.index - b.index;
    })
    .map((m) => m.name);
}

/**
 * Rank dimensions: question-mentioned first, then low-cardinality, then
 * original order. Keeps bar/color choices aligned with what the user asked.
 */
export function rankDims(
  dims: readonly FieldClassification[],
  questionText: string,
): FieldClassification[] {
  return dims
    .map((field, index) => ({ field, index }))
    .sort((a, b) => {
      const mentionDelta =
        Number(isMentioned(b.field.name, questionText)) -
        Number(isMentioned(a.field.name, questionText));
      if (mentionDelta !== 0) return mentionDelta;
      const lowDelta =
        Number(b.field.cardinalityHint === "low") - Number(a.field.cardinalityHint === "low");
      if (lowDelta !== 0) return lowDelta;
      return a.index - b.index;
    })
    .map((d) => d.field);
}

/** Geo-role preference when the question doesn't name a level: finer grain first. */
const GEO_ROLE_PREFERENCE: readonly string[] = ["state", "city", "zipcode", "country"];

/**
 * Pick the geo field: question-mentioned level wins (e.g. "by state" → State);
 * otherwise prefer state > city > zipcode > country (finer, more useful grain).
 */
export function pickGeoField(
  usable: readonly FieldClassification[],
  questionText: string,
): FieldClassification | undefined {
  const geos = usable.filter(
    (f) => f.role === "geographic" && f.tags.includes("geo_named") && f.geoRole !== undefined,
  );
  if (geos.length === 0) return undefined;
  const ranked = geos
    .map((field, index) => ({ field, index }))
    .sort((a, b) => {
      const mentionDelta =
        Number(isMentioned(b.field.name, questionText)) -
        Number(isMentioned(a.field.name, questionText));
      if (mentionDelta !== 0) return mentionDelta;
      const prefA = GEO_ROLE_PREFERENCE.indexOf(a.field.geoRole ?? "");
      const prefB = GEO_ROLE_PREFERENCE.indexOf(b.field.geoRole ?? "");
      if (prefA !== prefB) return prefA - prefB;
      return a.index - b.index;
    });
  return ranked[0]?.field;
}

const EXEC_MAX_CHARTS = 2;
/** Max KPI tiles in the exec KPI strip. */
const EXEC_MAX_KPI_TILES = 4;

/**
 * Build the exec KPI-band plan from classified fields + question text.
 *
 * The function returns a raw sheet list (before audience clamping) plus
 * a proposed layoutGrammar. The caller is responsible for running
 * applyAudienceClamps on the result.
 *
 * Rules:
 * - KPI tiles first (≤4), binding CP/PP/Difference by name where present.
 * - Below the KPI band: up to EXEC_MAX_CHARTS chart sheets.
 *   • One color-encoded bar (primary measure by first low-card dimension).
 *   • One map_filled if a state/geo field is present.
 *   • If two chart slots remain and no map, a trend line instead.
 */
function buildExecKpiBandPlan(
  allClassifications: FieldClassification[],
  questionText: string,
): { sheets: SheetSpec[]; layoutGrammar: LayoutGrammar } {
  const usable = usableFields(allClassifications);
  // Question-aware, deterministic ranking: measures/dims the user named come
  // first, then canonical business priority (Sales/Profit before Days-to-Ship),
  // then stable field order — so an alphabetical column list can't hijack the
  // KPI band or the primary chart measure.
  const measures = rankMeasures(
    usable.filter((f) => f.role === "measure").map((f) => f.name),
    questionText,
  );
  const dims = rankDims(
    usable.filter((f) => f.role === "dimension" && !f.tags.includes("temporal")),
    questionText,
  );
  const temporal = usable.find((f) => f.role === "temporal" || f.tags.includes("temporal"));
  const geoField = pickGeoField(usable, questionText);

  // 1. KPI strip
  const kpiSheets: SheetSpec[] = buildKpiStrip(
    measures,
    allClassifications,
    EXEC_MAX_KPI_TILES,
  );

  // 2. Chart sheets below the KPI band
  const chartSheets: SheetSpec[] = [];

  // 2a. Color-encoded bar: primary measure by the first low-card dimension,
  // colored by a second dimension when one exists, otherwise by the measure
  // itself (sequential color by magnitude) — so an exec bar is always encoded.
  const primaryMeasure = measures[0];
  // dims are already ranked (mentioned → low-cardinality → stable order).
  const primaryDim = dims[0];
  const colorDim = dims[1];

  if (primaryMeasure && primaryDim) {
    const barSheet: SheetSpec = {
      title: `${toLabel(primaryMeasure)} by ${toLabel(primaryDim.name)}`,
      markType: "bar",
      cols: [primaryDim.name],
      rows: [],
      measures: [primaryMeasure],
      color: colorDim
        ? { field: colorDim.name, kind: "dimension" as const }
        : { field: primaryMeasure, kind: "measure" as const },
    };
    // FEW-7 (BI_DESIGN §9): note the small-multiples alternative when the
    // bar is colored by a second dimension AND the question signals a
    // cross-dimension comparison.
    chartSheets.push(appendSmallMultiplesHintIfApplicable(barSheet, questionText));
  }

  // 2b. Filled map if a geo field is present
  if (
    geoField &&
    geoField.geoRole !== undefined &&
    primaryMeasure &&
    chartSheets.length < EXEC_MAX_CHARTS
  ) {
    const mapSheet: SheetSpec = {
      title: `${toLabel(primaryMeasure)} by ${toLabel(geoField.name)}`,
      markType: "map_filled",
      cols: [],
      rows: [geoField.name],
      measures: [primaryMeasure],
      geo: {
        geoField: geoField.name,
        geoRole: geoField.geoRole,
        colorMeasure: primaryMeasure,
      },
      color: { field: primaryMeasure, kind: "measure" as const },
    };
    chartSheets.push(mapSheet);
  }

  // 2c. Trend line if no map was added and a temporal field exists
  if (temporal && primaryMeasure && chartSheets.length < EXEC_MAX_CHARTS) {
    const trendSheet: SheetSpec = {
      title: `${toLabel(primaryMeasure)} over Time`,
      markType: "line",
      cols: [temporal.name],
      rows: [],
      measures: [primaryMeasure],
    };
    chartSheets.push(trendSheet);
  }

  // If question text contains scatter/correlation hints, add scatter if room
  const wantsScatter =
    /\b(scatter|correlation|drives|relationship|vs\.?|plotted against)\b/i.test(questionText);
  if (wantsScatter && measures.length >= 2 && chartSheets.length < EXEC_MAX_CHARTS) {
    const x = measures[0]!;
    const y = measures[1]!;
    const scatterSheet: SheetSpec = {
      title: `${toLabel(x)} vs ${toLabel(y)}`,
      markType: "scatter",
      cols: [],
      rows: [],
      measures: [x, y],
      scatter: { x, y },
    };
    chartSheets.push(scatterSheet);
  }

  // Assemble final sheet list: KPI tiles first, then chart sheets
  const allSheets: SheetSpec[] = [...kpiSheets, ...chartSheets];

  // Build layoutGrammar
  const layoutGrammar: LayoutGrammar = {
    kind: "kpi_band_over_charts",
    kpiTileTitles: kpiSheets.map((s) => s.title),
    chartTitles: chartSheets.map((s) => s.title),
  };

  return { sheets: allSheets, layoutGrammar };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toLabel(name: string): string {
  return name
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

// ---------------------------------------------------------------------------
// Phase E4 — deterministic story arc (Pillar E, BI_DESIGN §9)
//
// Slice T3 (PLAN.md's Top-100 Corpus plan) gating note: `design/corpus/
// stats/story_norms.yaml` (T1 corpus mining, 110 workbooks incl. the
// top-100 VOTD set) found ZERO storyboards in the entire corpus —
// `usage_rate: { count: 0, n: 0, confidence: "low" }`. Per the plan's own
// n<15 confidence guard (`design/corpus/GAPS.md` §1), a low-confidence
// bucket is NEVER auto-applied — there is no mined evidence to inform a
// usage-rate-based default, so `wantsStoryArc` gates ONLY on an explicit
// signal (question language or persona preference) and nothing looser.
// ---------------------------------------------------------------------------

/** Question language that signals the user wants a narrative artifact. */
const STORY_LANGUAGE_RE = /\b(story|narrative|presentation)\b/i;

/**
 * True when a story arc should be emitted: either the resolved persona
 * explicitly prefers "story" artifacts (brand.yaml `preferredArtifact`), or
 * the business question itself asks for a story/narrative/presentation.
 * These are the ONLY two triggers (see the mined-evidence note above) —
 * do not add a looser one (e.g. audience-based or sheet-count-based) without
 * first re-running the T1 corpus miner and clearing the n>=15 confidence
 * floor for `story_norms.yaml`'s `usage_rate`.
 */
export function wantsStoryArc(
  personaPreferredArtifact: PersonaPreferredArtifact | undefined,
  questionText: string,
): boolean {
  if (personaPreferredArtifact === "story") return true;
  return STORY_LANGUAGE_RE.test(questionText);
}

// ---------------------------------------------------------------------------
// Caption template v2 (Slice T3): takeaway-style, persona-toned,
// question-echoing captions.
//
// Beauty-gate round 2 flagged v1's bare-label captions ("Profit.") as
// "irrelevant — adds no narrative value". `story_norms.yaml`'s 0/110 finding
// (above) means there is no mined caption-STYLE norm to draw on either, so
// v2 is a deterministic-template floor, not a data-driven one — see
// `docs/feature-prompt-authoring/BI_DESIGN.md`'s story section for the full
// rationale and the intended quality path: the CALLING LLM rewrites
// `plan.storyArc[].caption` with real data-aware narrative between
// `design_dashboard`'s proposal and the `build_from_plan` confirm call
// (`assertStoryArcCapturedSheetsExist` in `src/tools/buildFromPlan.ts`
// re-validates `capturedSheet` references either way — the client can
// reorder/drop/rewrite captions freely, just not invent a sheet).
// ---------------------------------------------------------------------------

/**
 * Lowercased, leading-interrogative-stripped echo of the business question,
 * for mid-sentence use in a takeaway caption: "How is sales trending?" ->
 * "sales trending". Empty string when there is no question text to echo
 * (e.g. `directed` mode with no narrative-bearing directions).
 */
function questionEcho(questionText: string): string {
  const trimmed = questionText.trim().replace(/[?!.]+$/, "");
  if (!trimmed) return "";
  const withoutLead = trimmed.replace(
    /^(how|what|why|when|where|which)\s+(is|are|was|were|does|do|did)\s+/i,
    "",
  );
  return withoutLead.charAt(0).toLowerCase() + withoutLead.slice(1);
}

/**
 * Headline caption for the story's opening point (captures the KPI band's
 * leading tile for exec plans, or the plan's first sheet otherwise).
 * "concise" tone: the bare dashboard title. Otherwise: a takeaway sentence
 * that echoes the business question and names the primary measure —
 * falling back to the dashboard title when there's no question to echo.
 */
function deriveHeadlineCaption(
  dashboardTitle: string,
  questionText: string,
  tone: PersonaTone | undefined,
  primaryMeasure: string | undefined,
): string {
  if (tone === "concise") return dashboardTitle;
  const echo = questionEcho(questionText);
  const lead = echo ? `The headline: ${echo}` : `The headline: ${dashboardTitle}`;
  return primaryMeasure
    ? `${lead} — start with ${toLabel(primaryMeasure)} at a glance.`
    : `${lead} — the numbers at a glance.`;
}

/** Per-KPI point: names the tile's own measure alongside the headline's primary measure. */
function deriveKpiCaption(sheet: SheetSpec, primaryMeasure: string | undefined): string {
  const measure = sheet.kpi?.primaryMeasure ?? sheet.measures[0] ?? sheet.title;
  const measureLabel = toLabel(measure);
  if (!primaryMeasure || primaryMeasure === measure) {
    return `${measureLabel}: a lever worth watching.`;
  }
  return `${measureLabel}: watch this lever alongside ${toLabel(primaryMeasure)}.`;
}

/** Drivers point (bar): names the breakdown dimension as the story's "why". */
function deriveDriversCaption(sheet: SheetSpec): string {
  const dim = sheet.cols[0] ?? sheet.rows[0];
  const measure = sheet.measures[0];
  if (dim && measure) {
    return `${toLabel(dim)} drives the mix — where ${toLabel(measure)} concentrates.`;
  }
  return `${sheet.title}: where the mix concentrates.`;
}

/** Geography point (map_filled): names where the measure shows up. */
function deriveGeographyCaption(sheet: SheetSpec): string {
  const measure = sheet.measures[0] ?? sheet.geo?.colorMeasure;
  return measure
    ? `The geography: where ${toLabel(measure)} shows up on the map.`
    : `The geography: where it shows up on the map.`;
}

/** Trend point (line): frames the chart as a direction-of-travel question. */
function deriveTrendCaption(sheet: SheetSpec): string {
  const measure = sheet.measures[0];
  const dim = sheet.cols[0] ?? sheet.rows[0];
  if (!measure) return `${sheet.title}: is the trend moving the right way?`;
  return dim
    ? `${toLabel(measure)} over ${toLabel(dim)} — is the trend moving the right way?`
    : `${toLabel(measure)} over time — is the trend moving the right way?`;
}

/** Relationship point (scatter): frames the pairing as an open question. */
function deriveScatterCaption(sheet: SheetSpec): string {
  if (!sheet.scatter) return `${sheet.title}.`;
  return (
    `${toLabel(sheet.scatter.x)} vs ${toLabel(sheet.scatter.y)} — ` +
    "is there a relationship worth flagging?"
  );
}

/** Generic fallback for any sheet kind/markType not covered above. */
function deriveGenericCaption(sheet: SheetSpec): string {
  const measure = sheet.measures[0];
  const dim = sheet.cols[0] ?? sheet.rows[0];
  if (measure && dim) return `${toLabel(measure)}: a closer look by ${toLabel(dim)}.`;
  if (measure) return `${toLabel(measure)}: a closer look.`;
  return `${sheet.title}: a closer look.`;
}

/**
 * Persona-toned caption for one non-headline story point, routed by the
 * sheet's own kind/markType — kpi_tile -> per-KPI, bar -> drivers,
 * map_filled -> geography, scatter -> relationship, line -> trend, anything
 * else -> a generic takeaway. "concise" tone always uses the sheet's own
 * title (already human-readable — no narrative wrapper needed).
 */
function deriveChartCaption(
  sheet: SheetSpec,
  tone: PersonaTone | undefined,
  primaryMeasure: string | undefined,
): string {
  if (tone === "concise") return sheet.title;
  if (sheet.kind === "kpi_tile") return deriveKpiCaption(sheet, primaryMeasure);
  if (sheet.markType === "bar") return deriveDriversCaption(sheet);
  if (sheet.markType === "map_filled") return deriveGeographyCaption(sheet);
  if (sheet.markType === "scatter") return deriveScatterCaption(sheet);
  if (sheet.markType === "line") return deriveTrendCaption(sheet);
  return deriveGenericCaption(sheet);
}

/**
 * Build a deterministic exec-style story arc over the plan's own sheets:
 * a headline point (the plan's leading sheet — the top KPI tile for exec
 * plans) followed by one point per remaining sheet, in plan order, each
 * captioned by its own kind (KPI tile / bar / map / scatter / line — see
 * `deriveChartCaption`) so a drivers point always captures a bar and a
 * geography point always captures a map, never the wrong sheet kind.
 *
 * Every `capturedSheet` is guaranteed to equal an existing sheet title in
 * `sheets` — `build_from_plan` re-validates this before calling the sidecar,
 * and the builder validates it a third time against the actual workbook
 * (fail loud, never silent) as the final backstop.
 *
 * `questionText` (optional, defaults to "") drives the headline's
 * question-echo — omit it only when there's no original question to echo
 * (the headline caption falls back to `dashboardTitle` in that case).
 */
export function buildStoryArc(
  sheets: readonly SheetSpec[],
  dashboardTitle: string,
  tone: PersonaTone | undefined,
  questionText = "",
): StoryArcPoint[] {
  if (sheets.length === 0) return [];
  const [headline, ...rest] = sheets;
  const primaryMeasure = headline!.kpi?.primaryMeasure ?? headline!.measures[0];
  const points: StoryArcPoint[] = [
    {
      caption: deriveHeadlineCaption(dashboardTitle, questionText, tone, primaryMeasure),
      capturedSheet: headline!.title,
    },
  ];
  for (const sheet of rest) {
    points.push({
      caption: deriveChartCaption(sheet, tone, primaryMeasure),
      capturedSheet: sheet.title,
    });
  }
  return points;
}

// ---------------------------------------------------------------------------
// Main plan generators
// ---------------------------------------------------------------------------

export interface PlanInput {
  mode: "autonomous" | "directed" | "interview_followup";
  audience: Audience;
  businessQuestion?: string;
  directions?: string;
  answers?: Record<string, string>;
  fieldHints?: FieldHint[];
  datasourceLuid: string;
  datasourceName: string;
  projectName: string;
  workbookName?: string;
  requestedLayout?: DashboardLayout;
  datasourceSpec?: DatasourceSpec;
  /**
   * Phase E1 (Slice A): persona-driven audience-constraint overrides, already
   * resolved by the caller (e.g. `design_dashboard` reading brand.yaml). The
   * planner stays pure — it never reads brand.yaml itself.
   */
  constraintOverrides?: AudienceConstraintOverrides;
  /** Named persona (from brand.yaml) that produced `constraintOverrides`, for provenance. */
  personaName?: string;
  /** Brand name (from brand.yaml), for provenance alongside `personaName`. */
  brandName?: string;
  /**
   * Phase E1 (Slice C): the resolved persona's `preferredArtifact` (from
   * brand.yaml), for provenance. "story" drives `wantsStoryArc`/
   * `buildStoryArc` below (Phase E4, shipped); `buildProposal` still
   * surfaces an honest openQuestion for "pulse" (Phase E3 — no dashboard
   * build path consumes it yet).
   */
  personaPreferredArtifact?: PersonaPreferredArtifact;
  /**
   * Phase E1 (Slice C): the resolved persona's `tone` (from brand.yaml), for
   * provenance. `buildProposal` trims the proposal summary to its first
   * sentence + layout line when this is "concise".
   */
  personaTone?: PersonaTone;
}

/** Generate a DashboardPlan from a finalized set of inputs. */
export function generatePlan(input: PlanInput): DashboardPlan {
  // Schema version guard
  assertSchemaVersion(SCHEMA_VERSION);

  const {
    audience,
    fieldHints,
    datasourceLuid,
    datasourceName,
    projectName,
    datasourceSpec,
    requestedLayout,
  } = input;

  // Resolve the primary question text
  const questionText =
    input.mode === "interview_followup"
      ? Object.values(input.answers ?? {}).join(". ")
      : input.mode === "directed"
        ? (input.directions ?? "")
        : (input.businessQuestion ?? "");

  // Derive workbook name
  const workbookName =
    input.workbookName ?? deriveWorkbookName(questionText || datasourceName, audience);

  // Field classification
  let allClassifications: FieldClassification[] = [];
  let hasRealFields = false;
  if (fieldHints && fieldHints.length > 0) {
    allClassifications = classifyFields(fieldHints);
    hasRealFields = true;
  }

  // First available measure name for KPI insertion
  const firstMeasure = hasRealFields
    ? (usableFields(allClassifications).find((f) => f.role === "measure")?.name ?? "")
    : "";

  // ---------------------------------------------------------------------------
  // Phase-1: Exec audience with real fields → KPI-band plan
  // ---------------------------------------------------------------------------

  let layoutGrammarFromExec: LayoutGrammar | undefined;

  let rawSheets: SheetSpec[];
  if (!hasRealFields) {
    rawSheets = buildPlaceholderSheets(audience);
  } else if (audience === "exec" && hasRealFields) {
    // Use the exec KPI-band builder
    const { sheets, layoutGrammar } = buildExecKpiBandPlan(allClassifications, questionText);
    rawSheets = sheets;
    layoutGrammarFromExec = layoutGrammar;
  } else if (!questionText) {
    // No question — produce one bar chart with default shelves
    const usable = usableFields(allClassifications);
    const dim = usable.find(
      (f) =>
        f.role === "dimension" ||
        f.role === "temporal" ||
        f.role === "geographic",
    );
    const meas = usable.find((f) => f.role === "measure");
    rawSheets = [
      {
        title: datasourceName,
        markType: "bar",
        cols: dim ? [dim.name] : [],
        rows: [],
        measures: meas ? [meas.name] : [],
      },
    ];
  } else {
    const rawFromHeuristic = applyMarkHeuristic(questionText, allClassifications, "Sheet");
    // Convert RawSheet → SheetSpec (same shape, but SheetSpec is typed)
    rawSheets = rawFromHeuristic.map((s) => ({
      title: s.title,
      markType: s.markType,
      cols: s.cols,
      rows: s.rows,
      measures: s.measures,
      rationale: s.rationale,
      // Phase-1 optional encoding blocks
      ...(s.kind !== undefined ? { kind: s.kind } : {}),
      ...(s.color !== undefined ? { color: s.color } : {}),
      ...(s.kpi !== undefined ? { kpi: s.kpi } : {}),
      ...(s.scatter !== undefined ? { scatter: s.scatter } : {}),
      ...(s.geo !== undefined ? { geo: s.geo } : {}),
    }));
  }

  // ---------------------------------------------------------------------------
  // Audience clamps
  // ---------------------------------------------------------------------------

  // For exec, the KPI-band builder already places kpi_tile sheets first and
  // chart sheets after. We pass firstMeasure so stepEnsureKpiLead is satisfied
  // by the leading text mark (kpi_tile has markType="text").
  const { sheets, dashboardLayout: rawLayout } = applyAudienceClamps(
    rawSheets,
    audience,
    firstMeasure,
    requestedLayout,
    input.constraintOverrides,
  );

  // L-01 layout gap
  const { layout: dashboardLayout, note: l01Note } = resolveAnalystLayout(
    audience,
    sheets.length,
    rawLayout,
  );

  // ---------------------------------------------------------------------------
  // Phase-1: Dashboard title + subtitle + layoutGrammar
  // ---------------------------------------------------------------------------

  const derivedTitle = deriveDashboardTitle(questionText || datasourceName, audience);
  const audienceSubtitle = deriveDashboardSubtitle(audience);

  // T2 auto-shorten (see the block above `TITLE_AUTO_SHORTEN_THRESHOLD`): a
  // long question-derived title becomes a short "{Measure} Performance"
  // headline, and the full original question takes over as the subtitle —
  // replacing the generic per-audience default, which is never something a
  // caller "set" (there is no explicit-subtitle input on `PlanInput` today).
  const shouldShortenTitle =
    derivedTitle.length > TITLE_AUTO_SHORTEN_THRESHOLD && questionText.trim().length > 0;
  const dashboardTitle = shouldShortenTitle
    ? shortenDashboardTitle(questionText, derivedTitle, planMeasureNames(sheets))
    : derivedTitle;
  const dashboardSubtitle = shouldShortenTitle ? questionText.trim() : audienceSubtitle;

  // layoutGrammar: use the exec-specific one if we built it, otherwise derive
  // from the dashboardLayout for other audiences.
  let layoutGrammar: LayoutGrammar | undefined;
  if (layoutGrammarFromExec !== undefined && audience === "exec") {
    // Rebuild titles from the clamped sheets for accuracy
    const kpiTileSheets = sheets.filter((s) => s.kind === "kpi_tile");
    const chartSheetsAfterClamp = sheets.filter((s) => s.kind !== "kpi_tile");
    layoutGrammar = {
      kind: "kpi_band_over_charts",
      kpiTileTitles: kpiTileSheets.map((s) => s.title),
      chartTitles: chartSheetsAfterClamp.map((s) => s.title),
    };
  }

  // Build rationale
  const rationaleLines: string[] = [AUDIENCE_NOTES[audience]];
  if (l01Note) rationaleLines.push(l01Note);
  if (!hasRealFields) {
    rationaleLines.push(
      "No field hints were provided; sheets contain placeholder tokens. Fill them before calling build_from_plan.",
    );
  }
  const rationale = rationaleLines.join(" ");

  // ---------------------------------------------------------------------------
  // Phase E4: deterministic story arc (persona preference or question language)
  // ---------------------------------------------------------------------------

  const emitStoryArc = wantsStoryArc(input.personaPreferredArtifact, questionText);
  const storyArc = emitStoryArc
    ? buildStoryArc(sheets, dashboardTitle, input.personaTone, questionText)
    : undefined;
  const storyName = emitStoryArc ? dashboardTitle : undefined;

  const plan = DashboardPlanSchema.parse({
    schemaVersion: SCHEMA_VERSION,
    kind: "plan",
    workbookName,
    datasourceLuid,
    datasourceName,
    projectName,
    audience,
    rationale,
    dashboardLayout,
    sheets,
    ...(datasourceSpec ? { datasourceSpec } : {}),
    // Phase-1 optional fields
    dashboardTitle,
    dashboardSubtitle,
    ...(layoutGrammar !== undefined ? { layoutGrammar } : {}),
    ...(input.personaName !== undefined ? { personaName: input.personaName } : {}),
    ...(input.brandName !== undefined ? { brandName: input.brandName } : {}),
    ...(input.personaPreferredArtifact !== undefined
      ? { personaPreferredArtifact: input.personaPreferredArtifact }
      : {}),
    ...(input.personaTone !== undefined ? { personaTone: input.personaTone } : {}),
    ...(storyArc !== undefined ? { storyArc } : {}),
    ...(storyName !== undefined ? { storyName } : {}),
  });

  return plan;
}

// ---------------------------------------------------------------------------
// Interview mode
// ---------------------------------------------------------------------------

export interface InterviewOptions {
  audience?: Audience;
  context?: string;
  fieldHints?: FieldHint[];
  businessQuestion?: string;
}

/** Return a ClarifyingQuestions object for interview mode. */
export function generateInterview(options: InterviewOptions): ClarifyingQuestions {
  const input: InterviewInput = {
    audience: options.audience,
    businessQuestion: options.businessQuestion,
    context: options.context,
    fieldHints: options.fieldHints,
  };
  const questions = selectQuestions(input);
  return {
    schemaVersion: SCHEMA_VERSION,
    kind: "questions",
    questions,
  };
}
