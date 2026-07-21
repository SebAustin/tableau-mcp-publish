/**
 * Metric-dictionary derivation (the external skill suite's Scribe "Metric Builder", deterministic).
 *
 * Turns the field metadata `get_datasource_fields` already returns into a
 * structured metric dictionary — role, suggested aggregation, display format,
 * and a one-line definition template per field. Pure and deterministic: it
 * REUSES the planner's own role inference (`classifyField`, BI_DESIGN §1) so the
 * dictionary agrees with how the planner itself reads a field, and mirrors the
 * sidecar's currency/percent word-hints for the format guess. No I/O here.
 */

import { classifyField, type FieldHint } from "./fields.js";

/** A field as `get_datasource_fields` returns it (VDS-sourced). */
export interface DatasourceField {
  name: string;
  caption?: string;
  dataType?: string;
  defaultAggregation?: string;
}

export type MetricRole = "measure" | "dimension" | "date";
export type MetricFormat = "currency" | "percent" | "number" | "text";

export interface MetricEntry {
  name: string;
  caption?: string;
  role: MetricRole;
  suggestedAggregation?: string;
  format: MetricFormat;
  definitionTemplate: string;
}

export interface MetricDictionary {
  metrics: MetricEntry[];
  count: number;
}

/** Normalize a VDS/Tableau logical data-type string to the planner's dtype enum. */
function normalizeDtype(dataType?: string): FieldHint["dataType"] {
  if (!dataType) return undefined;
  const d = dataType.toUpperCase();
  if (["INTEGER", "REAL", "NUMBER", "DECIMAL", "FLOAT", "DOUBLE"].includes(d)) return "number";
  if (["STRING", "TEXT", "CHAR"].includes(d)) return "string";
  if (["DATE", "DATETIME", "TIMESTAMP"].includes(d)) return "date";
  if (["BOOLEAN", "BOOL"].includes(d)) return "boolean";
  return undefined;
}

/** Collapse the planner's 5 field roles into the dictionary's 3-way role. */
function toMetricRole(plannerRole: string): MetricRole {
  if (plannerRole === "temporal") return "date";
  if (plannerRole === "measure") return "measure";
  return "dimension"; // dimension | identifier | geographic
}

const CURRENCY_HINT = /(price|sales|revenue|cost|profit|discount|amount|spend|margin\$|budget)/i;
const PERCENT_HINT = /(rate|ratio|pct|percent|margin|share|%)/i;

/** Word-hint the display format (mirrors sidecar `classify_measure_format`). */
function inferFormat(field: DatasourceField, role: MetricRole): MetricFormat {
  if (role !== "measure") return "text";
  const hay = `${field.name} ${field.caption ?? ""}`;
  if (PERCENT_HINT.test(hay)) return "percent";
  if (CURRENCY_HINT.test(hay)) return "currency";
  return "number";
}

function suggestAggregation(field: DatasourceField, role: MetricRole): string | undefined {
  if (field.defaultAggregation) return field.defaultAggregation;
  if (role === "measure") return "SUM";
  return undefined;
}

/**
 * Derive a metric dictionary from a field list. Order is preserved from the
 * input (stable); the same input always yields the same output.
 */
export function deriveMetricDictionary(fields: readonly DatasourceField[]): MetricDictionary {
  const metrics = fields.map((f): MetricEntry => {
    const hint: FieldHint = {
      name: f.name,
      ...(normalizeDtype(f.dataType) ? { dataType: normalizeDtype(f.dataType) } : {}),
    };
    const role = toMetricRole(classifyField(hint).role);
    const format = inferFormat(f, role);
    const aggregation = suggestAggregation(f, role);
    const label = f.caption ?? f.name;
    return {
      name: f.name,
      ...(f.caption !== undefined ? { caption: f.caption } : {}),
      role,
      ...(aggregation !== undefined ? { suggestedAggregation: aggregation } : {}),
      format,
      definitionTemplate: `${label} — ${role} (${aggregation ?? "n/a"}), formatted as ${format}.`,
    };
  });
  return { metrics, count: metrics.length };
}
