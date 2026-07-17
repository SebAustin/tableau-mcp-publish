/**
 * Cloud extract-refresh SCHEDULING — Phase E2 slice B.
 *
 * Tableau Cloud does **not** support classic Server-style shared schedules
 * over REST. Instead, each extract-refresh task carries its own **embedded**
 * schedule (frequency + frequencyDetails + intervals) directly on the task
 * resource:
 *
 *   Create: `POST /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes` (API >= 3.20)
 *   Update: `POST /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes/{taskLuid}` (same body shape)
 *   List:   `GET  /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes`
 *   Get:    `GET  /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes/{taskId}`
 *   Delete: `DELETE /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes/{taskId}`
 *   Run now: `POST /api/{ver}/sites/{siteLuid}/tasks/extractRefreshes/{taskId}/runNow`
 *
 * `runNow` is distinct from {@link TableauRestClient.refreshDatasource}
 * (`POST …/datasources/{id}/refresh`, in `restClient.ts`): `refreshDatasource`
 * triggers a one-off refresh directly on the datasource with no schedule or
 * task involved, while `runNow` runs an *existing scheduled task*
 * immediately, out of band from its recurring cadence. Both are kept.
 *
 * This module owns:
 *   - zod validation of a caller-supplied schedule spec (frequency-specific
 *     interval rules), with a single actionable error listing every problem;
 *   - the exact `tsRequest` XML bodies for create/update/runNow;
 *   - response parsing back into a normalized {@link ExtractRefreshTask}.
 *
 * IMPORTANT connectivity nuance (also surfaced at the `schedule_refresh` tool
 * layer via its `note` output field): Cloud can only *execute* a scheduled
 * refresh for a connection that is cloud-reachable with embedded
 * credentials (e.g. Snowflake, a direct cloud database). A datasource
 * published from a local file (CSV/Excel/local Hyper extract) accepts a
 * schedule-creation call just fine, but Cloud cannot itself re-query a local
 * file without Tableau Bridge — the schedule is created successfully yet its
 * runs will fail. We cannot reliably detect "has embedded, cloud-reachable
 * credentials" from the create/list REST response, so callers should always
 * surface this caveat rather than only surfacing it conditionally.
 *
 * VERIFY-LIVE items (documented per the Phase E2 plan, not yet confirmed
 * against a live Cloud site):
 *   1. The exact `IncrementalRefresh` token spelling — reference material is
 *      inconsistent between `IncrementalRefresh` and `IncrementalExtract`.
 *      This module defaults every caller to `FullRefresh` and only emits
 *      `IncrementalRefresh` when explicitly requested.
 *   2. Whether the create/update response wraps the task as
 *      `<task><extractRefresh>…</extractRefresh></task>` (mirroring the list
 *      shape) or returns a bare `<extractRefresh>…</extractRefresh>` at the
 *      top level — {@link parseExtractRefreshTaskResponse} tolerates both.
 *   3. The exact attribute name (`nextRunAt`) and presence of the computed
 *      next-run time on the response `<schedule>` element.
 *   4. The full set of allowed `hours` values for an Hourly interval (Server
 *      docs suggest a fixed set like 1/2/4/6/8/12, but Cloud's per-task
 *      embedded schedules are not confirmed to share that exact list) — this
 *      module validates numeric format only, not a fixed allowlist.
 */

import { z } from "zod";
import { xmlEscape, asArray } from "./xml.js";

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

export const ScheduleTargetKindSchema = z.enum(["datasource", "workbook"]);
export type ScheduleTargetKind = z.infer<typeof ScheduleTargetKindSchema>;

/** What an extract-refresh task refreshes: a published datasource or a workbook's embedded extracts. */
export interface ScheduleTarget {
  kind: ScheduleTargetKind;
  id: string;
}

/**
 * `FullRefresh` is the token confirmed by the official REST API reference.
 * `IncrementalRefresh` is this module's best-effort spelling for incremental
 * extracts — VERIFY-LIVE before relying on it in production (see module docstring).
 */
export const ExtractRefreshTypeSchema = z.enum(["FullRefresh", "IncrementalRefresh"]);
export type ExtractRefreshType = z.infer<typeof ExtractRefreshTypeSchema>;

export const FrequencySchema = z.enum(["Hourly", "Daily", "Weekly", "Monthly"]);
export type Frequency = z.infer<typeof FrequencySchema>;

export const WeekDaySchema = z.enum([
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
]);
export type WeekDay = z.infer<typeof WeekDaySchema>;

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/;
const timeSchema = z.string().regex(TIME_RE, 'must be 24-hour "HH:MM:SS", e.g. "03:30:00"');

const HOURS_RE = /^\d+(\.\d+)?$/;
const MONTH_DAY_RE = /^([1-9]|[12]\d|3[01]|LastDay)$/;

export const IntervalSchema = z.object({
  hours: z.string().regex(HOURS_RE, 'hours must be numeric, e.g. "1", "4", "12"').optional(),
  weekDay: WeekDaySchema.optional(),
  monthDay: z.string().regex(MONTH_DAY_RE, 'monthDay must be "1"-"31" or "LastDay"').optional(),
});
export type Interval = z.infer<typeof IntervalSchema>;

export const FrequencyDetailsSchema = z.object({
  start: timeSchema.describe('Local start time, "HH:MM:SS".'),
  end: timeSchema.optional().describe("Optional end time bounding a repeating Hourly window."),
  intervals: z
    .array(IntervalSchema)
    .default([])
    .describe(
      "Interval list: Hourly needs >=1 {hours}, Weekly needs >=1 {weekDay}, Monthly needs " +
        "exactly one {monthDay}, Daily needs none.",
    ),
});
export type FrequencyDetails = z.infer<typeof FrequencyDetailsSchema>;

export const ScheduleSpecSchema = z.object({
  frequency: FrequencySchema,
  frequencyDetails: FrequencyDetailsSchema,
});
export type ScheduleSpec = z.infer<typeof ScheduleSpecSchema>;

/**
 * Frequency-specific cross-field validation that a flat zod schema can't
 * express cleanly: Hourly needs >=1 `hours` interval, Weekly needs >=1
 * `weekDay` interval, Monthly needs exactly one `monthDay` interval, Daily
 * needs no intervals, and every interval must set exactly one of
 * {hours, weekDay, monthDay}. Throws a single actionable `Error` (not a
 * `ZodError`) listing every problem found, so a caller/agent gets the full
 * picture in one round trip.
 */
export function validateScheduleSpec(spec: ScheduleSpec): ScheduleSpec {
  const problems: string[] = [];
  const { frequency, frequencyDetails } = spec;
  const { intervals } = frequencyDetails;

  intervals.forEach((interval, i) => {
    const set = (["hours", "weekDay", "monthDay"] as const).filter((k) => interval[k] !== undefined);
    if (set.length !== 1) {
      problems.push(
        `intervals[${i}] must set exactly one of hours, weekDay, or monthDay (got: ${
          set.length > 0 ? set.join(", ") : "none"
        }).`,
      );
    }
  });

  if (frequency === "Hourly") {
    if (intervals.filter((iv) => iv.hours !== undefined).length === 0) {
      problems.push('Hourly frequency requires at least one interval with "hours" set.');
    }
  } else if (frequency === "Weekly") {
    if (intervals.filter((iv) => iv.weekDay !== undefined).length === 0) {
      problems.push('Weekly frequency requires at least one interval with "weekDay" set.');
    }
  } else if (frequency === "Monthly") {
    if (intervals.filter((iv) => iv.monthDay !== undefined).length !== 1) {
      problems.push('Monthly frequency requires exactly one interval with "monthDay" set.');
    }
  }
  // Daily: frequencyDetails.start alone is sufficient; intervals are not required.

  if (problems.length > 0) {
    throw new Error(
      `Invalid schedule spec for frequency "${frequency}":\n${problems.map((p) => `  - ${p}`).join("\n")}`,
    );
  }
  return spec;
}

// ---------------------------------------------------------------------------
// Request XML
// ---------------------------------------------------------------------------

function targetXml(target: ScheduleTarget): string {
  const elem = target.kind === "datasource" ? "datasource" : "workbook";
  return `<${elem} id="${xmlEscape(target.id)}" />`;
}

function intervalXml(interval: Interval): string {
  if (interval.hours !== undefined) return `<interval hours="${xmlEscape(interval.hours)}" />`;
  if (interval.weekDay !== undefined) return `<interval weekDay="${interval.weekDay}" />`;
  if (interval.monthDay !== undefined) return `<interval monthDay="${xmlEscape(interval.monthDay)}" />`;
  throw new Error("intervalXml: interval must set hours, weekDay, or monthDay.");
}

function scheduleXml(spec: ScheduleSpec): string {
  const { frequency, frequencyDetails } = spec;
  const endAttr = frequencyDetails.end ? ` end="${frequencyDetails.end}"` : "";
  const intervalsXml =
    frequencyDetails.intervals.length > 0
      ? `<intervals>${frequencyDetails.intervals.map(intervalXml).join("")}</intervals>`
      : "";
  return (
    `<schedule frequency="${frequency}">` +
    `<frequencyDetails start="${frequencyDetails.start}"${endAttr}>${intervalsXml}</frequencyDetails>` +
    `</schedule>`
  );
}

/**
 * Body for both create (`POST …/tasks/extractRefreshes`) and update
 * (`POST …/tasks/extractRefreshes/{taskId}`) — the documented shape is
 * identical for both. `<schedule>` is a SIBLING of `<extractRefresh>`, not
 * nested inside it — matches the verified research brief exactly.
 */
export function buildExtractRefreshTaskXml(
  target: ScheduleTarget,
  type: ExtractRefreshType,
  spec: ScheduleSpec,
): string {
  return (
    `<tsRequest><extractRefresh type="${type}">${targetXml(target)}</extractRefresh>` +
    scheduleXml(spec) +
    `</tsRequest>`
  );
}

/** `runNow` sends an empty envelope, mirroring `refreshDatasource`'s POST body in `restClient.ts`. */
export const RUN_NOW_BODY = "<tsRequest></tsRequest>";

// ---------------------------------------------------------------------------
// Response parsing
// ---------------------------------------------------------------------------

/** Normalized extract-refresh task, flattened from the nested `extractRefresh`/`schedule` response shape. */
export interface ExtractRefreshTask {
  taskId: string;
  type: string;
  targetKind?: ScheduleTargetKind;
  targetId?: string;
  frequency?: string;
  /** VERIFY-LIVE: exact attribute name/presence unconfirmed against a live site — see module docstring. */
  nextRunAt?: string;
  start?: string;
  end?: string;
}

interface RawInterval {
  hours?: unknown;
  weekDay?: unknown;
  monthDay?: unknown;
}

interface RawSchedule {
  frequency?: unknown;
  nextRunAt?: unknown;
  frequencyDetails?: {
    start?: unknown;
    end?: unknown;
    intervals?: { interval?: RawInterval | RawInterval[] };
  };
}

interface RawExtractRefresh {
  id?: unknown;
  type?: unknown;
  datasource?: { id?: unknown };
  workbook?: { id?: unknown };
  schedule?: RawSchedule;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Spreads `{ [key]: value }` only when `value` is defined — keeps optional fields truly absent instead of `undefined`. */
function optionalField<K extends string, V>(key: K, value: V | undefined): { [P in K]?: V } {
  return value !== undefined ? ({ [key]: value } as { [P in K]?: V }) : {};
}

function toExtractRefreshTask(raw: RawExtractRefresh): ExtractRefreshTask {
  const taskId = optionalString(raw.id);
  if (!taskId) {
    throw new Error("Malformed extract-refresh task response: missing id.");
  }
  const targetKind: ScheduleTargetKind | undefined = raw.datasource
    ? "datasource"
    : raw.workbook
      ? "workbook"
      : undefined;
  const targetId = optionalString(raw.datasource?.id) ?? optionalString(raw.workbook?.id);
  const frequency = optionalString(raw.schedule?.frequency);
  const nextRunAt = optionalString(raw.schedule?.nextRunAt);
  const start = optionalString(raw.schedule?.frequencyDetails?.start);
  const end = optionalString(raw.schedule?.frequencyDetails?.end);

  return {
    taskId,
    type: optionalString(raw.type) ?? "FullRefresh",
    ...optionalField("targetKind", targetKind),
    ...optionalField("targetId", targetId),
    ...optionalField("frequency", frequency),
    ...optionalField("nextRunAt", nextRunAt),
    ...optionalField("start", start),
    ...optionalField("end", end),
  };
}

/**
 * Parses a single-task response. Accepts either a bare
 * `{ extractRefresh }` or a `{ task: { extractRefresh } }` wrapper — which
 * shape the real API returns for create/update/get is a VERIFY-LIVE item
 * (see module docstring), so both are tolerated.
 */
export function parseExtractRefreshTaskResponse(json: unknown): ExtractRefreshTask {
  const obj = json as {
    extractRefresh?: RawExtractRefresh;
    task?: { extractRefresh?: RawExtractRefresh };
  };
  const raw = obj.extractRefresh ?? obj.task?.extractRefresh;
  if (!raw) {
    throw new Error("Malformed extract-refresh task response: missing extractRefresh.");
  }
  return toExtractRefreshTask(raw);
}

/** Parses the `GET …/tasks/extractRefreshes` list response: `{ tasks: { task: [...] } }`. */
export function parseExtractRefreshTaskList(json: unknown): ExtractRefreshTask[] {
  const obj = json as {
    tasks?: { task?: Array<{ extractRefresh?: RawExtractRefresh }> | { extractRefresh?: RawExtractRefresh } };
  };
  return asArray(obj.tasks?.task)
    .map((t) => t.extractRefresh)
    .filter((r): r is RawExtractRefresh => r !== undefined)
    .map(toExtractRefreshTask);
}
