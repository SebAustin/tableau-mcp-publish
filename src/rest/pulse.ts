/**
 * Tableau Pulse metric-definition + metric client — Phase E3.
 *
 * Pulse is **Cloud-only** (Tableau Server does not have it) and lives at a
 * fixed, version-independent path — `{server}/api/-/pulse/...` — the literal
 * hyphen replaces the usual `/api/{version}/` REST API version segment, the
 * same way VDS (`rest/vds.ts`) has its own fixed `/api/v1/vizql-data-service`
 * path. Auth is the same `X-Tableau-Auth` session token as the rest of the
 * REST API, but bodies are plain **JSON**, not the XML `tsRequest` envelope
 * every other module in `src/rest/` builds.
 *
 * Endpoints (site is implied by the session token, not part of the path):
 *   Create definition: `POST   {server}/api/-/pulse/definitions`
 *   List definitions:  `GET    {server}/api/-/pulse/definitions?page_size=1000`
 *   Get definition:    `GET    {server}/api/-/pulse/definitions/{id}`
 *   Delete definition: `DELETE {server}/api/-/pulse/definitions/{id}`
 *   Create metric:     `POST   {server}/api/-/pulse/metrics`
 *   List metrics:      `GET    {server}/api/-/pulse/definitions/{id}/metrics`
 *
 * PAT scopes (from Tableau's Pulse REST API description): creating/updating a
 * definition needs `tableau:insight_definitions:create`/`update`; creating a
 * metric needs `tableau:insight_metrics:create`; reading either needs
 * `tableau:insight_definitions_metrics:read`. Creating a definition ALSO
 * requires write+publish permission on the target datasource.
 *
 * Create-definition body shape is copied EXACTLY from Tableau's official
 * `pulse-api-utilities` reference repo — including the important detail that
 * `extension_options` / `representation_options` / `insights_options` /
 * `comparisons` / `datasource_goals` / `related_links` / `certification` are
 * TOP-LEVEL siblings of `specification`, NOT nested inside it (the repo calls
 * this out explicitly — it's easy to get wrong by analogy with
 * `specification.basic_specification`).
 *
 * De-risk step (per Tableau's own guidance, and the Phase E3 plan): before
 * relying on this in production, create one definition in the Pulse UI,
 * `GET` it back, and lock the real enum tokens/shape as a fixture. Every
 * enum below beyond the few tokens explicitly confirmed in the research
 * brief is marked VERIFY-LIVE.
 *
 * Retry policy for this module (an explicit, narrower policy than the
 * shared default in `restClient.ts`'s `api()` — see its JSDoc for the
 * general per-verb reasoning): only GET is retried. POST/DELETE are never
 * retried here, even though DELETE is idempotent-by-REST-semantics
 * elsewhere in this codebase — Pulse's delete-definition response shape is
 * itself a VERIFY-LIVE unknown (see `deleteDefinition`), so this module
 * takes the more conservative stance until that's confirmed live.
 */

import { request } from "undici";
import { z } from "zod";
import { TableauApiError, parseTableauErrorBody } from "./errors.js";
import { withRetry, type RetryDeps, type RetrySignal } from "./retry.js";
import type { VdsAuth, VdsField } from "./vds.js";

const DEFINITIONS_PATH = "/api/-/pulse/definitions";
const METRICS_PATH = "/api/-/pulse/metrics";

/** Same {server, token} shape VDS uses — Pulse and VDS both bypass the versioned `/api/{v}/sites/{id}` REST path. */
export type PulseAuth = VdsAuth;

// ---------------------------------------------------------------------------
// Friendly enums (tool-facing) → wire tokens
// ---------------------------------------------------------------------------

/**
 * Confirmed by the research brief: `AGGREGATION_SUM|AVERAGE|MEDIAN|MAX|MIN|COUNT|COUNT_DISTINCT`.
 * Tool/API callers pass the bare suffix (`"SUM"`); {@link buildCreateDefinitionBody}
 * adds the `AGGREGATION_` prefix. VERIFY-LIVE: whether this set is exhaustive.
 */
export const PulseAggregationSchema = z.enum([
  "SUM",
  "AVERAGE",
  "MEDIAN",
  "MAX",
  "MIN",
  "COUNT",
  "COUNT_DISTINCT",
]);
export type PulseAggregation = z.infer<typeof PulseAggregationSchema>;

/**
 * Confirmed by the research brief: `NUMBER_FORMAT_TYPE_NUMBER|CURRENCY|PERCENT`.
 * VERIFY-LIVE: whether this set is exhaustive.
 */
export const PulseNumberFormatSchema = z.enum(["NUMBER", "CURRENCY", "PERCENT"]);
export type PulseNumberFormat = z.infer<typeof PulseNumberFormatSchema>;

/**
 * Confirmed by the research brief: `SENTIMENT_TYPE_NONE|UP_IS_GOOD|DOWN_IS_GOOD`.
 * VERIFY-LIVE: whether this set is exhaustive.
 */
export const PulseSentimentSchema = z.enum(["NONE", "UP_IS_GOOD", "DOWN_IS_GOOD"]);
export type PulseSentiment = z.infer<typeof PulseSentimentSchema>;

/**
 * VERIFY-LIVE beyond the one confirmed default (`GRANULARITY_BY_MONTH`, from
 * the research brief's sample create-metric body). The remaining tokens
 * follow the same naming convention but are NOT independently confirmed
 * against a live site — lock this against the de-risk fixture before relying
 * on anything but the default.
 */
export const PulseGranularitySchema = z.enum([
  "GRANULARITY_BY_DAY",
  "GRANULARITY_BY_WEEK",
  "GRANULARITY_BY_MONTH",
  "GRANULARITY_BY_QUARTER",
  "GRANULARITY_BY_YEAR",
  "GRANULARITY_BY_FISCAL_QUARTER", // VERIFY-LIVE — the external skill suite's Pulse-Blueprint API enum reference; unconfirmed against a live create.
  "GRANULARITY_BY_FISCAL_YEAR", // VERIFY-LIVE — the external skill suite's Pulse-Blueprint API enum reference; unconfirmed against a live create.
]);
export type PulseGranularity = z.infer<typeof PulseGranularitySchema>;

/** VERIFY-LIVE beyond the confirmed default `RANGE_LAST_COMPLETE` — see {@link PulseGranularitySchema} docs. */
export const PulseRangeSchema = z.enum(["RANGE_CURRENT_PARTIAL", "RANGE_LAST_COMPLETE", "RANGE_LAST_N_COMPLETE"]);
export type PulseRange = z.infer<typeof PulseRangeSchema>;

/** VERIFY-LIVE beyond the confirmed default `TIME_COMPARISON_PREVIOUS_PERIOD` — see {@link PulseGranularitySchema} docs. */
export const PulseComparisonSchema = z.enum([
  "TIME_COMPARISON_NONE",
  "TIME_COMPARISON_PREVIOUS_PERIOD",
  "TIME_COMPARISON_YEAR_AGO_PERIOD",
  "TIME_COMPARISON_FISCAL_YEAR_AGO_PERIOD", // VERIFY-LIVE — the external skill suite's Pulse-Blueprint API enum reference; unconfirmed against a live create.
]);
export type PulseComparison = z.infer<typeof PulseComparisonSchema>;

/**
 * VERIFY-LIVE — the external skill suite's Pulse-Blueprint confirmed API enum reference
 * (a "Currency Codes (common)" list); no member of this enum, including
 * `UNSPECIFIED`, has been confirmed against a live create.
 */
export const PulseCurrencyCodeSchema = z.enum([
  "USD", // VERIFY-LIVE
  "EUR", // VERIFY-LIVE
  "GBP", // VERIFY-LIVE
  "JPY", // VERIFY-LIVE
  "UNSPECIFIED", // VERIFY-LIVE
]);
export type PulseCurrencyCode = z.infer<typeof PulseCurrencyCodeSchema>;

/**
 * VERIFY-LIVE — the 8-value `INSIGHT_TYPE_*` family from the external skill
 * suite's Pulse-Blueprint confirmed API enum reference. Bare suffix, same
 * bare-in/prefixed-out convention as {@link PulseAggregationSchema}
 * ({@link buildCreateDefinitionBody} adds the `INSIGHT_TYPE_` prefix).
 * Unconfirmed against a live create; `insightSettings` on
 * {@link CreatePulseDefinitionInputSchema} is schema-readiness only (see
 * module docstring / ADR-0011 / ADR-0014).
 */
export const PulseInsightTypeSchema = z.enum([
  "CURRENT_TREND", // VERIFY-LIVE
  "NEW_TREND", // VERIFY-LIVE
  "TOP_DRIVERS", // VERIFY-LIVE
  "TOP_DETRACTORS", // VERIFY-LIVE
  "BOTTOM_CONTRIBUTORS", // VERIFY-LIVE
  "RISKY_MONOPOLY", // VERIFY-LIVE
  "UNUSUAL_CHANGE", // VERIFY-LIVE
  "RECORD_LEVEL_OUTLIERS", // VERIFY-LIVE
]);
export type PulseInsightType = z.infer<typeof PulseInsightTypeSchema>;

/**
 * A single insight enable/disable setting. Tool/API callers pass the bare
 * `INSIGHT_TYPE_*` suffix (e.g. `"TOP_DRIVERS"`); {@link buildCreateDefinitionBody}
 * adds the `INSIGHT_TYPE_` prefix. VERIFY-LIVE — see {@link PulseInsightTypeSchema}.
 */
export const PulseInsightSettingSchema = z.object({
  type: PulseInsightTypeSchema.describe("Bare INSIGHT_TYPE_* suffix, e.g. \"TOP_DRIVERS\"."),
  disabled: z.boolean().optional().describe("Whether this insight type is disabled for the metric."),
});
export type PulseInsightSetting = z.infer<typeof PulseInsightSettingSchema>;

/** VERIFY-LIVE — a `{singular, plural}` noun pair naming the row-level entity (e.g. "deal"/"deals"). See `rowLevelEntityNames`. */
export const PulseRowLevelEntityNamesSchema = z.object({
  singular: z.string().min(1).describe("Singular noun for one row-level entity, e.g. \"deal\"."),
  plural: z.string().min(1).describe("Plural noun for row-level entities, e.g. \"deals\"."),
});
export type PulseRowLevelEntityNames = z.infer<typeof PulseRowLevelEntityNamesSchema>;

// ---------------------------------------------------------------------------
// Create definition — input schema + wire body
// ---------------------------------------------------------------------------

/** A single Pulse specification filter. Shape is intentionally loose (VERIFY-LIVE): the research brief only confirms `filters: []`. */
export const PulseFilterSchema = z.record(z.string(), z.unknown());
export type PulseFilter = z.infer<typeof PulseFilterSchema>;

export const CreatePulseDefinitionInputSchema = z.object({
  name: z.string().min(1).describe("Display name for the Pulse metric definition."),
  datasourceLuid: z
    .string()
    .min(1)
    .describe("LUID of a single PUBLISHED datasource with a real date dimension."),
  measure: z.object({
    field: z.string().min(1).describe("Measure field name/caption on the datasource, e.g. \"Sales\"."),
    aggregation: PulseAggregationSchema.describe("Aggregation applied to the measure."),
  }),
  timeDimension: z.object({
    field: z.string().min(1).describe("Date-like field name/caption, e.g. \"Order Date\"."),
  }),
  filters: z.array(PulseFilterSchema).default([]).describe("Optional specification filters (shape VERIFY-LIVE)."),
  allowedDimensions: z
    .array(z.string())
    .default([])
    .describe("Dimension field names Pulse may use for insight breakdowns/correlations."),
  numberFormat: PulseNumberFormatSchema.default("NUMBER").describe("Display format for the metric value."),
  sentiment: PulseSentimentSchema.default("NONE").describe("Whether up/down movement reads as good or bad."),
  isRunningTotal: z.boolean().default(false).describe("Whether the metric accumulates as a running total."),
  currencyCode: PulseCurrencyCodeSchema.optional().describe(
    "VERIFY-LIVE, optional: currency code for representation_options when numberFormat is CURRENCY " +
      "(e.g. \"USD\"). Omitted by default — additive, does not change the wire body when unset.",
  ),
  insightSettings: z
    .array(PulseInsightSettingSchema)
    .optional()
    .describe(
      "VERIFY-LIVE, optional: per-insight-type enable/disable settings for insights_options.settings. " +
        "Omitted by default (empty settings array, matching prior behavior).",
    ),
  rowLevelIdField: z
    .string()
    .min(1)
    .optional()
    .describe(
      "VERIFY-LIVE, optional: identifier column enabling row-level outlier detection " +
        "(INSIGHT_TYPE_RECORD_LEVEL_OUTLIERS), e.g. \"Order ID\".",
    ),
  rowLevelNameField: z
    .string()
    .min(1)
    .optional()
    .describe("VERIFY-LIVE, optional: human-readable label column for row-level entities, e.g. \"Order Name\"."),
  rowLevelEntityNames: PulseRowLevelEntityNamesSchema.optional().describe(
    "VERIFY-LIVE, optional: singular/plural noun pair naming the row-level entity, e.g. {singular: \"order\", plural: \"orders\"}.",
  ),
});
export type CreatePulseDefinitionInput = z.infer<typeof CreatePulseDefinitionInputSchema>;

/** The exact wire body shape, mirroring Tableau's official `pulse-api-utilities` reference. */
export interface PulseDefinitionRequestBody {
  name: string;
  specification: {
    basic_specification: {
      measure: { field: string; aggregation: string };
      time_dimension: { field: string };
      filters: PulseFilter[];
    };
    is_running_total: boolean;
    datasource: { id: string };
    /** VERIFY-LIVE — schema-readiness only; see the external skill suite's Pulse-Blueprint reference / ADR-0014. */
    row_level_id_field?: string;
    /** VERIFY-LIVE — schema-readiness only; see the external skill suite's Pulse-Blueprint reference / ADR-0014. */
    row_level_name_field?: string;
    /** VERIFY-LIVE — schema-readiness only; see the external skill suite's Pulse-Blueprint reference / ADR-0014. */
    row_level_entity_names?: { singular_noun: string; plural_noun: string };
  };
  extension_options: {
    allowed_dimensions: string[];
    allowed_granularities: string[];
    offset_from_today: number;
    correlation_candidate_definition_ids: string[];
    use_dynamic_offset: boolean;
  };
  representation_options: {
    type: string;
    sentiment_type: string;
    /** VERIFY-LIVE — schema-readiness only; see the external skill suite's Pulse-Blueprint reference / ADR-0014. */
    currency_code?: string;
  };
  insights_options: { show_insights: boolean; settings: { type: string; disabled?: boolean }[] };
  comparisons: { comparisons: unknown[] };
  datasource_goals: unknown[];
  related_links: unknown[];
  certification: { is_certified: boolean };
}

/**
 * Builds the create-definition JSON body. `extension_options` /
 * `representation_options` / `insights_options` / `comparisons` /
 * `datasource_goals` / `related_links` / `certification` are TOP-LEVEL,
 * siblings of `specification` — see the module docstring.
 *
 * `currencyCode` / `insightSettings` / `rowLevel*` (external skill enhancement
 * #5 — schema-readiness only, VERIFY-LIVE) are ADDITIVE: when omitted, the
 * output is byte-for-byte identical to before this widening.
 */
export function buildCreateDefinitionBody(input: CreatePulseDefinitionInput): PulseDefinitionRequestBody {
  return {
    name: input.name,
    specification: {
      basic_specification: {
        measure: { field: input.measure.field, aggregation: `AGGREGATION_${input.measure.aggregation}` },
        time_dimension: { field: input.timeDimension.field },
        filters: input.filters,
      },
      is_running_total: input.isRunningTotal,
      datasource: { id: input.datasourceLuid },
      ...(input.rowLevelIdField !== undefined ? { row_level_id_field: input.rowLevelIdField } : {}),
      ...(input.rowLevelNameField !== undefined ? { row_level_name_field: input.rowLevelNameField } : {}),
      ...(input.rowLevelEntityNames !== undefined
        ? {
            row_level_entity_names: {
              singular_noun: input.rowLevelEntityNames.singular,
              plural_noun: input.rowLevelEntityNames.plural,
            },
          }
        : {}),
    },
    extension_options: {
      allowed_dimensions: input.allowedDimensions,
      allowed_granularities: [],
      offset_from_today: 0,
      correlation_candidate_definition_ids: [],
      use_dynamic_offset: false,
    },
    representation_options: {
      type: `NUMBER_FORMAT_TYPE_${input.numberFormat}`,
      sentiment_type: `SENTIMENT_TYPE_${input.sentiment}`,
      ...(input.currencyCode !== undefined ? { currency_code: `CURRENCY_CODE_${input.currencyCode}` } : {}),
    },
    insights_options: {
      show_insights: true,
      settings: (input.insightSettings ?? []).map((setting) => ({
        type: `INSIGHT_TYPE_${setting.type}`,
        ...(setting.disabled !== undefined ? { disabled: setting.disabled } : {}),
      })),
    },
    comparisons: { comparisons: [] },
    datasource_goals: [],
    related_links: [],
    certification: { is_certified: false },
  };
}

// ---------------------------------------------------------------------------
// Create metric — input schema + wire body
// ---------------------------------------------------------------------------

export const CreatePulseMetricInputSchema = z.object({
  definitionId: z.string().min(1).describe("id of an existing Pulse metric definition."),
  filters: z.array(PulseFilterSchema).default([]).describe("Optional specification filters (shape VERIFY-LIVE)."),
  granularity: PulseGranularitySchema.default("GRANULARITY_BY_MONTH"),
  range: PulseRangeSchema.default("RANGE_LAST_COMPLETE"),
  comparison: PulseComparisonSchema.default("TIME_COMPARISON_PREVIOUS_PERIOD"),
});
export type CreatePulseMetricInput = z.infer<typeof CreatePulseMetricInputSchema>;

export interface PulseMetricRequestBody {
  definition_id: string;
  specification: {
    filters: PulseFilter[];
    measurement_period: { granularity: string; range: string };
    comparison: { comparison: string };
  };
}

/** Builds the create-metric JSON body, mirroring the research brief's sample exactly. */
export function buildCreateMetricBody(input: CreatePulseMetricInput): PulseMetricRequestBody {
  return {
    definition_id: input.definitionId,
    specification: {
      filters: input.filters,
      measurement_period: { granularity: input.granularity, range: input.range },
      comparison: { comparison: input.comparison },
    },
  };
}

// ---------------------------------------------------------------------------
// Response parsing
// ---------------------------------------------------------------------------

export interface PulseDefinition {
  definitionId: string;
  name: string;
  datasourceLuid?: string;
}

export interface PulseMetric {
  metricId: string;
  definitionId?: string;
}

interface RawPulseDefinition {
  id?: unknown;
  name?: unknown;
  specification?: { datasource?: { id?: unknown } };
}

interface RawPulseMetric {
  id?: unknown;
  definition_id?: unknown;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function toDefinition(raw: RawPulseDefinition): PulseDefinition {
  const definitionId = optionalString(raw.id);
  const name = optionalString(raw.name);
  if (!definitionId || !name) {
    throw new Error("Malformed Pulse definition response: missing id or name.");
  }
  const datasourceLuid = optionalString(raw.specification?.datasource?.id);
  return {
    definitionId,
    name,
    ...(datasourceLuid !== undefined ? { datasourceLuid } : {}),
  };
}

/**
 * Parses a single-definition response. The exact wrapper key is unconfirmed
 * (VERIFY-LIVE) — tolerates a bare object (`{id, name, ...}`) as well as a
 * `{ metric_definition: {...} }` wrapper, mirroring the tolerant style used
 * for every other REST module in this project.
 */
export function parseDefinition(json: unknown): PulseDefinition {
  const obj = json as { metric_definition?: RawPulseDefinition; id?: unknown };
  const raw = obj.metric_definition ?? (obj.id !== undefined ? (obj as RawPulseDefinition) : undefined);
  if (!raw) {
    throw new Error("Malformed Pulse definition response: missing definition.");
  }
  return toDefinition(raw);
}

/**
 * Parses the list-definitions response. Per the task brief, tolerates BOTH a
 * `{ metric_definitions: [...] }` wrapper and a bare top-level array.
 */
export function parseDefinitionList(json: unknown): PulseDefinition[] {
  const list = Array.isArray(json)
    ? json
    : ((json as { metric_definitions?: RawPulseDefinition[] })?.metric_definitions ?? []);
  return list.map(toDefinition);
}

function toMetric(raw: RawPulseMetric): PulseMetric {
  const metricId = optionalString(raw.id);
  if (!metricId) {
    throw new Error("Malformed Pulse metric response: missing id.");
  }
  const definitionId = optionalString(raw.definition_id);
  return {
    metricId,
    ...(definitionId !== undefined ? { definitionId } : {}),
  };
}

/** Parses a single-metric response — same bare-object-or-wrapper tolerance as {@link parseDefinition}. */
export function parseMetric(json: unknown): PulseMetric {
  const obj = json as { metric?: RawPulseMetric; id?: unknown };
  const raw = obj.metric ?? (obj.id !== undefined ? (obj as RawPulseMetric) : undefined);
  if (!raw) {
    throw new Error("Malformed Pulse metric response: missing metric.");
  }
  return toMetric(raw);
}

/** Parses the list-metrics response — tolerates both `{ metrics: [...] }` and a bare array. */
export function parseMetricList(json: unknown): PulseMetric[] {
  const list = Array.isArray(json) ? json : ((json as { metrics?: RawPulseMetric[] })?.metrics ?? []);
  return list.map(toMetric);
}

// ---------------------------------------------------------------------------
// Pre-flight validation (VDS-backed)
// ---------------------------------------------------------------------------

const DATE_LIKE_RE = /DATE/i;

export interface PulsePreflightResult {
  /** Soft findings — creation proceeds, but these should be surfaced to the caller. */
  warnings: string[];
}

function findField(fields: VdsField[], name: string): VdsField | undefined {
  return fields.find((f) => f.fieldName === name || f.fieldCaption === name);
}

function fieldList(fields: VdsField[]): string {
  return fields.map((f) => f.fieldCaption ?? f.fieldName).join(", ") || "(none)";
}

/**
 * Verifies a Pulse definition's `measure`/`timeDimension` fields against
 * REAL VDS field metadata (see `get_datasource_fields` / `rest/vds.ts`).
 *
 * Hard failures (throws, listing every available field so the caller can
 * self-correct without a second round trip):
 *   - the measure field does not exist on the datasource;
 *   - the time_dimension field does not exist on the datasource;
 *   - the time_dimension field exists but its VDS `dataType` is reported and
 *     is not date-like (Pulse requires a real date dimension).
 *
 * Soft finding (returned as a warning, does not block creation):
 *   - the measure field's VDS `defaultAggregation` disagrees with the
 *     requested aggregation (compared case-insensitively).
 */
export function validatePulsePreflight(
  fields: VdsField[],
  measure: { field: string; aggregation: string },
  timeDimension: { field: string },
): PulsePreflightResult {
  const warnings: string[] = [];

  const measureField = findField(fields, measure.field);
  if (!measureField) {
    throw new Error(
      `Pulse pre-flight failed: measure field "${measure.field}" was not found on the datasource. ` +
        `Available fields: ${fieldList(fields)}.`,
    );
  }
  if (
    measureField.defaultAggregation !== undefined &&
    measureField.defaultAggregation.toUpperCase() !== measure.aggregation.toUpperCase()
  ) {
    warnings.push(
      `Measure "${measure.field}" has a VDS defaultAggregation of "${measureField.defaultAggregation}", ` +
        `which differs from the requested "${measure.aggregation}". Confirm this is intentional.`,
    );
  }

  const timeField = findField(fields, timeDimension.field);
  if (!timeField) {
    throw new Error(
      `Pulse pre-flight failed: time_dimension field "${timeDimension.field}" was not found on the ` +
        `datasource. Available fields: ${fieldList(fields)}.`,
    );
  }
  if (timeField.dataType === undefined) {
    warnings.push(
      `VDS did not report a dataType for time_dimension field "${timeDimension.field}"; proceeding ` +
        "without confirming it is a real date dimension.",
    );
  } else if (!DATE_LIKE_RE.test(timeField.dataType)) {
    const dateLikeFields = fields.filter((f) => f.dataType !== undefined && DATE_LIKE_RE.test(f.dataType));
    throw new Error(
      `Pulse pre-flight failed: time_dimension field "${timeDimension.field}" has dataType ` +
        `"${timeField.dataType}", which is not date-like. Pulse requires a real date dimension. ` +
        `Date-like fields available: ${fieldList(dateLikeFields)}.`,
    );
  }

  return { warnings };
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

type PulseResourceKind = "definition" | "metric";

/** Heuristic: does this error body suggest Pulse itself isn't enabled for the site (vs. a not-found id)? */
function isPulseDisabledMessage(text: string): boolean {
  return /pulse.{0,30}(disab|not enabled)|feature.{0,20}(off|unavailable)/i.test(text);
}

function buildPulseError(
  status: number,
  text: string,
  resourceKind: PulseResourceKind,
  method: string,
  path: string,
): TableauApiError {
  if (status === 404) {
    return new TableauApiError({
      status,
      method,
      path,
      summary: `Pulse ${resourceKind} not found, or Pulse is unavailable on this site`,
      detail:
        "Tableau Pulse is Cloud-only (Tableau Server does not support it) and must be enabled for " +
        "the site under Site Settings > Pulse. Confirm the id, that this server is a Cloud site, " +
        "and that Pulse is turned on before retrying.",
    });
  }

  const parsed = parseTableauErrorBody(text);
  const disabledHint = isPulseDisabledMessage(`${parsed?.summary ?? ""} ${parsed?.detail ?? ""} ${text}`)
    ? " Tableau Pulse is not enabled for this site — ask a site admin to enable it under Site " +
      "Settings > Pulse (Cloud-only feature)."
    : "";

  return new TableauApiError({
    status,
    method,
    path,
    code: parsed?.code,
    summary: (parsed?.summary ?? `Pulse ${resourceKind} request failed`) + disabledHint,
    detail: parsed?.detail,
  });
}

interface PulseRequestOptions {
  body?: string;
  idempotent: boolean;
  parse?: "json" | "none";
  resourceKind: PulseResourceKind;
}

async function pulseRequest(
  auth: PulseAuth,
  method: string,
  path: string,
  opts: PulseRequestOptions,
  retryDeps: RetryDeps,
): Promise<unknown> {
  const url = `${auth.server}${path}`;
  const { body, idempotent, parse = "json", resourceKind } = opts;

  return withRetry<unknown>(
    async () => {
      const res = await request(url, {
        method,
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Tableau-Auth": auth.token,
        },
        ...(body !== undefined ? { body } : {}),
      });

      if (res.statusCode >= 400) {
        const text = await res.body.text();
        process.stderr.write(`[tableau-mcp-publish] Pulse ${method} ${path} ${res.statusCode}: ${text}\n`);
        throw buildPulseError(res.statusCode, text, resourceKind, method, path);
      }
      if (parse === "none") {
        await res.body.text();
        return undefined;
      }
      return res.body.json();
    },
    (err: unknown): RetrySignal | undefined =>
      err instanceof TableauApiError ? { status: err.status, retryAfterMs: err.retryAfterMs } : undefined,
    idempotent,
    undefined,
    retryDeps,
  );
}

// ---------------------------------------------------------------------------
// Public client functions
// ---------------------------------------------------------------------------

/** `POST /api/-/pulse/definitions`. Not retried: POST is non-idempotent (see module docstring). */
export async function createDefinition(
  auth: PulseAuth,
  input: CreatePulseDefinitionInput,
  retryDeps: RetryDeps = {},
): Promise<PulseDefinition> {
  const validated = CreatePulseDefinitionInputSchema.parse(input);
  const body = JSON.stringify(buildCreateDefinitionBody(validated));
  const json = await pulseRequest(
    auth,
    "POST",
    DEFINITIONS_PATH,
    { body, idempotent: false, resourceKind: "definition" },
    retryDeps,
  );
  return parseDefinition(json);
}

/** `GET /api/-/pulse/definitions?page_size=1000`. Read-only — retriable. */
export async function listDefinitions(auth: PulseAuth, retryDeps: RetryDeps = {}): Promise<PulseDefinition[]> {
  const json = await pulseRequest(
    auth,
    "GET",
    `${DEFINITIONS_PATH}?page_size=1000`,
    { idempotent: true, resourceKind: "definition" },
    retryDeps,
  );
  return parseDefinitionList(json);
}

/** `GET /api/-/pulse/definitions/{id}`. Read-only — retriable. */
export async function getDefinition(
  auth: PulseAuth,
  definitionId: string,
  retryDeps: RetryDeps = {},
): Promise<PulseDefinition> {
  if (!definitionId.trim()) {
    throw new Error("getDefinition: definitionId is required.");
  }
  const json = await pulseRequest(
    auth,
    "GET",
    `${DEFINITIONS_PATH}/${encodeURIComponent(definitionId)}`,
    { idempotent: true, resourceKind: "definition" },
    retryDeps,
  );
  return parseDefinition(json);
}

/**
 * `DELETE /api/-/pulse/definitions/{id}`. VERIFY-LIVE note (per the plan):
 * not retried even on a transient failure — unlike DELETE elsewhere in this
 * codebase — because this endpoint's exact behavior (whether a definition
 * with live metrics can be deleted, the exact response shape) has not yet
 * been confirmed against a live site.
 */
export async function deleteDefinition(
  auth: PulseAuth,
  definitionId: string,
  retryDeps: RetryDeps = {},
): Promise<void> {
  if (!definitionId.trim()) {
    throw new Error("deleteDefinition: definitionId is required.");
  }
  await pulseRequest(
    auth,
    "DELETE",
    `${DEFINITIONS_PATH}/${encodeURIComponent(definitionId)}`,
    { idempotent: false, parse: "none", resourceKind: "definition" },
    retryDeps,
  );
}

/** `POST /api/-/pulse/metrics`. Not retried: POST is non-idempotent (see module docstring). */
export async function createMetric(
  auth: PulseAuth,
  input: CreatePulseMetricInput,
  retryDeps: RetryDeps = {},
): Promise<PulseMetric> {
  const validated = CreatePulseMetricInputSchema.parse(input);
  const body = JSON.stringify(buildCreateMetricBody(validated));
  const json = await pulseRequest(
    auth,
    "POST",
    METRICS_PATH,
    { body, idempotent: false, resourceKind: "metric" },
    retryDeps,
  );
  return parseMetric(json);
}

/** `GET /api/-/pulse/definitions/{id}/metrics`. Read-only — retriable. */
export async function listMetrics(
  auth: PulseAuth,
  definitionId: string,
  retryDeps: RetryDeps = {},
): Promise<PulseMetric[]> {
  if (!definitionId.trim()) {
    throw new Error("listMetrics: definitionId is required.");
  }
  const json = await pulseRequest(
    auth,
    "GET",
    `${DEFINITIONS_PATH}/${encodeURIComponent(definitionId)}/metrics`,
    { idempotent: true, resourceKind: "metric" },
    retryDeps,
  );
  return parseMetricList(json);
}
