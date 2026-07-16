/**
 * Shared DashboardPlan / ClarifyingQuestions / DashboardProposal Zod schema
 * (BI_DESIGN §0/§8.1).
 *
 * This module is the single source of truth for the contract between
 * `design_dashboard` (producer) and `build_from_plan` (consumer).
 * Both tools import from here; the sidecar mirrors the shape as a Pydantic model.
 *
 * The `schemaVersion` literal `1` is enforced at parse time — any other value throws.
 *
 * NOTE — schemaVersion bump to 2: deferred until required-by-kind validation
 * (asserting e.g. that a kpi_tile sheet always carries a kpi block) lands in a
 * later phase.  All additions below are OPTIONAL so existing callers and tests
 * remain valid against schemaVersion 1.
 */

import { z } from "zod";
import type { FieldHint } from "./fields.js";

// Re-export so callers have one import point.
export type { FieldHint };

// ---------------------------------------------------------------------------
// Schema version guard
// ---------------------------------------------------------------------------

export const SCHEMA_VERSION = 1 as const;

/** Throw if the supplied version does not match the supported version. */
export function assertSchemaVersion(version: unknown): void {
  if (version !== SCHEMA_VERSION) {
    throw new Error(
      `Unsupported schemaVersion: ${String(version)}. ` +
        `This planner only supports schemaVersion ${SCHEMA_VERSION}.`,
    );
  }
}

// ---------------------------------------------------------------------------
// Shared enums
// ---------------------------------------------------------------------------

export const AudienceEnum = z.enum(["exec", "analyst", "operational", "mixed"]);
export type Audience = z.infer<typeof AudienceEnum>;

/** Extended with Phase-1 mark types: scatter (Circle mark) and map_filled (filled-map). */
export const MarkTypeEnum = z.enum(["bar", "line", "text", "map", "scatter", "map_filled"]);
export type MarkType = z.infer<typeof MarkTypeEnum>;

export const DashboardLayoutEnum = z.enum(["tiled_vertical", "tiled_horizontal"]);
export type DashboardLayout = z.infer<typeof DashboardLayoutEnum>;

/**
 * Sheet kind — distinguishes a standard chart from a KPI tile.
 * Defaults to "chart" so plain {title, markType, rows, cols, measures} sheets
 * are backward-compatible without explicitly setting this field.
 */
export const SheetKindEnum = z.enum(["chart", "kpi_tile"]);
export type SheetKind = z.infer<typeof SheetKindEnum>;

// ---------------------------------------------------------------------------
// FieldHint schema
// ---------------------------------------------------------------------------

export const FieldHintSchema = z.object({
  name: z.string(),
  role: z.enum(["dimension", "measure"]).optional(),
  dataType: z.enum(["string", "number", "date", "boolean"]).optional(),
});

// ---------------------------------------------------------------------------
// SheetSpec sub-schemas (new optional encoding blocks)
// ---------------------------------------------------------------------------

/**
 * Color encoding for a worksheet.
 * - kind "dimension": color by a categorical field (e.g. Category).
 * - kind "measure_names": color by Measure Names (used in multi-measure lines/bars).
 * - kind "measure": color by a quantitative measure (e.g. Profit on a filled map).
 */
export const SheetColorSchema = z.object({
  field: z.string().min(1),
  kind: z.enum(["dimension", "measure_names", "measure"]),
});
export type SheetColor = z.infer<typeof SheetColorSchema>;

/**
 * KPI tile encoding.
 * primaryMeasure is always shown; comparison/delta/sparkline are optional.
 * deltaIsPositiveGood controls the coloring of the delta arrow (green-up vs red-up).
 */
export const SheetKpiSchema = z.object({
  primaryMeasure: z.string().min(1),
  comparisonMeasure: z.string().optional(),
  deltaMeasure: z.string().optional(),
  deltaIsPositiveGood: z.boolean().optional(),
  sparklineField: z.string().optional(),
  valuePrefix: z.string().optional(),
  valueSuffix: z.string().optional(),
});
export type SheetKpi = z.infer<typeof SheetKpiSchema>;

/**
 * Scatter-plot encoding.
 * x / y are measure fields; breakdown is an optional dimension for color/shape.
 */
export const SheetScatterSchema = z.object({
  x: z.string().min(1),
  y: z.string().min(1),
  breakdown: z.string().optional(),
});
export type SheetScatter = z.infer<typeof SheetScatterSchema>;

/**
 * Filled-map / geographic encoding.
 * geoField is the dimension column; geoRole defines how Tableau geocodes it.
 * colorMeasure is the optional KPI painted on the map.
 */
export const SheetGeoSchema = z.object({
  geoField: z.string().min(1),
  geoRole: z.enum(["state", "country", "city", "zipcode"]),
  colorMeasure: z.string().optional(),
});
export type SheetGeo = z.infer<typeof SheetGeoSchema>;

// ---------------------------------------------------------------------------
// SheetSpec — superset of the existing sidecar.ts SheetSpec
// ---------------------------------------------------------------------------

export const SheetSpecSchema = z.object({
  title: z.string().min(1),
  markType: MarkTypeEnum,
  rows: z.array(z.string()).default([]),
  cols: z.array(z.string()).default([]),
  measures: z.array(z.string()).default([]),
  rationale: z.string().optional(),
  // --- Phase-1 optional encoding blocks (Slice 2: carry end-to-end) ---
  /** Sheet kind: "chart" (default) or "kpi_tile". */
  kind: SheetKindEnum.optional(),
  /** Color encoding for dimension, measure-names, or quantitative coloring. */
  color: SheetColorSchema.optional(),
  /** KPI tile configuration (required when kind = "kpi_tile"; validated in a later phase). */
  kpi: SheetKpiSchema.optional(),
  /** Scatter-plot axis binding (for markType = "scatter"). */
  scatter: SheetScatterSchema.optional(),
  /** Geographic encoding (for markType = "map_filled"). */
  geo: SheetGeoSchema.optional(),
});

export type SheetSpec = z.infer<typeof SheetSpecSchema>;

// ---------------------------------------------------------------------------
// DatasourceSpec — present only when a new datasource must be built
// ---------------------------------------------------------------------------

export const DatasourceSpecSchema = z
  .object({
    datasourceName: z.string().min(1),
    filePath: z.string().optional(),
    fileType: z.enum(["csv", "json", "jsonl", "xlsx", "xls", "parquet"]).optional(),
    excelSheet: z.union([z.string(), z.number().int().nonnegative()]).optional(),
    jsonPath: z.string().optional(),
    sql: z.string().optional(),
    connection: z.record(z.unknown()).optional(),
    /**
     * Explicit text encoding for CSV files (e.g. "utf-16").
     * When absent the sidecar auto-sniffs from the BOM.
     * Threaded end-to-end in Slice 1; schema field added here for completeness.
     */
    encoding: z.string().optional(),
    /**
     * Column delimiter for CSV files (e.g. "\t").
     * When absent the sidecar auto-sniffs from the decoded header row.
     */
    delimiter: z.string().optional(),
  })
  .refine(
    (d) => (d.filePath !== undefined) !== (d.sql !== undefined) || d.filePath !== undefined,
    {
      message: "DatasourceSpec must set exactly one of filePath or sql+connection.",
    },
  );

export type DatasourceSpec = z.infer<typeof DatasourceSpecSchema>;

// ---------------------------------------------------------------------------
// DashboardPlan layout sub-schemas
// ---------------------------------------------------------------------------

/**
 * Layout grammar for the dashboard canvas.
 *
 * - "kpi_band_over_charts": a horizontal KPI strip at the top with chart tiles
 *   below (Phase-1 target for exec dashboards).
 * - "tiled_vertical" / "tiled_horizontal": simple 1-D tiling (existing behavior).
 */
export const LayoutGrammarKindEnum = z.enum([
  "kpi_band_over_charts",
  "tiled_vertical",
  "tiled_horizontal",
]);
export type LayoutGrammarKind = z.infer<typeof LayoutGrammarKindEnum>;

export const LayoutGrammarSchema = z.object({
  kind: LayoutGrammarKindEnum,
  /** Titles for the KPI tiles in the top band (used by kpi_band_over_charts). */
  kpiTileTitles: z.array(z.string()).optional(),
  /** Titles for the chart tiles in the main area. */
  chartTitles: z.array(z.string()).optional(),
});
export type LayoutGrammar = z.infer<typeof LayoutGrammarSchema>;

export const TextZoneSchema = z.object({
  text: z.string().min(1),
  position: z.enum(["header", "footer"]),
});
export type TextZone = z.infer<typeof TextZoneSchema>;

// ---------------------------------------------------------------------------
// DashboardPlan
// ---------------------------------------------------------------------------

export const DashboardPlanSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  kind: z.literal("plan"),
  workbookName: z.string().min(1),
  datasourceLuid: z.string().min(1),
  datasourceName: z.string().min(1),
  projectName: z.string().min(1),
  audience: AudienceEnum,
  rationale: z.string().min(1),
  dashboardLayout: DashboardLayoutEnum,
  sheets: z.array(SheetSpecSchema).min(1),
  datasourceSpec: DatasourceSpecSchema.optional(),
  // --- Phase-1 optional dashboard-level fields (Slice 2: carry end-to-end) ---
  /** Human-readable dashboard title rendered in the title text zone. */
  dashboardTitle: z.string().optional(),
  /** Human-readable dashboard subtitle rendered below the title. */
  dashboardSubtitle: z.string().optional(),
  /** Explicit text zones (header / footer) additional to any auto-derived title zone. */
  textZones: z.array(TextZoneSchema).optional(),
  /** Structured layout grammar used by the builder to emit multi-zone XML. */
  layoutGrammar: LayoutGrammarSchema.optional(),
  // --- Phase E1 (Slice A): brand/persona provenance, resolved by the tool layer ---
  /** Named persona (from brand.yaml) that resolved this plan's audience + overrides, if any. */
  personaName: z.string().optional(),
  /** Brand name (from brand.yaml) applied when a persona was resolved, if any. */
  brandName: z.string().optional(),
});

export type DashboardPlan = z.infer<typeof DashboardPlanSchema>;

// ---------------------------------------------------------------------------
// ClarifyingQuestions
// ---------------------------------------------------------------------------

export const ClarifyingQuestionsSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  kind: z.literal("questions"),
  questions: z
    .array(
      z.object({
        id: z.string().min(1),
        question: z.string().min(1),
        hint: z.string().optional(),
      }),
    )
    .min(3)
    .max(10),
});

export type ClarifyingQuestions = z.infer<typeof ClarifyingQuestionsSchema>;

// ---------------------------------------------------------------------------
// DashboardProposal — the propose→confirm contract (BI_DESIGN §2.6)
//
// Returned by `design_dashboard` once enough context is known.
// The agent presents this to the user; on "confirm" the agent calls
// `build_from_plan` with `proposal.plan` verbatim (re-validated server-side).
// The `design_dashboard` tool NEVER builds; building is always a separate step.
// ---------------------------------------------------------------------------

export const KpiStripItemSchema = z.object({
  /** Display label for this KPI tile. */
  label: z.string().min(1),
  /** Primary measure field name. */
  primaryMeasure: z.string().min(1),
  /** Comparison-period measure field name (optional). */
  comparisonMeasure: z.string().optional(),
  /** Delta / difference measure field name (optional). */
  deltaMeasure: z.string().optional(),
  /**
   * Directional interpretation of the delta for coloring:
   * - "up_good": positive delta → green arrow.
   * - "down_good": negative delta → green arrow (e.g. cost, returns).
   * - "neutral": no coloring applied.
   */
  direction: z.enum(["up_good", "down_good", "neutral"]),
});
export type KpiStripItem = z.infer<typeof KpiStripItemSchema>;

export const ProposedViewSchema = z.object({
  /** Worksheet title. */
  title: z.string().min(1),
  /** Mark type / chart type identifier (e.g. "bar", "scatter", "map_filled"). */
  chartType: z.string().min(1),
  /** Human-readable encoding summary (e.g. "Sales by Category, colored by Segment"). */
  encodingSummary: z.string().min(1),
  /** Field names referenced by this view. */
  fields: z.array(z.string()).min(1),
});
export type ProposedView = z.infer<typeof ProposedViewSchema>;

export const DashboardProposalSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  kind: z.literal("proposal"),
  workbookName: z.string().min(1),
  audience: AudienceEnum,
  datasourceName: z.string().min(1),
  projectName: z.string().min(1),
  /** One-paragraph natural-language summary of the proposal for the user. */
  summary: z.string().min(1),
  /** Proposed dashboard title (optional; mirrors DashboardPlan.dashboardTitle). */
  dashboardTitle: z.string().optional(),
  /** Proposed dashboard subtitle (optional). */
  dashboardSubtitle: z.string().optional(),
  /** KPI strip items; empty array means no KPI band. */
  kpiStrip: z.array(KpiStripItemSchema),
  /** Proposed worksheet views. */
  views: z.array(ProposedViewSchema).min(1),
  /** Natural-language description of the layout for the user. */
  layoutSummary: z.string().min(1),
  /**
   * Open questions the agent needs answered before producing a full plan.
   * Present only when the proposal is preliminary.
   */
  openQuestions: z.array(z.string()).optional(),
  /**
   * The ready-to-execute plan embedded in the proposal.
   * The agent passes this verbatim to `build_from_plan` on user confirmation.
   */
  plan: DashboardPlanSchema,
});

export type DashboardProposal = z.infer<typeof DashboardProposalSchema>;

// ---------------------------------------------------------------------------
// Type guards
// ---------------------------------------------------------------------------

/** Returns true when the payload is a DashboardPlan (discriminated by `kind`). */
export function isDashboardPlan(payload: unknown): payload is DashboardPlan {
  if (typeof payload !== "object" || payload === null) return false;
  const p = payload as Record<string, unknown>;
  if (p["kind"] !== "plan") return false;
  const result = DashboardPlanSchema.safeParse(payload);
  return result.success;
}

/** Returns true when the payload is a DashboardProposal (discriminated by `kind`). */
export function isDashboardProposal(payload: unknown): payload is DashboardProposal {
  if (typeof payload !== "object" || payload === null) return false;
  const p = payload as Record<string, unknown>;
  if (p["kind"] !== "proposal") return false;
  const result = DashboardProposalSchema.safeParse(payload);
  return result.success;
}
