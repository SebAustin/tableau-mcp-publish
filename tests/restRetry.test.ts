import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));
vi.mock("undici", () => ({ request: vi.fn() }));

import { request } from "undici";
import { TableauRestClient, TableauApiError } from "../src/restClient.js";
import type { Config } from "../src/config.js";
import type { RetryDeps } from "../src/rest/retry.js";

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

function response(
  statusCode: number,
  obj: unknown,
  headers: Record<string, string> = {},
) {
  return {
    statusCode,
    headers,
    body: { json: async () => obj, text: async () => JSON.stringify(obj) },
  };
}

beforeEach(() => vi.clearAllMocks());

/** Signs in (consumes one mocked request) with injected, no-real-sleep retry deps. */
async function signedInClient(retryDeps: RetryDeps): Promise<TableauRestClient> {
  mockedRequest.mockResolvedValueOnce(
    response(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
  );
  const client = new TableauRestClient(cfg, undefined, retryDeps);
  await client.signIn();
  mockedRequest.mockClear(); // isolate call-count assertions to the call under test
  return client;
}

describe("REST retry/backoff hardening (Phase E2 Foundation)", () => {
  it("retries a 429 honoring Retry-After, then succeeds (GET is idempotent by default)", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep });

    mockedRequest
      .mockResolvedValueOnce(response(429, {}, { "retry-after": "2" }) as never)
      .mockResolvedValueOnce(
        response(200, { pagination: { totalAvailable: "0" }, projects: { project: [] } }) as never,
      );

    const projects = await client.listProjects();
    expect(projects).toEqual([]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(2000); // 2s Retry-After header honored verbatim
  });

  it("retries a 503 with computed backoff, then succeeds", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep, jitterFn: () => 1 });

    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(
        response(200, { pagination: { totalAvailable: "0" }, projects: { project: [] } }) as never,
      );

    const projects = await client.listProjects();
    expect(projects).toEqual([]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
  });

  it("does NOT retry a 400 (bad request)", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep });

    mockedRequest.mockResolvedValueOnce(
      response(400, { error: { code: "400011", summary: "Bad Request", detail: "bad payload" } }) as never,
    );

    await expect(client.listProjects()).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1); // no retry attempted
    expect(sleep).not.toHaveBeenCalled();
  });

  it("does NOT retry a 401 (unauthorized)", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep });

    mockedRequest.mockResolvedValueOnce(response(401, {}) as never);

    await expect(client.listProjects()).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("gives up after max attempts (3) with a clear TableauApiError", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep, jitterFn: () => 0 });

    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(503, {}) as never);

    let caught: unknown;
    try {
      await client.listProjects();
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(503);
    expect(err.retriable).toBe(true);
    expect(err.message).toContain("503");
    expect(mockedRequest).toHaveBeenCalledTimes(3); // exactly maxAttempts, no 4th call
    expect(sleep).toHaveBeenCalledTimes(2);
  });

  it("never retries the non-idempotent chunk-append PUT mid-upload, even on 429/503", async () => {
    const { readFile, stat } = await import("node:fs/promises");
    vi.mocked(stat).mockResolvedValue({ size: 100 * 1024 * 1024 } as never); // force chunked path
    vi.mocked(readFile).mockResolvedValue(Buffer.from("0123456789") as never);

    const sleep = vi.fn().mockResolvedValue(undefined);
    const client = await signedInClient({ sleep });

    mockedRequest
      .mockResolvedValueOnce(response(201, { fileUpload: { uploadSessionId: "US" } }) as never) // init POST (not idempotent, but succeeds first try)
      .mockResolvedValueOnce(response(429, {}, { "retry-after": "1" }) as never); // chunk append PUT fails — must NOT retry

    await expect(client.publishDatasource("/tmp/big.tdsx", "Big", "PID", true)).rejects.toThrow(
      TableauApiError,
    );
    expect(sleep).not.toHaveBeenCalled();
    // init POST + exactly one PUT attempt — no retry of the chunk append.
    expect(mockedRequest).toHaveBeenCalledTimes(2);
  });

  it("does not throw a raw undici rejection to callers on non-retriable failure — error is a typed TableauApiError with status/code/summary", async () => {
    const client = await signedInClient({ sleep: vi.fn().mockResolvedValue(undefined) });
    mockedRequest.mockResolvedValueOnce(
      response(404, { error: { code: "404012", summary: "Not Found", detail: "no such project" } }) as never,
    );

    let caught: unknown;
    try {
      await client.listProjects();
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(404);
    expect(err.code).toBe("404012");
    expect(err.summary).toBe("Not Found");
    expect(err.retriable).toBe(false);
  });
});
