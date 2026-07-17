import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("undici", () => ({ request: vi.fn() }));
vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));

import { request } from "undici";
import { stat, readFile } from "node:fs/promises";
import { TableauRestClient } from "../src/restClient.js";
import { loadConfig } from "../src/config.js";
import type { Config } from "../src/config.js";

const SECRET = "SUPER_SECRET_PAT_VALUE_42";
const cfg: Config = {
  server: "https://x.online.tableau.com",
  siteName: "s",
  patName: "p",
  patValue: SECRET,
  apiVersion: "3.28",
  sidecarHost: "127.0.0.1",
  sidecarPort: 8899,
};
const mockedRequest = vi.mocked(request);

function jsonResponse(statusCode: number, obj: unknown, text = "") {
  return { statusCode, body: { json: async () => obj, text: async () => text } };
}

/** Capture everything written to stderr and the console during a block. */
function captureOutput() {
  const chunks: string[] = [];
  const sink = (s: unknown) => {
    chunks.push(String(s));
    return true;
  };
  const spies = [
    vi.spyOn(process.stderr, "write").mockImplementation(sink as never),
    vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => void sink(a.join(" "))),
    vi.spyOn(console, "log").mockImplementation((...a: unknown[]) => void sink(a.join(" "))),
  ];
  return { chunks, restore: () => spies.forEach((s) => s.mockRestore()) };
}

beforeEach(() => vi.clearAllMocks());

describe("the PAT is never logged", () => {
  it("does not appear in output when signing in", async () => {
    mockedRequest.mockResolvedValueOnce(
      jsonResponse(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
    );
    const cap = captureOutput();
    try {
      await new TableauRestClient(cfg).signIn();
    } finally {
      cap.restore();
    }
    expect(cap.chunks.join("\n")).not.toContain(SECRET);
  });

  it("does not appear in a redacted API error", async () => {
    mockedRequest
      .mockResolvedValueOnce(
        jsonResponse(200, {
          credentials: { token: "T", site: { id: "S" }, user: { id: "U" } },
        }) as never,
      )
      .mockResolvedValueOnce(jsonResponse(400, {}, "upstream error detail (no secret here)") as never);

    const cap = captureOutput();
    let threw = false;
    try {
      const client = new TableauRestClient(cfg);
      await client.signIn();
      await client.createProject("X").catch(() => {
        threw = true;
      });
    } finally {
      cap.restore();
    }
    expect(threw).toBe(true);
    expect(cap.chunks.join("\n")).not.toContain(SECRET);
  });

  it("config validation errors do not echo the PAT value", () => {
    let message = "";
    try {
      loadConfig({ SERVER: "not-a-url", SITE_NAME: "s", PAT_NAME: "p", PAT_VALUE: SECRET } as never);
    } catch (e) {
      message = (e as Error).message;
    }
    expect(message).toMatch(/Invalid configuration|server/i);
    expect(message).not.toContain(SECRET);
  });
});

describe("the embedded datasource credential password is never logged (Phase E2 slice C)", () => {
  const DB_PASSWORD = "SUPER_SECRET_DB_PASSWORD_99";

  it("does not appear in output on a successful publishDatasource call with credentials", async () => {
    mockedRequest
      .mockResolvedValueOnce(
        jsonResponse(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
      )
      .mockResolvedValueOnce(jsonResponse(201, { datasource: { id: "DS" } }) as never);

    vi.mocked(stat).mockResolvedValue({ size: 1000 } as never);
    vi.mocked(readFile).mockResolvedValue(Buffer.from("filedata") as never);

    const cap = captureOutput();
    try {
      const client = new TableauRestClient(cfg);
      await client.signIn();
      await client.publishDatasource("/tmp/live.tds", "Orders", "PID", false, {
        credentials: { username: "svc_user", password: DB_PASSWORD, embed: true, oauth: false },
      });
    } finally {
      cap.restore();
    }
    expect(cap.chunks.join("\n")).not.toContain(DB_PASSWORD);
  });

  it("does not appear in output when the publish call itself fails (upstream error body logged, not the request)", async () => {
    mockedRequest
      .mockResolvedValueOnce(
        jsonResponse(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
      )
      .mockResolvedValueOnce(jsonResponse(400, {}, "upstream error detail (no secret here)") as never);

    vi.mocked(stat).mockResolvedValue({ size: 1000 } as never);
    vi.mocked(readFile).mockResolvedValue(Buffer.from("filedata") as never);

    const cap = captureOutput();
    let threw = false;
    try {
      const client = new TableauRestClient(cfg);
      await client.signIn();
      await client
        .publishDatasource("/tmp/live.tds", "Orders", "PID", false, {
          credentials: { username: "svc_user", password: DB_PASSWORD, embed: true, oauth: false },
        })
        .catch(() => {
          threw = true;
        });
    } finally {
      cap.restore();
    }
    expect(threw).toBe(true);
    expect(cap.chunks.join("\n")).not.toContain(DB_PASSWORD);
  });
});
