import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));
vi.mock("undici", () => ({ request: vi.fn() }));

import { request } from "undici";
import { TableauRestClient, TableauApiError } from "../src/restClient.js";
import type { Config } from "../src/config.js";
import type { RetryDeps } from "../src/rest/retry.js";
import {
  buildCreateWebhookXml,
  parseWebhook,
  parseWebhookList,
  validateCreateWebhookInput,
  CreateWebhookInputSchema,
  type CreateWebhookInput,
} from "../src/rest/webhooks.js";

const cfg: Config = {
  server: "https://x.online.tableau.com",
  siteName: "s",
  patName: "p",
  patValue: "secret-value",
  apiVersion: "3.28",
  sidecarHost: "127.0.0.1",
  sidecarPort: 8899,
};

const mockedRequest = vi.mocked(request);

function response(statusCode: number, obj: unknown, headers: Record<string, string> = {}) {
  return {
    statusCode,
    headers,
    body: { json: async () => obj, text: async () => JSON.stringify(obj) },
  };
}

async function signedInClient(retryDeps: RetryDeps): Promise<TableauRestClient> {
  mockedRequest.mockResolvedValueOnce(
    response(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
  );
  const client = new TableauRestClient(cfg, undefined, retryDeps);
  await client.signIn();
  mockedRequest.mockClear();
  return client;
}

const validInput: CreateWebhookInput = {
  name: "On refresh success",
  event: "DatasourceRefreshSucceeded",
  url: "https://example.com/hooks/tableau",
};

beforeEach(() => vi.clearAllMocks());

// ---------------------------------------------------------------------------
// Pure module: schema, XML builder, response parsing
// ---------------------------------------------------------------------------

describe("rest/webhooks — CreateWebhookInputSchema + validateCreateWebhookInput", () => {
  it("accepts a well-formed HTTPS input", () => {
    const parsed = CreateWebhookInputSchema.parse(validInput);
    expect(() => validateCreateWebhookInput(parsed)).not.toThrow();
  });

  it("rejects an unknown event outside the enum at the schema layer", () => {
    expect(() => CreateWebhookInputSchema.parse({ ...validInput, event: "SomethingMadeUp" })).toThrow();
  });

  it("rejects an empty name at the schema layer", () => {
    expect(() => CreateWebhookInputSchema.parse({ ...validInput, name: "" })).toThrow();
  });

  it("rejects a malformed URL at the schema layer", () => {
    expect(() => CreateWebhookInputSchema.parse({ ...validInput, url: "not-a-url" })).toThrow();
  });

  it("validateCreateWebhookInput rejects http:// (schema layer alone accepts it)", () => {
    const parsed = CreateWebhookInputSchema.parse({ ...validInput, url: "http://example.com/hook" });
    expect(() => validateCreateWebhookInput(parsed)).toThrow(/HTTPS/);
  });
});

describe("rest/webhooks — buildCreateWebhookXml", () => {
  it("builds the documented tsRequest shape", () => {
    const xml = buildCreateWebhookXml(validInput);
    expect(xml).toBe(
      '<tsRequest><webhook name="On refresh success" event="DatasourceRefreshSucceeded">' +
        '<webhook-destination><webhook-destination-http method="POST" url="https://example.com/hooks/tableau" /></webhook-destination>' +
        "</webhook></tsRequest>",
    );
  });

  it("XML-escapes the name", () => {
    const xml = buildCreateWebhookXml({ ...validInput, name: 'On "refresh" & success' });
    expect(xml).toContain('name="On &quot;refresh&quot; &amp; success"');
  });
});

describe("rest/webhooks — response parsing", () => {
  it("parses a create response { webhook: {...} }", () => {
    const webhook = parseWebhook({
      webhook: {
        id: "WH1",
        name: "On refresh success",
        event: "DatasourceRefreshSucceeded",
        "webhook-destination": { "webhook-destination-http": { url: "https://example.com/hooks/tableau" } },
      },
    });
    expect(webhook).toEqual({
      webhookId: "WH1",
      name: "On refresh success",
      event: "DatasourceRefreshSucceeded",
      url: "https://example.com/hooks/tableau",
    });
  });

  it("throws a clear error when webhook is missing from the response", () => {
    expect(() => parseWebhook({})).toThrow(/missing webhook/);
  });

  it("throws a clear error when id or name is missing", () => {
    expect(() => parseWebhook({ webhook: { name: "x" } })).toThrow(/missing id or name/);
  });

  it("parses a list response, tolerating the single-item-collapses-to-object quirk", () => {
    const single = parseWebhookList({ webhooks: { webhook: { id: "WH1", name: "A", event: "DatasourceCreated" } } });
    expect(single).toEqual([{ webhookId: "WH1", name: "A", event: "DatasourceCreated" }]);

    const multi = parseWebhookList({
      webhooks: {
        webhook: [
          { id: "WH1", name: "A", event: "DatasourceCreated" },
          { id: "WH2", name: "B", event: "WorkbookUpdated" },
        ],
      },
    });
    expect(multi.map((w) => w.webhookId)).toEqual(["WH1", "WH2"]);
  });

  it("returns an empty list when webhooks/webhook is absent", () => {
    expect(parseWebhookList({})).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// TableauRestClient wiring (mocked undici)
// ---------------------------------------------------------------------------

describe("TableauRestClient — webhook wiring", () => {
  it("createWebhook POSTs the create body and returns the parsed webhook", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    mockedRequest.mockResolvedValueOnce(
      response(200, {
        webhook: { id: "WH1", name: validInput.name, event: validInput.event },
      }) as never,
    );

    const webhook = await client.createWebhook(validInput);
    expect(webhook).toEqual({ webhookId: "WH1", name: validInput.name, event: validInput.event });

    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/3.28/sites/S/webhooks");
    const call = opts as { method: string; body: string };
    expect(call.method).toBe("POST");
    expect(call.body).toContain('event="DatasourceRefreshSucceeded"');
  });

  it("createWebhook rejects a non-HTTPS url before making any request", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    await expect(client.createWebhook({ ...validInput, url: "http://example.com/hook" })).rejects.toThrow(
      /HTTPS/,
    );
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("createWebhook rewraps a 403 with an actionable site-admin explanation", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    mockedRequest.mockResolvedValueOnce(
      response(403, { error: { code: "403006", summary: "Forbidden", detail: "no permission" } }) as never,
    );

    let caught: unknown;
    try {
      await client.createWebhook(validInput);
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(403);
    expect(err.summary).toMatch(/site administrator/i);
  });

  it("does NOT retry createWebhook on a 503 (POST is non-idempotent by default)", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep });
    mockedRequest.mockResolvedValueOnce(response(503, {}) as never);

    await expect(client.createWebhook(validInput)).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("listWebhooks GETs the collection and retries a transient 503", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep, jitterFn: () => 0 });
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(
        response(200, { webhooks: { webhook: [{ id: "WH1", name: "A", event: "DatasourceCreated" }] } }) as never,
      );

    const webhooks = await client.listWebhooks();
    expect(webhooks).toEqual([{ webhookId: "WH1", name: "A", event: "DatasourceCreated" }]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
  });

  it("deleteWebhook DELETEs the resource and is retriable on a transient 503", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep, jitterFn: () => 0 });
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(204, {}) as never);

    await client.deleteWebhook("WH1");
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    const [url, opts] = mockedRequest.mock.calls[1]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/3.28/sites/S/webhooks/WH1");
    expect((opts as { method: string }).method).toBe("DELETE");
  });
});
