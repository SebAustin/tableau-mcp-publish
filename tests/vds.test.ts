import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("undici", () => ({ request: vi.fn() }));

import { request } from "undici";
import { readDatasourceMetadata } from "../src/rest/vds.js";
import { TableauApiError } from "../src/rest/errors.js";

const mockedRequest = vi.mocked(request);
const auth = { server: "https://x.online.tableau.com", token: "T" };
const noSleep = vi.fn().mockResolvedValue(undefined);

function response(statusCode: number, obj: unknown) {
  return {
    statusCode,
    headers: {},
    body: { json: async () => obj, text: async () => JSON.stringify(obj) },
  };
}

beforeEach(() => vi.clearAllMocks());

describe("readDatasourceMetadata (VDS read-metadata client)", () => {
  it("POSTs to /api/v1/vizql-data-service/read-metadata with the datasourceLuid + auth header", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(200, {
        data: [{ fieldName: "Sales", fieldCaption: "Sales", dataType: "REAL", defaultAggregation: "SUM" }],
      }) as never,
    );

    const fields = await readDatasourceMetadata(auth, "DS-luid", { sleep: noSleep });

    expect(fields).toEqual([
      {
        fieldName: "Sales",
        fieldCaption: "Sales",
        dataType: "REAL",
        defaultAggregation: "SUM",
        logicalTableId: undefined,
      },
    ]);

    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/v1/vizql-data-service/read-metadata");
    const call = opts as { method: string; headers: Record<string, string>; body: string };
    expect(call.method).toBe("POST");
    expect(call.headers["X-Tableau-Auth"]).toBe("T");
    expect(JSON.parse(call.body)).toEqual({ datasource: { datasourceLuid: "DS-luid" } });
  });

  it("maps multiple fields and tolerates absent optional fields", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(200, {
        data: [
          { fieldName: "Order Date", dataType: "DATE" }, // no caption/defaultAggregation/logicalTableId
          { fieldName: "Region", fieldCaption: "Region", logicalTableId: "t0" },
        ],
      }) as never,
    );

    const fields = await readDatasourceMetadata(auth, "DS-luid", { sleep: noSleep });
    expect(fields).toHaveLength(2);
    expect(fields[0]).toMatchObject({ fieldName: "Order Date", dataType: "DATE" });
    expect(fields[0]?.fieldCaption).toBeUndefined();
    expect(fields[1]).toMatchObject({ fieldName: "Region", fieldCaption: "Region", logicalTableId: "t0" });
  });

  it("rejects an empty datasourceLuid before making a request", async () => {
    await expect(readDatasourceMetadata(auth, "", { sleep: noSleep })).rejects.toThrow(/datasourceLuid/);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("throws an actionable 'not found or VDS unavailable' error on 404", async () => {
    mockedRequest.mockResolvedValueOnce(response(404, {}) as never);

    let caught: unknown;
    try {
      await readDatasourceMetadata(auth, "missing-luid", { sleep: noSleep });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(404);
    expect(err.summary).toMatch(/not found|unavailable/i);
  });

  it("surfaces a feature-disabled response actionably", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(400, {
        error: {
          code: "400011",
          summary: "Bad Request",
          detail: "VizQL Data Service is not enabled for this site.",
        },
      }) as never,
    );

    let caught: unknown;
    try {
      await readDatasourceMetadata(auth, "DS-luid", { sleep: noSleep });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(400);
    expect(err.summary).toMatch(/not enabled/i);
  });

  it("throws a clear error on a malformed response (field missing fieldName)", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(200, { data: [{ dataType: "REAL" }] }) as never, // no fieldName
    );

    await expect(readDatasourceMetadata(auth, "DS-luid", { sleep: noSleep })).rejects.toThrow(
      /malformed response/i,
    );
  });

  it("treats an absent data[] as zero fields rather than throwing", async () => {
    mockedRequest.mockResolvedValueOnce(response(200, {}) as never);
    const fields = await readDatasourceMetadata(auth, "DS-luid", { sleep: noSleep });
    expect(fields).toEqual([]);
  });

  it("retries a transient 503 (read-metadata is a read despite the POST verb)", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(200, { data: [] }) as never);

    const fields = await readDatasourceMetadata(auth, "DS-luid", { sleep, jitterFn: () => 0 });
    expect(fields).toEqual([]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
  });
});
