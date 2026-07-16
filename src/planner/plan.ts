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
 */

import { classifyFields, usableFields } from "./fields.js";
import type { FieldHint, FieldClassification } from "./fields.js";
import { applyMarkHeuristic, buildKpiStrip } from "./marks.js";
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
    chartSheets.push(barSheet);
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

  const dashboardTitle = deriveDashboardTitle(questionText || datasourceName, audience);
  const dashboardSubtitle = deriveDashboardSubtitle(audience);

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
