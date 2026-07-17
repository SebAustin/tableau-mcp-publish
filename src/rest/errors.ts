/**
 * Typed error taxonomy for Tableau REST + VDS calls (Phase E2 — Foundation).
 *
 * Every failed call throws a {@link TableauApiError} instead of a bare
 * `Error`, so callers (tools, tests) can branch on `status`/`code`/`retriable`
 * instead of regexing a message string.
 */

import { isRetriableStatus } from "./retry.js";

/** Tableau's documented JSON error envelope: `{"error":{"code","summary","detail"}}`. */
export interface TableauErrorBody {
  code?: string;
  summary?: string;
  detail?: string;
}

export interface TableauApiErrorParams {
  status: number;
  method: string;
  path: string;
  code?: string;
  summary?: string;
  detail?: string;
  /** Parsed from a `Retry-After` response header, when the upstream sent one. */
  retryAfterMs?: number;
}

/**
 * Structured error for a failed Tableau REST/VDS call.
 *
 * `retriable` is derived from `status` via {@link isRetriableStatus} (429,
 * 502, 503, 504 — never 401, never any other 4xx) so this one field is the
 * single source of truth callers and tests should check, instead of
 * duplicating the status-code list.
 *
 * The exception message intentionally never echoes the raw response body —
 * only status/method/path/summary — so a PAT (which is never present in a
 * response body anyway) or other upstream internals can't leak through an
 * error message that might be logged or surfaced to an agent. The full
 * upstream body is still logged to stderr by the caller for debugging.
 */
export class TableauApiError extends Error {
  readonly status: number;
  readonly method: string;
  readonly path: string;
  readonly code?: string;
  readonly summary?: string;
  readonly detail?: string;
  readonly retriable: boolean;
  readonly retryAfterMs?: number;

  constructor(params: TableauApiErrorParams) {
    const summaryPart = params.summary ? `: ${params.summary}` : "";
    super(`Tableau API request failed (${params.status}) on ${params.method} ${params.path}${summaryPart}`);
    this.name = "TableauApiError";
    this.status = params.status;
    this.method = params.method;
    this.path = params.path;
    this.code = params.code;
    this.summary = params.summary;
    this.detail = params.detail;
    this.retriable = isRetriableStatus(params.status);
    this.retryAfterMs = params.retryAfterMs;
  }
}

/**
 * Best-effort parse of Tableau's JSON error envelope. Tolerant of
 * non-JSON/malformed bodies — returns `undefined` rather than throwing, so
 * building an error can never itself fail.
 */
export function parseTableauErrorBody(text: string): TableauErrorBody | undefined {
  if (!text.trim()) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (typeof parsed !== "object" || parsed === null || !("error" in parsed)) return undefined;

  const err = (parsed as { error?: unknown }).error;
  if (typeof err !== "object" || err === null) return undefined;
  const e = err as { code?: unknown; summary?: unknown; detail?: unknown };
  return {
    code: typeof e.code === "string" ? e.code : undefined,
    summary: typeof e.summary === "string" ? e.summary : undefined,
    detail: typeof e.detail === "string" ? e.detail : undefined,
  };
}

/**
 * Parse an HTTP `Retry-After` header value (delta-seconds or an HTTP-date)
 * into milliseconds. Returns `undefined` when absent or unparseable.
 */
export function parseRetryAfterMs(
  headerValue: string | string[] | undefined,
  now: () => number = Date.now,
): number | undefined {
  const value = Array.isArray(headerValue) ? headerValue[0] : headerValue;
  if (value === undefined) return undefined;

  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;

  const dateMs = Date.parse(value);
  if (Number.isNaN(dateMs)) return undefined;
  return Math.max(0, dateMs - now());
}
