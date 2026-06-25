/**
 * Unit tests for AuthoringSidecar.buildDatasourceFromFile() asserting that
 * the `columns` field is parsed from the sidecar response and returned to
 * the caller.
 *
 * The sidecar's HTTP call is mocked via undici so no real sidecar process
 * needs to run.
 */

vi.mock("undici", () => ({ request: vi.fn() }));

import { describe, it, expect, vi, beforeEach } from "vitest";
import { request } from "undici";
import { AuthoringSidecar } from "../src/sidecar.js";
import type { ColumnInfo } from "../src/sidecar.js";

const mockedRequest = vi.mocked(request);

/** Build a fake undici response that returns the given JSON body. */
function fakeResponse(body: unknown, statusCode = 200) {
  return {
    statusCode,
    body: {
      json: vi.fn().mockResolvedValue(body),
      text: vi.fn().mockResolvedValue(JSON.stringify(body)),
    },
  };
}

describe("AuthoringSidecar.buildDatasourceFromFile — columns parsing", () => {
  let sidecar: AuthoringSidecar;

  beforeEach(() => {
    vi.clearAllMocks();
    // Construct with explicit port so start() is not required for these unit tests.
    // We wire the actualPort via a cast so the private field is set.
    sidecar = new AuthoringSidecar("127.0.0.1", 19_999);
    // Force actualPort so baseUrl resolves without spawning a real process.
    // AuthoringSidecar stores actualPort at 0 until start() — patch via prototype.
    Object.defineProperty(sidecar, "actualPort", { value: 19_999, writable: true });
  });

  it("returns columns from the sidecar file-result response", async () => {
    const columns: ColumnInfo[] = [
      { name: "region", dataType: "string" },
      { name: "customer", dataType: "string" },
      { name: "revenue", dataType: "number" },
    ];
    mockedRequest.mockResolvedValue(
      fakeResponse({ path: "/tmp/abc.tdsx", columns }) as never,
    );

    const result = await sidecar.buildDatasourceFromFile({
      name: "Top Customers",
      filePath: "/data/top_customers.csv",
      fileType: "csv",
    });

    expect(result.tdsxPath).toBe("/tmp/abc.tdsx");
    expect(result.columns).toEqual(columns);
  });

  it("passes filePath and fileType to the sidecar POST body", async () => {
    const columns: ColumnInfo[] = [{ name: "a", dataType: "number" }];
    mockedRequest.mockResolvedValue(
      fakeResponse({ path: "/tmp/x.tdsx", columns }) as never,
    );

    await sidecar.buildDatasourceFromFile({
      name: "DS",
      filePath: "/some/file.csv",
      fileType: "csv",
    });

    const [url, options] = mockedRequest.mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toContain("/datasource/from-file");
    const body = JSON.parse(options["body"] as string) as Record<string, unknown>;
    expect(body["filePath"]).toBe("/some/file.csv");
    expect(body["fileType"]).toBe("csv");
  });

  it("preserves column order from the response", async () => {
    const columns: ColumnInfo[] = [
      { name: "z_col", dataType: "string" },
      { name: "a_col", dataType: "number" },
      { name: "m_col", dataType: "boolean" },
    ];
    mockedRequest.mockResolvedValue(
      fakeResponse({ path: "/tmp/y.tdsx", columns }) as never,
    );

    const result = await sidecar.buildDatasourceFromFile({
      name: "DS",
      filePath: "/data/f.csv",
    });

    expect(result.columns.map((c) => c.name)).toEqual(["z_col", "a_col", "m_col"]);
  });
});
