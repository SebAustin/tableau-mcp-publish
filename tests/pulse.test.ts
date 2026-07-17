import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));
vi.mock("undici", () => ({ request: vi.fn() }));

import { request } from "undici";
import { TableauRestClient, TableauApiError } from "../src/restClient.js";
import type { Config } from "../src/config.js";
import type { RetryDeps } from "../src/rest/retry.js";
import {
  buildCreateDefinitionBody,
  buildCreateMetricBody,
  createDefinition,
  CreatePulseDefinitionInputSchema,
  CreatePulseMetricInputSchema,
  deleteDefinition,
  getDefinition,
  listDefinitions,
  listMetrics,
  parseDefinition,
  parseDefinitionList,
  parseMetric,
  parseMetricList,
  validatePulsePreflight,
  type CreatePulseDefinitionInput,
  type CreatePulseMetricInput,
} from "../src/rest/pulse.js";
import type { VdsField } from "../src/rest/vds.js";

const mockedRequest = vi.mocked(request);
const pulseAuth = { server: "https://x.online.tableau.com", token: "T" };
const noSleep = vi.fn().mockResolvedValue(undefined);

function response(statusCode: number, obj: unknown, headers: Record<string, string> = {}) {
  return {
    statusCode,
    headers,
    body: { json: async () => obj, text: async () => JSON.stringify(obj) },
  };
}

beforeEach(() => vi.clearAllMocks());

// ---------------------------------------------------------------------------
// Sample fixture: the exact Sales/Order Date definition from the plan brief
// ---------------------------------------------------------------------------

const salesDefinitionInput: CreatePulseDefinitionInput = CreatePulseDefinitionInputSchema.parse({
  name: "Sales",
  datasourceLuid: "DS-luid",
  measure: { field: "Sales", aggregation: "SUM" },
  timeDimension: { field: "Order Date" },
});

const EXPECTED_WIRE_BODY = {
  name: "Sales",
  specification: {
    basic_specification: {
      measure: { field: "Sales", aggregation: "AGGREGATION_SUM" },
      time_dimension: { field: "Order Date" },
      filters: [],
    },
    is_running_total: false,
    datasource: { id: "DS-luid" },
  },
  extension_options: {
    allowed_dimensions: [],
    allowed_granularities: [],
    offset_from_today: 0,
    correlation_candidate_definition_ids: [],
    use_dynamic_offset: false,
  },
  representation_options: {
    type: "NUMBER_FORMAT_TYPE_NUMBER",
    sentiment_type: "SENTIMENT_TYPE_NONE",
  },
  insights_options: { show_insights: true, settings: [] },
  comparisons: { comparisons: [] },
  datasource_goals: [],
  related_links: [],
  certification: { is_certified: false },
};

// ---------------------------------------------------------------------------
// Pure module: schemas + wire-body builders
// ---------------------------------------------------------------------------

describe("rest/pulse — buildCreateDefinitionBody", () => {
  it("matches the official pulse-api-utilities shape EXACTLY for a Sales/Order Date definition", () => {
    expect(buildCreateDefinitionBody(salesDefinitionInput)).toEqual(EXPECTED_WIRE_BODY);
  });

  it("keeps extension_options/representation_options/insights_options/comparisons TOP-LEVEL, not nested in specification", () => {
    const body = buildCreateDefinitionBody(salesDefinitionInput) as Record<string, unknown>;
    for (const key of [
      "extension_options",
      "representation_options",
      "insights_options",
      "comparisons",
      "datasource_goals",
      "related_links",
      "certification",
    ]) {
      expect(Object.keys(body)).toContain(key);
      expect(Object.keys(body.specification as Record<string, unknown>)).not.toContain(key);
    }
  });

  it("threads allowedDimensions, numberFormat, sentiment, isRunningTotal, and filters through", () => {
    const input = CreatePulseDefinitionInputSchema.parse({
      name: "AOV",
      datasourceLuid: "DS2",
      measure: { field: "Profit", aggregation: "AVERAGE" },
      timeDimension: { field: "Ship Date" },
      filters: [{ field: "Region", operator: "eq", values: ["West"] }],
      allowedDimensions: ["Region", "Segment"],
      numberFormat: "CURRENCY",
      sentiment: "UP_IS_GOOD",
      isRunningTotal: true,
    });
    const body = buildCreateDefinitionBody(input);
    expect(body.specification.basic_specification.measure.aggregation).toBe("AGGREGATION_AVERAGE");
    expect(body.specification.basic_specification.filters).toEqual([
      { field: "Region", operator: "eq", values: ["West"] },
    ]);
    expect(body.specification.is_running_total).toBe(true);
    expect(body.extension_options.allowed_dimensions).toEqual(["Region", "Segment"]);
    expect(body.representation_options).toEqual({
      type: "NUMBER_FORMAT_TYPE_CURRENCY",
      sentiment_type: "SENTIMENT_TYPE_UP_IS_GOOD",
    });
  });
});

describe("rest/pulse — CreatePulseDefinitionInputSchema", () => {
  it("rejects an empty name", () => {
    expect(() =>
      CreatePulseDefinitionInputSchema.parse({
        name: "",
        datasourceLuid: "DS",
        measure: { field: "Sales", aggregation: "SUM" },
        timeDimension: { field: "Order Date" },
      }),
    ).toThrow();
  });

  it("rejects an aggregation outside the enum", () => {
    expect(() =>
      CreatePulseDefinitionInputSchema.parse({
        name: "Sales",
        datasourceLuid: "DS",
        measure: { field: "Sales", aggregation: "TOTAL" },
        timeDimension: { field: "Order Date" },
      }),
    ).toThrow();
  });
});

describe("rest/pulse — buildCreateMetricBody", () => {
  it("matches the official research-brief sample shape exactly, with defaults applied", () => {
    const input: CreatePulseMetricInput = CreatePulseMetricInputSchema.parse({ definitionId: "DEF1" });
    expect(buildCreateMetricBody(input)).toEqual({
      definition_id: "DEF1",
      specification: {
        filters: [],
        measurement_period: { granularity: "GRANULARITY_BY_MONTH", range: "RANGE_LAST_COMPLETE" },
        comparison: { comparison: "TIME_COMPARISON_PREVIOUS_PERIOD" },
      },
    });
  });

  it("threads explicit granularity/range/comparison through", () => {
    const input = CreatePulseMetricInputSchema.parse({
      definitionId: "DEF1",
      granularity: "GRANULARITY_BY_WEEK",
      range: "RANGE_CURRENT_PARTIAL",
      comparison: "TIME_COMPARISON_YEAR_AGO_PERIOD",
    });
    expect(buildCreateMetricBody(input).specification).toEqual({
      filters: [],
      measurement_period: { granularity: "GRANULARITY_BY_WEEK", range: "RANGE_CURRENT_PARTIAL" },
      comparison: { comparison: "TIME_COMPARISON_YEAR_AGO_PERIOD" },
    });
  });
});

// ---------------------------------------------------------------------------
// Pure module: response parsing
// ---------------------------------------------------------------------------

describe("rest/pulse — parseDefinition / parseDefinitionList", () => {
  it("parses a bare-object definition response", () => {
    expect(parseDefinition({ id: "DEF1", name: "Sales", specification: { datasource: { id: "DS1" } } })).toEqual({
      definitionId: "DEF1",
      name: "Sales",
      datasourceLuid: "DS1",
    });
  });

  it("parses a { metric_definition: {...} } wrapped response", () => {
    expect(parseDefinition({ metric_definition: { id: "DEF1", name: "Sales" } })).toEqual({
      definitionId: "DEF1",
      name: "Sales",
    });
  });

  it("throws a clear error when id or name is missing", () => {
    expect(() => parseDefinition({ id: "DEF1" })).toThrow(/missing id or name/);
    expect(() => parseDefinition({})).toThrow(/missing definition/);
  });

  it("parses a { metric_definitions: [...] } wrapped list", () => {
    const list = parseDefinitionList({
      metric_definitions: [
        { id: "DEF1", name: "Sales" },
        { id: "DEF2", name: "Profit" },
      ],
    });
    expect(list.map((d) => d.definitionId)).toEqual(["DEF1", "DEF2"]);
  });

  it("parses a bare top-level array list (both response shapes tolerated)", () => {
    const list = parseDefinitionList([{ id: "DEF1", name: "Sales" }]);
    expect(list).toEqual([{ definitionId: "DEF1", name: "Sales" }]);
  });

  it("returns an empty list when metric_definitions is absent", () => {
    expect(parseDefinitionList({})).toEqual([]);
  });
});

describe("rest/pulse — parseMetric / parseMetricList", () => {
  it("parses a bare-object metric response", () => {
    expect(parseMetric({ id: "M1", definition_id: "DEF1" })).toEqual({ metricId: "M1", definitionId: "DEF1" });
  });

  it("parses a { metric: {...} } wrapped response", () => {
    expect(parseMetric({ metric: { id: "M1" } })).toEqual({ metricId: "M1" });
  });

  it("throws a clear error when the metric id is missing", () => {
    expect(() => parseMetric({})).toThrow(/missing metric/);
  });

  it("parses both a { metrics: [...] } wrapper and a bare array", () => {
    expect(parseMetricList({ metrics: [{ id: "M1" }] })).toEqual([{ metricId: "M1" }]);
    expect(parseMetricList([{ id: "M1" }])).toEqual([{ metricId: "M1" }]);
    expect(parseMetricList({})).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Pure module: pre-flight validation
// ---------------------------------------------------------------------------

const goodFields: VdsField[] = [
  { fieldName: "Sales", fieldCaption: "Sales", dataType: "REAL", defaultAggregation: "SUM" },
  { fieldName: "Order Date", fieldCaption: "Order Date", dataType: "DATE" },
  { fieldName: "Region", fieldCaption: "Region", dataType: "STRING" },
];

describe("rest/pulse — validatePulsePreflight", () => {
  it("passes with no warnings when fields + aggregation all match", () => {
    const result = validatePulsePreflight(goodFields, { field: "Sales", aggregation: "SUM" }, { field: "Order Date" });
    expect(result.warnings).toEqual([]);
  });

  it("warns (does not throw) when defaultAggregation disagrees with the requested aggregation", () => {
    const fields: VdsField[] = [
      { fieldName: "Sales", dataType: "REAL", defaultAggregation: "AVERAGE" },
      { fieldName: "Order Date", dataType: "DATE" },
    ];
    const result = validatePulsePreflight(fields, { field: "Sales", aggregation: "SUM" }, { field: "Order Date" });
    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toMatch(/defaultAggregation.*AVERAGE.*SUM/s);
  });

  it("throws with the available field list when the measure field is missing", () => {
    expect(() =>
      validatePulsePreflight(goodFields, { field: "Nonexistent", aggregation: "SUM" }, { field: "Order Date" }),
    ).toThrow(/measure field "Nonexistent" was not found.*Sales.*Order Date.*Region/s);
  });

  it("throws with the available field list when the time_dimension field is missing", () => {
    expect(() =>
      validatePulsePreflight(goodFields, { field: "Sales", aggregation: "SUM" }, { field: "Nonexistent" }),
    ).toThrow(/time_dimension field "Nonexistent" was not found/);
  });

  it("throws when the time_dimension field exists but is not date-like", () => {
    expect(() =>
      validatePulsePreflight(goodFields, { field: "Sales", aggregation: "SUM" }, { field: "Region" }),
    ).toThrow(/not date-like.*Order Date/s);
  });

  it("warns instead of throwing when the time_dimension dataType is unreported", () => {
    const fields: VdsField[] = [
      { fieldName: "Sales", dataType: "REAL", defaultAggregation: "SUM" },
      { fieldName: "Order Date" }, // no dataType
    ];
    const result = validatePulsePreflight(fields, { field: "Sales", aggregation: "SUM" }, { field: "Order Date" });
    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toMatch(/did not report a dataType/);
  });
});

// ---------------------------------------------------------------------------
// Pure module: direct HTTP wiring (auth object, no TableauRestClient)
// ---------------------------------------------------------------------------

describe("rest/pulse — createDefinition / listDefinitions / getDefinition / deleteDefinition (direct)", () => {
  it("POSTs to /api/-/pulse/definitions with JSON content-type and the exact wire body", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(200, { id: "DEF1", name: "Sales", specification: { datasource: { id: "DS-luid" } } }) as never,
    );

    const definition = await createDefinition(pulseAuth, salesDefinitionInput, { sleep: noSleep });
    expect(definition).toEqual({ definitionId: "DEF1", name: "Sales", datasourceLuid: "DS-luid" });

    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/definitions");
    const call = opts as { method: string; headers: Record<string, string>; body: string };
    expect(call.method).toBe("POST");
    expect(call.headers["Content-Type"]).toBe("application/json");
    expect(call.headers["X-Tableau-Auth"]).toBe("T");
    expect(JSON.parse(call.body)).toEqual(EXPECTED_WIRE_BODY);
  });

  it("does NOT retry createDefinition on a transient 503 (POST is non-idempotent per this module's policy)", async () => {
    const sleep = vi.fn();
    mockedRequest.mockResolvedValueOnce(response(503, {}) as never);
    await expect(createDefinition(pulseAuth, salesDefinitionInput, { sleep })).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("surfaces a Cloud-only/Pulse-disabled 404 actionably", async () => {
    mockedRequest.mockResolvedValueOnce(response(404, {}) as never);
    let caught: unknown;
    try {
      await createDefinition(pulseAuth, salesDefinitionInput, { sleep: noSleep });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    const err = caught as TableauApiError;
    expect(err.status).toBe(404);
    expect(err.summary).toMatch(/not found|unavailable/i);
    expect(err.detail).toMatch(/Cloud-only|Site Settings/i);
  });

  it("surfaces a Pulse-not-enabled response actionably", async () => {
    mockedRequest.mockResolvedValueOnce(
      response(400, { error: { code: "400011", summary: "Bad Request", detail: "Pulse is not enabled for this site." } }) as never,
    );
    let caught: unknown;
    try {
      await createDefinition(pulseAuth, salesDefinitionInput, { sleep: noSleep });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(TableauApiError);
    expect((caught as TableauApiError).summary).toMatch(/not enabled/i);
  });

  it("listDefinitions GETs with page_size=1000 and retries a transient 503 (GET is retriable)", async () => {
    const sleep = vi.fn();
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(200, { metric_definitions: [{ id: "DEF1", name: "Sales" }] }) as never);

    const definitions = await listDefinitions(pulseAuth, { sleep, jitterFn: () => 0 });
    expect(definitions).toEqual([{ definitionId: "DEF1", name: "Sales" }]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);

    const [url] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/definitions?page_size=1000");
  });

  it("listDefinitions tolerates a bare top-level array response", async () => {
    mockedRequest.mockResolvedValueOnce(response(200, [{ id: "DEF1", name: "Sales" }]) as never);
    const definitions = await listDefinitions(pulseAuth, { sleep: noSleep });
    expect(definitions).toEqual([{ definitionId: "DEF1", name: "Sales" }]);
  });

  it("getDefinition GETs /definitions/{id} and retries a transient 503", async () => {
    const sleep = vi.fn();
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(200, { id: "DEF1", name: "Sales" }) as never);

    const definition = await getDefinition(pulseAuth, "DEF1", { sleep, jitterFn: () => 0 });
    expect(definition).toEqual({ definitionId: "DEF1", name: "Sales" });
    const [url] = mockedRequest.mock.calls[1]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/definitions/DEF1");
  });

  it("rejects an empty definitionId before making a request (getDefinition/deleteDefinition/listMetrics)", async () => {
    await expect(getDefinition(pulseAuth, "", { sleep: noSleep })).rejects.toThrow(/definitionId/);
    await expect(deleteDefinition(pulseAuth, "", { sleep: noSleep })).rejects.toThrow(/definitionId/);
    await expect(listMetrics(pulseAuth, "", { sleep: noSleep })).rejects.toThrow(/definitionId/);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("deleteDefinition DELETEs /definitions/{id} and does NOT retry a transient 503", async () => {
    const sleep = vi.fn();
    mockedRequest.mockResolvedValueOnce(response(503, {}) as never);
    await expect(deleteDefinition(pulseAuth, "DEF1", { sleep })).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("deleteDefinition succeeds on 204 with no body parsing", async () => {
    mockedRequest.mockResolvedValueOnce(response(204, {}) as never);
    await expect(deleteDefinition(pulseAuth, "DEF1", { sleep: noSleep })).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// TableauRestClient wiring (mocked undici) — createPulseDefinition pre-flight
// ---------------------------------------------------------------------------

const cfg: Config = {
  server: "https://x.online.tableau.com",
  siteName: "s",
  patName: "p",
  patValue: "secret-value",
  apiVersion: "3.28",
  sidecarHost: "127.0.0.1",
  sidecarPort: 8899,
};

async function signedInClient(retryDeps: RetryDeps): Promise<TableauRestClient> {
  mockedRequest.mockResolvedValueOnce(
    response(200, { credentials: { token: "T", site: { id: "S" }, user: { id: "U" } } }) as never,
  );
  const client = new TableauRestClient(cfg, undefined, retryDeps);
  await client.signIn();
  mockedRequest.mockClear();
  return client;
}

describe("TableauRestClient — createPulseDefinition pre-flight", () => {
  it("passes pre-flight (matching fields/aggregation) with zero pre-flight warnings, then creates", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest
      // VDS read-metadata
      .mockResolvedValueOnce(
        response(200, {
          data: [
            { fieldName: "Sales", dataType: "REAL", defaultAggregation: "SUM" },
            { fieldName: "Order Date", dataType: "DATE" },
          ],
        }) as never,
      )
      // Pulse create-definition
      .mockResolvedValueOnce(response(200, { id: "DEF1", name: "Sales" }) as never);

    const { definition, warnings } = await client.createPulseDefinition(salesDefinitionInput);
    expect(definition).toEqual({ definitionId: "DEF1", name: "Sales" });
    expect(warnings).toEqual([]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);

    const [vdsUrl] = mockedRequest.mock.calls[0]!;
    expect(String(vdsUrl)).toBe("https://x.online.tableau.com/api/v1/vizql-data-service/read-metadata");
    const [pulseUrl] = mockedRequest.mock.calls[1]!;
    expect(String(pulseUrl)).toBe("https://x.online.tableau.com/api/-/pulse/definitions");
  });

  it("warns (soft) on an aggregation mismatch but still creates the definition", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest
      .mockResolvedValueOnce(
        response(200, {
          data: [
            { fieldName: "Sales", dataType: "REAL", defaultAggregation: "AVERAGE" },
            { fieldName: "Order Date", dataType: "DATE" },
          ],
        }) as never,
      )
      .mockResolvedValueOnce(response(200, { id: "DEF1", name: "Sales" }) as never);

    const { definition, warnings } = await client.createPulseDefinition(salesDefinitionInput);
    expect(definition.definitionId).toBe("DEF1");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/AVERAGE.*SUM/s);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
  });

  it("fails loudly (before calling Pulse) when the measure field does not exist, listing available fields", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest.mockResolvedValueOnce(
      response(200, { data: [{ fieldName: "Order Date", dataType: "DATE" }] }) as never,
    );

    await expect(client.createPulseDefinition(salesDefinitionInput)).rejects.toThrow(
      /measure field "Sales" was not found.*Order Date/s,
    );
    // Only the VDS call happened — Pulse create was never reached.
    expect(mockedRequest).toHaveBeenCalledTimes(1);
  });

  it("is resilient when VDS itself is unavailable: warns and still creates instead of blocking", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest
      // VDS 404s (unavailable)
      .mockResolvedValueOnce(response(404, {}) as never)
      // Pulse create still proceeds
      .mockResolvedValueOnce(response(200, { id: "DEF1", name: "Sales" }) as never);

    const { definition, warnings } = await client.createPulseDefinition(salesDefinitionInput);
    expect(definition.definitionId).toBe("DEF1");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/Pre-flight validation skipped.*VDS/s);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
  });

  it("honors skipPreflight=true by never calling VDS", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest.mockResolvedValueOnce(response(200, { id: "DEF1", name: "Sales" }) as never);

    const { definition, warnings } = await client.createPulseDefinition(salesDefinitionInput, {
      skipPreflight: true,
    });
    expect(definition.definitionId).toBe("DEF1");
    expect(warnings).toEqual(["Pre-flight validation skipped (skipPreflight=true): field existence and aggregation were not verified against the datasource."]);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    const [url] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/definitions");
  });
});

describe("TableauRestClient — Pulse wiring (list/get/delete/metric)", () => {
  it("listPulseDefinitions GETs the collection", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest.mockResolvedValueOnce(
      response(200, { metric_definitions: [{ id: "DEF1", name: "Sales" }] }) as never,
    );
    const definitions = await client.listPulseDefinitions();
    expect(definitions).toEqual([{ definitionId: "DEF1", name: "Sales" }]);
  });

  it("deletePulseDefinition DELETEs by id", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest.mockResolvedValueOnce(response(204, {}) as never);
    await client.deletePulseDefinition("DEF1");
    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/definitions/DEF1");
    expect((opts as { method: string }).method).toBe("DELETE");
  });

  it("createPulseMetric POSTs the create-metric body", async () => {
    const client = await signedInClient({ sleep: noSleep });
    mockedRequest.mockResolvedValueOnce(response(200, { id: "M1", definition_id: "DEF1" }) as never);
    const metric = await client.createPulseMetric(CreatePulseMetricInputSchema.parse({ definitionId: "DEF1" }));
    expect(metric).toEqual({ metricId: "M1", definitionId: "DEF1" });
    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/-/pulse/metrics");
    expect(JSON.parse((opts as { body: string }).body)).toEqual({
      definition_id: "DEF1",
      specification: {
        filters: [],
        measurement_period: { granularity: "GRANULARITY_BY_MONTH", range: "RANGE_LAST_COMPLETE" },
        comparison: { comparison: "TIME_COMPARISON_PREVIOUS_PERIOD" },
      },
    });
  });
});
