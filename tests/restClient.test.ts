import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));
vi.mock("undici", () => ({ request: vi.fn() }));

import { stat, readFile } from "node:fs/promises";
import { request } from "undici";
import {
  TableauRestClient,
  splitIntoChunks,
  selectPublishStrategy,
  SINGLE_REQUEST_LIMIT_BYTES,
} from "../src/restClient.js";
import type { Config } from "../src/config.js";

const cfg: Config = {
  server: "https://x.online.tableau.com",
  siteName: "s",
  patName: "p",
  patValue: "secret-value",
  apiVersion: "3.28",
  sidecarHost: "127.0.0.1",
  sidecarPort: 8899,
};

function jsonResponse(statusCode: number, obj: unknown) {
  return {
    statusCode,
    body: { json: async () => obj, text: async () => JSON.stringify(obj) },
  };
}

const mockedRequest = vi.mocked(request);
const mockedStat = vi.mocked(stat);
const mockedReadFile = vi.mocked(readFile);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("splitIntoChunks (chunk math)", () => {
  it("splits into ceil(size/chunk) pieces and reassembles", () => {
    const buf = Buffer.from("0123456789"); // 10 bytes
    const chunks = splitIntoChunks(buf, 4);
    expect(chunks.map((c) => c.length)).toEqual([4, 4, 2]);
    expect(Buffer.concat(chunks).toString()).toBe("0123456789");
  });

  it("returns one chunk when smaller than the chunk size", () => {
    expect(splitIntoChunks(Buffer.from("ab"), 64).length).toBe(1);
  });

  it("returns no chunks for an empty buffer and rejects non-positive sizes", () => {
    expect(splitIntoChunks(Buffer.alloc(0), 4)).toEqual([]);
    expect(() => splitIntoChunks(Buffer.from("x"), 0)).toThrow();
  });
});

describe("selectPublishStrategy (64MiB boundary, TSC-aligned)", () => {
  // Boundary aligns to official server-client-python (TSC): chunks when
  // file_size >= FILESIZE_LIMIT_MB * BYTES_PER_MB.  Exactly 64 MiB → chunked.
  it("uses single below 64MiB, chunked at or above 64MiB", () => {
    expect(selectPublishStrategy(1)).toBe("single");
    expect(selectPublishStrategy(SINGLE_REQUEST_LIMIT_BYTES - 1)).toBe("single");
    expect(selectPublishStrategy(SINGLE_REQUEST_LIMIT_BYTES)).toBe("chunked"); // exactly 64MiB → chunked (TSC-aligned)
    expect(selectPublishStrategy(SINGLE_REQUEST_LIMIT_BYTES + 1)).toBe("chunked");
  });
});

describe("signIn", () => {
  it("parses the session token, site id and user id", async () => {
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
    );
    const client = new TableauRestClient(cfg);
    const session = await client.signIn();
    expect(session).toEqual({ token: "T", siteId: "S", userId: "U" });

    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toContain("/api/3.28/auth/signin");
    expect((opts as { method: string }).method).toBe("POST");
  });
});

async function signedInClient(chunkSize?: number): Promise<TableauRestClient> {
  mockedRequest.mockResolvedValueOnce(
    jsonResponse(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
  );
  const client = new TableauRestClient(cfg, chunkSize);
  await client.signIn();
  return client;
}

describe("publishDatasource — single request (<=64MB)", () => {
  it("posts a single multipart/mixed request and returns id + url", async () => {
    const client = await signedInClient();
    mockedStat.mockResolvedValue({ size: 1000 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("filedata") as never);
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(201, {
        datasource: {
          id: "DS1",
          webpageUrl: "https://x.online.tableau.com/#/site/s/datasources/12345",
        },
      }) as never,
    );

    const res = await client.publishDatasource("/tmp/x.tdsx", "Name", "PID", false);
    expect(res.id).toBe("DS1");
    expect(res.url).toBe("https://x.online.tableau.com/#/site/s/datasources/12345");

    const lastCall = mockedRequest.mock.calls.at(-1)!;
    expect(String(lastCall[0])).toContain("/api/3.28/sites/S/datasources");
    const headers = (lastCall[1] as { headers: Record<string, string> }).headers;
    expect(headers["Content-Type"]).toContain("multipart/mixed");
  });
});

describe("publishDatasource — chunked (>64MB)", () => {
  it("initiates, appends each chunk, then finalizes with uploadSessionId", async () => {
    const client = await signedInClient(4); // 4-byte chunks
    mockedStat.mockResolvedValue({ size: SINGLE_REQUEST_LIMIT_BYTES + 1 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("0123456789") as never); // 10 bytes -> 3 chunks
    mockedRequest
      .mockResolvedValueOnce(jsonResponse(201, { fileUpload: { uploadSessionId: "US" } }) as never)
      .mockResolvedValueOnce(jsonResponse(200, {}) as never)
      .mockResolvedValueOnce(jsonResponse(200, {}) as never)
      .mockResolvedValueOnce(jsonResponse(200, {}) as never)
      .mockResolvedValueOnce(jsonResponse(201, { datasource: { id: "DS2" } }) as never);

    const res = await client.publishDatasource("/tmp/big.tdsx", "Big", "PID", true);
    expect(res.id).toBe("DS2");

    const calls = mockedRequest.mock.calls;
    const puts = calls.filter((c) => (c[1] as { method: string }).method === "PUT");
    expect(puts.length).toBe(3); // ceil(10/4)
    const finalize = calls.filter(
      (c) =>
        (c[1] as { method: string }).method === "POST" &&
        String(c[0]).includes("uploadSessionId=US"),
    );
    expect(finalize.length).toBe(1);
  });

  it("aborts on a mid-stream chunk failure and never finalizes", async () => {
    const client = await signedInClient(4);
    mockedStat.mockResolvedValue({ size: SINGLE_REQUEST_LIMIT_BYTES + 1 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("0123456789") as never);
    mockedRequest
      .mockResolvedValueOnce(jsonResponse(201, { fileUpload: { uploadSessionId: "US" } }) as never)
      .mockResolvedValueOnce(jsonResponse(200, {}) as never) // chunk 1 ok
      .mockRejectedValueOnce(new Error("network blip")); // chunk 2 fails

    await expect(client.publishDatasource("/tmp/big.tdsx", "Big", "PID", true)).rejects.toThrow(
      "network blip",
    );

    const finalize = mockedRequest.mock.calls.filter(
      (c) =>
        (c[1] as { method: string }).method === "POST" && String(c[0]).includes("/datasources"),
    );
    expect(finalize.length).toBe(0); // no partial publish
  });
});

describe("publishDatasource — embedded credentials (Phase E2 slice C)", () => {
  it("emits <connectionCredentials> and useRemoteQueryAgent inside the request_payload XML", async () => {
    const client = await signedInClient();
    mockedStat.mockResolvedValue({ size: 1000 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("filedata") as never);
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(201, {
        datasource: { id: "DS3", webpageUrl: "https://x.online.tableau.com/#/site/s/datasources/DS3" },
      }) as never,
    );

    await client.publishDatasource("/tmp/live.tds", "Orders", "PID", false, {
      credentials: { username: "svc_user", password: "s3cr3t <secret>", embed: true, oauth: false },
      useRemoteQueryAgent: true,
    });

    const lastCall = mockedRequest.mock.calls.at(-1)!;
    const body = (lastCall[1] as { body: Buffer }).body.toString("utf8");
    // Exact tsRequest body (redacted-value-safe assertion: the raw password is
    // xml-escaped, never appears unescaped, and never as a bare `<` breakout).
    expect(body).toContain(
      '<tsRequest><datasource name="Orders" useRemoteQueryAgent="true">' +
        '<project id="PID" />' +
        '<connectionCredentials name="svc_user" password="s3cr3t &lt;secret&gt;" embed="true" oAuth="false" />' +
        "</datasource></tsRequest>",
    );
    expect(body).not.toContain("s3cr3t <secret>"); // never unescaped
  });

  it("omits <connectionCredentials> and useRemoteQueryAgent when not provided (back-compat)", async () => {
    const client = await signedInClient();
    mockedStat.mockResolvedValue({ size: 1000 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("filedata") as never);
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(201, { datasource: { id: "DS4" } }) as never,
    );

    await client.publishDatasource("/tmp/x.tdsx", "Plain", "PID", false);

    const lastCall = mockedRequest.mock.calls.at(-1)!;
    const body = (lastCall[1] as { body: Buffer }).body.toString("utf8");
    expect(body).toContain('<tsRequest><datasource name="Plain"><project id="PID" /></datasource></tsRequest>');
    expect(body).not.toContain("connectionCredentials");
    expect(body).not.toContain("useRemoteQueryAgent");
  });

  it("never applies credentials/useRemoteQueryAgent to a workbook publish", async () => {
    const client = await signedInClient();
    mockedStat.mockResolvedValue({ size: 1000 } as never);
    mockedReadFile.mockResolvedValue(Buffer.from("filedata") as never);
    mockedRequest.mockResolvedValueOnce(jsonResponse(201, { workbook: { id: "WB9" } }) as never);

    await client.publishWorkbook("/tmp/x.twbx", "WB", "PID", false);

    const lastCall = mockedRequest.mock.calls.at(-1)!;
    const body = (lastCall[1] as { body: Buffer }).body.toString("utf8");
    expect(body).not.toContain("connectionCredentials");
    expect(body).not.toContain("useRemoteQueryAgent");
  });
});

describe("resolveProjectId", () => {
  it("rejects an empty project name (never Default silently)", async () => {
    const client = await signedInClient();
    await expect(client.resolveProjectId("")).rejects.toThrow(/non-empty/);
  });

  it("rejects the Default project by name", async () => {
    const client = await signedInClient();
    await expect(client.resolveProjectId("Default")).rejects.toThrow(/Default/);
    await expect(client.resolveProjectId("default")).rejects.toThrow(/Default/);
  });

  it("resolves a known project name to its LUID", async () => {
    const client = await signedInClient();
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(200, {
        pagination: { totalAvailable: "1" },
        projects: { project: [{ id: "PID", name: "Sales" }] },
      }) as never,
    );
    expect(await client.resolveProjectId("Sales")).toBe("PID");
  });
});
