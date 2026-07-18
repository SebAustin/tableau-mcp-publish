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
 * Persona artifact preference (Phase E1, Slice C — BI_DESIGN §9). Mirrors
 * `PersonaOverrides.preferredArtifact` in `../branding/schema.ts`, redeclared
 * here (rather than imported) so the planner never depends on the branding
 * module — the tool layer resolves brand.yaml and passes the plain value in.
 */
export const PersonaPreferredArtifactEnum = z.enum(["dashboard", "story", "pulse"]);
export type PersonaPreferredArtifact = z.infer<typeof PersonaPreferredArtifactEnum>;

/**
 * Persona tone preference (Phase E1, Slice C — BI_DESIGN §9). Mirrors
 * `PersonaOverrides.tone` in `../branding/schema.ts`; redeclared for the same
 * planner-purity reason as `PersonaPreferredArtifactEnum` above.
 */
export const PersonaToneEnum = z.enum(["concise", "detailed"]);
export type PersonaTone = z.infer<typeof PersonaToneEnum>;

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

/**
 * A single worksheet style-rule override (Design Excellence, Slice D1 —
 * carry-only; mirrors sidecar's SheetStyleRuleModel / sidecar.ts's
 * SheetStyleRule). `formats` is a flat string-keyed map of Tableau format
 * attribute name → value (e.g. `{ "font-color": "#0b1f3a" }`).
 */
export const SheetStyleRuleSchema = z.object({
  element: z.string().min(1),
  formats: z.record(z.string(), z.string()),
});
export type SheetStyleRule = z.infer<typeof SheetStyleRuleSchema>;

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
  // --- Design Excellence, Slice D1: optional per-sheet style-rule overrides (carry-only) ---
  styleRules: z.array(SheetStyleRuleSchema).optional(),
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

/**
 * One point in a story arc (Phase E4 — Stories, BI_DESIGN §9).
 * `capturedSheet` must name one of the plan's own sheet titles — `plan.ts`'s
 * `buildStoryArc` only ever emits references to `plan.sheets[].title`, and
 * `build_from_plan` re-validates this before calling the sidecar (the
 * builder validates it a third time against the actual workbook, as the
 * final backstop — see `twb_builder._build_story`).
 */
export const StoryArcPointSchema = z.object({
  caption: z.string().min(1),
  capturedSheet: z.string().min(1),
});
export type StoryArcPoint = z.infer<typeof StoryArcPointSchema>;

// ---------------------------------------------------------------------------
// Design-theme sub-schemas (Design Excellence, Slice D1 — wire plumbing,
// carry-only). Mirrors the flat wire shape sidecar.ts's DesignTheme family
// and sidecar/server.py's DesignThemeModel family will consume in Slice D2
// onward. All fields optional except `name`, so this is additive and never
// required by an existing plan.
// ---------------------------------------------------------------------------

/** Hairline border spec for a themed zone. */
export const ThemeBorderSchema = z.object({
  color: z.string().optional(),
  style: z.string().optional(),
  width: z.number().optional(),
});
export type ThemeBorder = z.infer<typeof ThemeBorderSchema>;

/** Datalabel styling (size / weight / color mode). */
export const ThemeDatalabelSchema = z.object({
  fontSize: z.number().optional(),
  fontWeight: z.string().optional(),
  colorMode: z.string().optional(),
  // Design Excellence, Slice D4: wire completion (builder-consumed since D3).
  color: z.string().optional(),
});
export type ThemeDatalabel = z.infer<typeof ThemeDatalabelSchema>;

/** Chrome-removal rules: gridlines/zeroline/ticks/mark-labels. */
export const ThemeChromeSchema = z.object({
  hideGridlines: z.boolean().optional(),
  hideZeroline: z.boolean().optional(),
  hideAxisTicks: z.boolean().optional(),
  showMarkLabels: z.boolean().optional(),
  datalabel: ThemeDatalabelSchema.optional(),
  // Design Excellence, Slice D4: wire completion (builder-consumed since D3).
  titleColor: z.string().optional(),
});
export type ThemeChrome = z.infer<typeof ThemeChromeSchema>;

/** Canvas spacing (outer margin + gutter between zones). */
export const ThemeSpacingSchema = z.object({
  outerMargin: z.number().optional(),
  gutter: z.number().optional(),
});
export type ThemeSpacing = z.infer<typeof ThemeSpacingSchema>;

/** Chart-card zone-style box model (background/border/padding/margin/corner-radius). */
export const ThemeChartCardSchema = z.object({
  background: z.string().optional(),
  border: ThemeBorderSchema.optional(),
  padding: z.number().optional(),
  margin: z.number().optional(),
  cornerRadius: z.number().optional(),
});
export type ThemeChartCard = z.infer<typeof ThemeChartCardSchema>;

/** KPI-tile zone-style box model. */
export const ThemeKpiTileSchema = z.object({
  background: z.string().optional(),
  border: ThemeBorderSchema.optional(),
  padding: z.number().optional(),
  banColor: z.string().optional(),
  useSemanticDeltaColors: z.boolean().optional(),
});
export type ThemeKpiTile = z.infer<typeof ThemeKpiTileSchema>;

/** Header-band styling. */
export const ThemeHeaderSchema = z.object({
  background: z.string().optional(),
  titleColor: z.string().optional(),
  subtitleColor: z.string().optional(),
});
export type ThemeHeader = z.infer<typeof ThemeHeaderSchema>;

/**
 * Resolved design theme block. Design Excellence, Slice D1 — carry-only:
 * embedded on a DashboardPlan but not read by the builder yet (lands in
 * Slice D2 onward). Absent → unchanged behavior.
 */
export const DesignThemeSchema = z.object({
  name: z.string().min(1),
  dashboardBackground: z.string().optional(),
  spacing: ThemeSpacingSchema.optional(),
  chartCard: ThemeChartCardSchema.optional(),
  kpiTile: ThemeKpiTileSchema.optional(),
  header: ThemeHeaderSchema.optional(),
  chrome: ThemeChromeSchema.optional(),
});
export type DesignTheme = z.infer<typeof DesignThemeSchema>;

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
  // --- Design Excellence, Slice D1: resolved design-theme block (carry-only) ---
  /**
   * Resolved design theme block (from the corpus theme layer, once it
   * lands). Carry-only in this slice: `build_from_plan` forwards it to the
   * sidecar unread by the builder. Absent → unchanged behavior.
   */
  designTheme: DesignThemeSchema.optional(),
  // --- Phase E1 (Slice A): brand/persona provenance, resolved by the tool layer ---
  /** Named persona (from brand.yaml) that resolved this plan's audience + overrides, if any. */
  personaName: z.string().optional(),
  /** Brand name (from brand.yaml) applied when a persona was resolved, if any. */
  brandName: z.string().optional(),
  // --- Phase E1 (Slice C): persona artifact/tone provenance (BI_DESIGN §9) ---
  /**
   * The resolved persona's preferred artifact type (from brand.yaml), if any.
   * "story" now drives `generatePlan()` to emit a `storyArc` (Phase E4, see
   * below) instead of a stub; `buildProposal` still surfaces an honest
   * openQuestion for "pulse" (Phase E3 provenance-only — no dashboard build
   * path consumes it yet).
   */
  personaPreferredArtifact: PersonaPreferredArtifactEnum.optional(),
  /**
   * The resolved persona's tone preference (from brand.yaml), if any.
   * `buildProposal` trims the proposal summary to its first sentence + the
   * layout line when this is "concise". Also drives the story-point caption
   * style in `buildStoryArc` (Phase E4): "concise" → the sheet's own title;
   * default/"detailed" → a short narrative sentence.
   */
  personaTone: PersonaToneEnum.optional(),
  // --- Phase E4: deterministic story arc (Pillar E, BI_DESIGN §9) ---
  /**
   * Ordered story points, emitted by `generatePlan()` when the resolved
   * persona prefers "story" artifacts OR the business question itself uses
   * story/narrative/presentation language. Every `capturedSheet` is one of
   * this plan's own `sheets[].title` values. Absent when no story arc was
   * warranted for this plan.
   */
  storyArc: z.array(StoryArcPointSchema).optional(),
  /**
   * Display name for the story (before `build_from_plan` applies the
   * `"Story: "` prefix the Tableau story dashboard name requires). Present
   * iff `storyArc` is present.
   */
  storyName: z.string().optional(),
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
   * Ordered captions from `plan.storyArc`, if any (Phase E4). Mirrors
   * `plan.storyArc.map(p => p.caption)` — surfaced here so a caller can
   * render the story outline without re-deriving it from the plan.
   */
  storyOutline: z.array(z.string()).optional(),
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
