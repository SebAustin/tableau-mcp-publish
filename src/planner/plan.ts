/**
 * Planning pipeline: fieldHints → marks → clamp → DashboardPlan.
 *
 * This is the entry point used by `design_dashboard`. It wires the pure
 * sub-functions together and enforces the schemaVersion guard.
 */

import { classifyFields, usableFields } from "./fields.js";
import type { FieldHint, FieldClassification } from "./fields.js";
import { applyMarkHeuristic } from "./marks.js";
import { applyAudienceClamps } from "./audience.js";
import {
  assertSchemaVersion,
  SCHEMA_VERSION,
  type Audience,
  type DashboardPlan,
  type DashboardLayout,
  type SheetSpec,
  type DatasourceSpec,
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
  let classifications: FieldClassification[] = [];
  let hasRealFields = false;
  if (fieldHints && fieldHints.length > 0) {
    classifications = classifyFields(fieldHints);
    hasRealFields = true;
  }

  // First available measure name for KPI insertion
  const firstMeasure = hasRealFields
    ? (usableFields(classifications).find((f) => f.role === "measure")?.name ?? "")
    : "";

  // Build raw sheets
  let rawSheets: SheetSpec[];
  if (!hasRealFields) {
    rawSheets = buildPlaceholderSheets(audience);
  } else if (!questionText) {
    // No question — produce one bar chart with default shelves
    const usable = usableFields(classifications);
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
    const rawFromHeuristic = applyMarkHeuristic(questionText, classifications, "Sheet");
    // Convert RawSheet → SheetSpec (same shape, but SheetSpec is typed)
    rawSheets = rawFromHeuristic.map((s) => ({
      title: s.title,
      markType: s.markType,
      cols: s.cols,
      rows: s.rows,
      measures: s.measures,
      rationale: s.rationale,
    }));
  }

  // Audience clamps
  const { sheets, dashboardLayout: rawLayout } = applyAudienceClamps(
    rawSheets,
    audience,
    firstMeasure,
    requestedLayout,
  );

  // L-01 layout gap
  const { layout: dashboardLayout, note: l01Note } = resolveAnalystLayout(
    audience,
    sheets.length,
    rawLayout,
  );

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
