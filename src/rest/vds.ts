/**
 * VizQL Data Service (VDS) field-metadata client — Phase E2, Foundation slice.
 *
 * VDS is Tableau's runtime query/metadata service that sits in front of a
 * published datasource. Unlike the classic REST API it speaks JSON over a
 * fixed `/api/v1/vizql-data-service/*` path (independent of the REST API
 * version negotiated at sign-in), and `read-metadata` is the only officially
 * supported way to discover a datasource's REAL field names, data types, and
 * default aggregations without opening it in Desktop/Web Authoring.
 *
 * This feeds `get_datasource_fields` (planner `fieldHints`) and — in Phase
 * E3 — Pulse pre-flight validation.
 */

import { request } from "undici";
import { TableauApiError, parseTableauErrorBody } from "./errors.js";
import { withRetry, type RetryDeps, type RetrySignal } from "./retry.js";

const READ_METADATA_PATH = "/api/v1/vizql-data-service/read-metadata";

export interface VdsAuth {
  /** Full Tableau Cloud/Server URL, e.g. https://10ax.online.tableau.com */
  server: string;
  /** Active session token (the same one sent as X-Tableau-Auth to REST). */
  token: string;
}

/** A single field as reported by VDS read-metadata, normalized for callers. */
export interface VdsField {
  fieldName: string;
  fieldCaption?: string;
  dataType?: string;
  defaultAggregation?: string;
  logicalTableId?: string;
}

interface RawVdsField {
  fieldName?: unknown;
  fieldCaption?: unknown;
  dataType?: unknown;
  defaultAggregation?: unknown;
  logicalTableId?: unknown;
}

interface VdsMetadataResponse {
  data?: RawVdsField[];
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Map one raw VDS field to our normalized shape. Tolerates absent optional fields. */
function toVdsField(raw: RawVdsField, index: number): VdsField {
  const fieldName = optionalString(raw.fieldName);
  if (!fieldName) {
    throw new Error(
      `readDatasourceMetadata: malformed response — field at index ${index} is missing fieldName.`,
    );
  }
  return {
    fieldName,
    fieldCaption: optionalString(raw.fieldCaption),
    dataType: optionalString(raw.dataType),
    defaultAggregation: optionalString(raw.defaultAggregation),
    logicalTableId: optionalString(raw.logicalTableId),
  };
}

/** Heuristic: does this error body suggest VDS itself isn't enabled for the site? */
function isFeatureDisabledMessage(text: string): boolean {
  return /disab|not enabled|feature.{0,20}(off|unavailable)/i.test(text);
}

function buildVdsError(status: number, text: string): TableauApiError {
  if (status === 404) {
    return new TableauApiError({
      status,
      method: "POST",
      path: READ_METADATA_PATH,
      summary: "Datasource not found or VDS unavailable",
      detail:
        "The datasourceLuid was not found on this site, or the VizQL Data Service is not " +
        "reachable for it. Confirm the LUID (from publish_datasource/list_content) and that " +
        "the datasource is published (not a local/embedded-only extract).",
    });
  }

  const parsed = parseTableauErrorBody(text);
  const disabledHint = isFeatureDisabledMessage(`${parsed?.summary ?? ""} ${parsed?.detail ?? ""} ${text}`)
    ? " VizQL Data Service is not enabled for this site — ask a site admin to enable it, " +
      "or fall back to inspecting the published datasource's columns directly."
    : "";

  return new TableauApiError({
    status,
    method: "POST",
    path: READ_METADATA_PATH,
    code: parsed?.code,
    summary: (parsed?.summary ?? "VizQL Data Service request failed") + disabledHint,
    detail: parsed?.detail,
  });
}

/**
 * `POST {server}/api/v1/vizql-data-service/read-metadata`
 *
 * Read-only despite the POST verb: `read-metadata` has no observable side
 * effects, so — unlike the mutating POSTs in `restClient.ts` — it is safe to
 * retry on 429/502/503/504 (`idempotent: true` below).
 */
export async function readDatasourceMetadata(
  auth: VdsAuth,
  datasourceLuid: string,
  retryDeps: RetryDeps = {},
): Promise<VdsField[]> {
  if (!datasourceLuid.trim()) {
    throw new Error("readDatasourceMetadata: datasourceLuid is required.");
  }

  const url = `${auth.server}${READ_METADATA_PATH}`;
  const body = JSON.stringify({ datasource: { datasourceLuid } });

  const json = await withRetry<VdsMetadataResponse>(
    async () => {
      const res = await request(url, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Tableau-Auth": auth.token,
        },
        body,
      });

      if (res.statusCode >= 400) {
        const text = await res.body.text();
        process.stderr.write(
          `[tableau-mcp-publish] VDS read-metadata ${res.statusCode} for datasource ${datasourceLuid}: ${text}\n`,
        );
        throw buildVdsError(res.statusCode, text);
      }
      return (await res.body.json()) as VdsMetadataResponse;
    },
    (err: unknown): RetrySignal | undefined =>
      err instanceof TableauApiError
        ? { status: err.status, retryAfterMs: err.retryAfterMs }
        : undefined,
    true,
    undefined,
    retryDeps,
  );

  return (json.data ?? []).map(toVdsField);
}
