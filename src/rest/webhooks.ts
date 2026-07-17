/**
 * Webhooks — Phase E2 slice B.
 *
 * Tableau Cloud webhooks POST a JSON payload to an HTTPS destination when a
 * site event fires:
 *
 *   Create: `POST   /api/{ver}/sites/{siteId}/webhooks`
 *   List:   `GET    /api/{ver}/sites/{siteId}/webhooks`
 *   Delete: `DELETE /api/{ver}/sites/{siteId}/webhooks/{webhookId}`
 *
 * Creating a webhook requires **site-administrator** privileges; a PAT
 * without that role gets a 403, which
 * {@link TableauRestClient.createWebhook} (in `restClient.ts`) rewraps into
 * an explicit, actionable summary rather than a bare "Forbidden".
 *
 * The destination receives a JSON payload shaped like:
 * `{ resource, event_type, resource_name, site_luid, resource_luid, created_at }`
 * (documented at the `create_webhook` tool layer too, since Tableau posts it
 * to the caller's own endpoint — this module never receives or validates it).
 *
 * Supported events mirror the datasource/workbook refresh + CRUD lifecycle:
 * `Datasource{RefreshStarted,RefreshSucceeded,RefreshFailed,Created,Updated,Deleted}`
 * and the `Workbook*` equivalents.
 */

import { z } from "zod";
import { xmlEscape, asArray } from "./xml.js";

export const WebhookEventSchema = z.enum([
  "DatasourceRefreshStarted",
  "DatasourceRefreshSucceeded",
  "DatasourceRefreshFailed",
  "DatasourceCreated",
  "DatasourceUpdated",
  "DatasourceDeleted",
  "WorkbookRefreshStarted",
  "WorkbookRefreshSucceeded",
  "WorkbookRefreshFailed",
  "WorkbookCreated",
  "WorkbookUpdated",
  "WorkbookDeleted",
]);
export type WebhookEvent = z.infer<typeof WebhookEventSchema>;

/**
 * Basic shape (name presence + syntactically valid URL). Exposed as a plain
 * `z.object` (not `.refine`d) so its `.shape` can be spread directly into an
 * MCP tool's `inputSchema`; {@link validateCreateWebhookInput} adds the
 * HTTPS-only business rule that a bare `z.string().url()` can't express.
 */
export const CreateWebhookInputSchema = z.object({
  name: z.string().min(1).describe("Human-readable webhook name."),
  event: WebhookEventSchema.describe("Tableau site event that triggers this webhook."),
  url: z.string().url().describe("Destination URL that receives the webhook POST. Must be HTTPS."),
});
export type CreateWebhookInput = z.infer<typeof CreateWebhookInputSchema>;

/**
 * Enforces HTTPS-only destinations — Tableau requires it, and
 * `z.string().url()` alone accepts `http://`. Throws a plain, actionable
 * `Error` (not a `ZodError`) so every validation failure in this module has
 * one consistent shape.
 */
export function validateCreateWebhookInput(input: CreateWebhookInput): CreateWebhookInput {
  if (!input.url.startsWith("https://")) {
    throw new Error(
      `Invalid webhook input:\n  - url: must be HTTPS (got "${input.url}"). Tableau refuses non-HTTPS webhook destinations.`,
    );
  }
  return input;
}

/** `POST /sites/{siteId}/webhooks` body. */
export function buildCreateWebhookXml(input: CreateWebhookInput): string {
  return (
    `<tsRequest><webhook name="${xmlEscape(input.name)}" event="${input.event}">` +
    `<webhook-destination><webhook-destination-http method="POST" url="${xmlEscape(input.url)}" /></webhook-destination>` +
    `</webhook></tsRequest>`
  );
}

export interface Webhook {
  webhookId: string;
  name: string;
  event: string;
  url?: string;
}

interface RawWebhook {
  id?: unknown;
  name?: unknown;
  event?: unknown;
  "webhook-destination"?: {
    "webhook-destination-http"?: { url?: unknown; method?: unknown };
  };
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function toWebhook(raw: RawWebhook): Webhook {
  const webhookId = optionalString(raw.id);
  const name = optionalString(raw.name);
  if (!webhookId || !name) {
    throw new Error("Malformed webhook response: missing id or name.");
  }
  const url = optionalString(raw["webhook-destination"]?.["webhook-destination-http"]?.url);
  return {
    webhookId,
    name,
    event: optionalString(raw.event) ?? "",
    ...(url !== undefined ? { url } : {}),
  };
}

/** Parses the create-webhook response: `{ webhook: {...} }`. */
export function parseWebhook(json: unknown): Webhook {
  const obj = json as { webhook?: RawWebhook };
  if (!obj.webhook) {
    throw new Error("Malformed webhook response: missing webhook.");
  }
  return toWebhook(obj.webhook);
}

/** Parses the list response: `{ webhooks: { webhook: [...] } }`. */
export function parseWebhookList(json: unknown): Webhook[] {
  const obj = json as { webhooks?: { webhook?: RawWebhook | RawWebhook[] } };
  return asArray(obj.webhooks?.webhook).map(toWebhook);
}
