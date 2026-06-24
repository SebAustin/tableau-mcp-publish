/**
 * Shared DashboardPlan / ClarifyingQuestions Zod schema (BI_DESIGN §0/§8.1).
 *
 * This module is the single source of truth for the contract between
 * `design_dashboard` (producer) and `build_from_plan` (consumer).
 * Both tools import from here; the sidecar mirrors the shape as a Pydantic model.
 *
 * The `schemaVersion` literal `1` is enforced at parse time — any other value throws.
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

export const MarkTypeEnum = z.enum(["bar", "line", "text", "map"]);
export type MarkType = z.infer<typeof MarkTypeEnum>;

export const DashboardLayoutEnum = z.enum(["tiled_vertical", "tiled_horizontal"]);
export type DashboardLayout = z.infer<typeof DashboardLayoutEnum>;

// ---------------------------------------------------------------------------
// FieldHint schema
// ---------------------------------------------------------------------------

export const FieldHintSchema = z.object({
  name: z.string(),
  role: z.enum(["dimension", "measure"]).optional(),
  dataType: z.enum(["string", "number", "date", "boolean"]).optional(),
});

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
  })
  .refine(
    (d) => (d.filePath !== undefined) !== (d.sql !== undefined) || d.filePath !== undefined,
    {
      message: "DatasourceSpec must set exactly one of filePath or sql+connection.",
    },
  );

export type DatasourceSpec = z.infer<typeof DatasourceSpecSchema>;

// ---------------------------------------------------------------------------
// DashboardPlan
// ---------------------------------------------------------------------------

export const DashboardPlanSchema = z
  .object({
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
    .max(7),
});

export type ClarifyingQuestions = z.infer<typeof ClarifyingQuestionsSchema>;

// ---------------------------------------------------------------------------
// Type guard
// ---------------------------------------------------------------------------

/** Returns true when the payload is a DashboardPlan (discriminated by `kind`). */
export function isDashboardPlan(payload: unknown): payload is DashboardPlan {
  if (typeof payload !== "object" || payload === null) return false;
  const p = payload as Record<string, unknown>;
  if (p["kind"] !== "plan") return false;
  const result = DashboardPlanSchema.safeParse(payload);
  return result.success;
}
