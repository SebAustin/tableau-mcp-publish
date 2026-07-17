import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("node:fs/promises", () => ({ stat: vi.fn(), readFile: vi.fn() }));
vi.mock("undici", () => ({ request: vi.fn() }));

import { request } from "undici";
import { TableauRestClient, TableauApiError } from "../src/restClient.js";
import type { Config } from "../src/config.js";
import type { RetryDeps } from "../src/rest/retry.js";
import {
  buildExtractRefreshTaskXml,
  parseExtractRefreshTaskList,
  parseExtractRefreshTaskResponse,
  validateScheduleSpec,
  type ScheduleSpec,
} from "../src/rest/schedules.js";

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

const dailySpec: ScheduleSpec = {
  frequency: "Daily",
  frequencyDetails: { start: "03:30:00", intervals: [] },
};

beforeEach(() => vi.clearAllMocks());

// ---------------------------------------------------------------------------
// Pure module: XML builders + validation + response parsing
// ---------------------------------------------------------------------------

describe("rest/schedules — request XML shapes", () => {
  it("builds the documented create body for a datasource target with a Daily schedule", () => {
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "FullRefresh", dailySpec);
    expect(xml).toBe(
      '<tsRequest><extractRefresh type="FullRefresh"><datasource id="DS1" /></extractRefresh>' +
        '<schedule frequency="Daily"><frequencyDetails start="03:30:00"></frequencyDetails></schedule>' +
        "</tsRequest>",
    );
  });

  it("builds the workbook variant (<workbook id> instead of <datasource id>)", () => {
    const xml = buildExtractRefreshTaskXml({ kind: "workbook", id: "WB1" }, "FullRefresh", dailySpec);
    expect(xml).toContain('<workbook id="WB1" />');
    expect(xml).not.toContain("<datasource");
  });

  it("emits <schedule> as a SIBLING of <extractRefresh>, not nested inside it", () => {
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "FullRefresh", dailySpec);
    const extractRefreshEnd = xml.indexOf("</extractRefresh>");
    const scheduleStart = xml.indexOf("<schedule");
    expect(scheduleStart).toBeGreaterThan(extractRefreshEnd);
  });

  it("renders an Hourly schedule with hours intervals + optional end", () => {
    const spec: ScheduleSpec = {
      frequency: "Hourly",
      frequencyDetails: { start: "06:00:00", end: "20:00:00", intervals: [{ hours: "4" }] },
    };
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "FullRefresh", spec);
    expect(xml).toContain(
      '<frequencyDetails start="06:00:00" end="20:00:00"><intervals><interval hours="4" /></intervals></frequencyDetails>',
    );
  });

  it("renders a Weekly schedule with multiple weekDay intervals", () => {
    const spec: ScheduleSpec = {
      frequency: "Weekly",
      frequencyDetails: {
        start: "02:00:00",
        intervals: [{ weekDay: "Monday" }, { weekDay: "Thursday" }],
      },
    };
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "FullRefresh", spec);
    expect(xml).toContain(
      '<intervals><interval weekDay="Monday" /><interval weekDay="Thursday" /></intervals>',
    );
  });

  it("renders a Monthly schedule with a monthDay interval", () => {
    const spec: ScheduleSpec = {
      frequency: "Monthly",
      frequencyDetails: { start: "01:00:00", intervals: [{ monthDay: "1" }] },
    };
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "FullRefresh", spec);
    expect(xml).toContain("<intervals><interval monthDay=\"1\" /></intervals>");
  });

  it("XML-escapes the target id", () => {
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: 'DS"1' }, "FullRefresh", dailySpec);
    expect(xml).toContain('<datasource id="DS&quot;1" />');
  });

  it("emits the IncrementalRefresh type token when requested", () => {
    const xml = buildExtractRefreshTaskXml({ kind: "datasource", id: "DS1" }, "IncrementalRefresh", dailySpec);
    expect(xml).toContain('<extractRefresh type="IncrementalRefresh">');
  });
});

describe("rest/schedules — validateScheduleSpec", () => {
  it("accepts a Daily spec with no intervals", () => {
    expect(() => validateScheduleSpec(dailySpec)).not.toThrow();
  });

  it("rejects Hourly with no hours interval", () => {
    expect(() =>
      validateScheduleSpec({ frequency: "Hourly", frequencyDetails: { start: "01:00:00", intervals: [] } }),
    ).toThrow(/Hourly frequency requires at least one interval with "hours"/);
  });

  it("accepts Hourly with a hours interval", () => {
    expect(() =>
      validateScheduleSpec({
        frequency: "Hourly",
        frequencyDetails: { start: "01:00:00", intervals: [{ hours: "4" }] },
      }),
    ).not.toThrow();
  });

  it("rejects Weekly with no weekDay interval", () => {
    expect(() =>
      validateScheduleSpec({
        frequency: "Weekly",
        frequencyDetails: { start: "01:00:00", intervals: [{ hours: "4" }] },
      }),
    ).toThrow(/Weekly frequency requires at least one interval with "weekDay"/);
  });

  it("rejects Monthly with zero monthDay intervals", () => {
    expect(() =>
      validateScheduleSpec({ frequency: "Monthly", frequencyDetails: { start: "01:00:00", intervals: [] } }),
    ).toThrow(/Monthly frequency requires exactly one interval with "monthDay"/);
  });

  it("rejects Monthly with more than one monthDay interval", () => {
    expect(() =>
      validateScheduleSpec({
        frequency: "Monthly",
        frequencyDetails: {
          start: "01:00:00",
          intervals: [{ monthDay: "1" }, { monthDay: "15" }],
        },
      }),
    ).toThrow(/Monthly frequency requires exactly one interval with "monthDay"/);
  });

  it("rejects an interval that sets more than one of hours/weekDay/monthDay", () => {
    expect(() =>
      validateScheduleSpec({
        frequency: "Hourly",
        frequencyDetails: {
          start: "01:00:00",
          intervals: [{ hours: "4", weekDay: "Monday" } as never],
        },
      }),
    ).toThrow(/must set exactly one of hours, weekDay, or monthDay/);
  });

  it("lists every problem in a single error when several are wrong at once", () => {
    let message = "";
    try {
      validateScheduleSpec({ frequency: "Monthly", frequencyDetails: { start: "01:00:00", intervals: [] } });
    } catch (err) {
      message = err instanceof Error ? err.message : String(err);
    }
    expect(message).toMatch(/Invalid schedule spec for frequency "Monthly"/);
    expect(message).toMatch(/monthDay/);
  });
});

describe("rest/schedules — response parsing", () => {
  it("parses a single-task response with a nested schedule (frequency + nextRunAt)", () => {
    const task = parseExtractRefreshTaskResponse({
      extractRefresh: {
        id: "TASK1",
        type: "FullRefresh",
        datasource: { id: "DS1" },
        schedule: {
          frequency: "Daily",
          nextRunAt: "2026-07-18T03:30:00Z",
          frequencyDetails: { start: "03:30:00" },
        },
      },
    });
    expect(task).toEqual({
      taskId: "TASK1",
      type: "FullRefresh",
      targetKind: "datasource",
      targetId: "DS1",
      frequency: "Daily",
      nextRunAt: "2026-07-18T03:30:00Z",
      start: "03:30:00",
    });
  });

  it("also accepts a { task: { extractRefresh } } wrapper (VERIFY-LIVE shape uncertainty)", () => {
    const task = parseExtractRefreshTaskResponse({
      task: { extractRefresh: { id: "TASK2", workbook: { id: "WB1" } } },
    });
    expect(task).toMatchObject({ taskId: "TASK2", targetKind: "workbook", targetId: "WB1" });
  });

  it("defaults type to FullRefresh when the response omits it", () => {
    const task = parseExtractRefreshTaskResponse({ extractRefresh: { id: "TASK3" } });
    expect(task.type).toBe("FullRefresh");
  });

  it("throws a clear error when extractRefresh is missing entirely", () => {
    expect(() => parseExtractRefreshTaskResponse({})).toThrow(/missing extractRefresh/);
  });

  it("throws a clear error when the task id is missing", () => {
    expect(() => parseExtractRefreshTaskResponse({ extractRefresh: {} })).toThrow(/missing id/);
  });

  it("parses a list response, tolerating Tableau's single-item-collapses-to-object XML→JSON quirk", () => {
    const singleTask = parseExtractRefreshTaskList({
      tasks: { task: { extractRefresh: { id: "TASK1", datasource: { id: "DS1" } } } },
    });
    expect(singleTask).toHaveLength(1);
    expect(singleTask[0]).toMatchObject({ taskId: "TASK1", targetId: "DS1" });

    const multiTask = parseExtractRefreshTaskList({
      tasks: {
        task: [
          { extractRefresh: { id: "TASK1", datasource: { id: "DS1" } } },
          { extractRefresh: { id: "TASK2", workbook: { id: "WB1" } } },
        ],
      },
    });
    expect(multiTask.map((t) => t.taskId)).toEqual(["TASK1", "TASK2"]);
  });

  it("returns an empty list when tasks/task is absent", () => {
    expect(parseExtractRefreshTaskList({})).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// TableauRestClient wiring (mocked undici)
// ---------------------------------------------------------------------------

describe("TableauRestClient — schedule wiring", () => {
  it("scheduleRefresh POSTs the create body and returns the parsed task", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    mockedRequest.mockResolvedValueOnce(
      response(200, {
        extractRefresh: {
          id: "TASK1",
          type: "FullRefresh",
          datasource: { id: "DS1" },
          schedule: { frequency: "Daily", nextRunAt: "2026-07-18T03:30:00Z" },
        },
      }) as never,
    );

    const task = await client.scheduleRefresh({ kind: "datasource", id: "DS1" }, "FullRefresh", dailySpec);
    expect(task).toMatchObject({ taskId: "TASK1", frequency: "Daily", nextRunAt: "2026-07-18T03:30:00Z" });

    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe("https://x.online.tableau.com/api/3.28/sites/S/tasks/extractRefreshes");
    const call = opts as { method: string; body: string };
    expect(call.method).toBe("POST");
    expect(call.body).toContain('<extractRefresh type="FullRefresh">');
  });

  it("scheduleRefresh rejects an invalid spec before making any request", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    await expect(
      client.scheduleRefresh(
        { kind: "datasource", id: "DS1" },
        "FullRefresh",
        { frequency: "Hourly", frequencyDetails: { start: "01:00:00", intervals: [] } },
      ),
    ).rejects.toThrow(/Hourly frequency requires/);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("does NOT retry scheduleRefresh on a 503 (POST is non-idempotent by default)", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep });
    mockedRequest.mockResolvedValueOnce(response(503, {}) as never);

    await expect(
      client.scheduleRefresh({ kind: "datasource", id: "DS1" }, "FullRefresh", dailySpec),
    ).rejects.toThrow(TableauApiError);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("updateRefreshSchedule POSTs to the task id path", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    mockedRequest.mockResolvedValueOnce(
      response(200, { extractRefresh: { id: "TASK1", datasource: { id: "DS1" } } }) as never,
    );
    await client.updateRefreshSchedule("TASK1", { kind: "datasource", id: "DS1" }, "FullRefresh", dailySpec);
    const [url] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe(
      "https://x.online.tableau.com/api/3.28/sites/S/tasks/extractRefreshes/TASK1",
    );
  });

  it("listRefreshSchedules GETs the collection and retries a transient 503", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep, jitterFn: () => 0 });
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(
        response(200, {
          tasks: { task: [{ extractRefresh: { id: "TASK1", datasource: { id: "DS1" } } }] },
        }) as never,
      );

    const tasks = await client.listRefreshSchedules();
    expect(tasks).toEqual([{ taskId: "TASK1", type: "FullRefresh", targetKind: "datasource", targetId: "DS1" }]);
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
  });

  it("getRefreshSchedule GETs a single task by id", async () => {
    const client = await signedInClient({ sleep: vi.fn() });
    mockedRequest.mockResolvedValueOnce(
      response(200, { extractRefresh: { id: "TASK1", datasource: { id: "DS1" } } }) as never,
    );
    const task = await client.getRefreshSchedule("TASK1");
    expect(task.taskId).toBe("TASK1");
    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe(
      "https://x.online.tableau.com/api/3.28/sites/S/tasks/extractRefreshes/TASK1",
    );
    expect((opts as { method: string }).method).toBe("GET");
  });

  it("deleteRefreshSchedule DELETEs the task and is retriable on a transient 503", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep, jitterFn: () => 0 });
    mockedRequest
      .mockResolvedValueOnce(response(503, {}) as never)
      .mockResolvedValueOnce(response(204, {}) as never);

    await client.deleteRefreshSchedule("TASK1");
    expect(mockedRequest).toHaveBeenCalledTimes(2);
    const [url, opts] = mockedRequest.mock.calls[1]!;
    expect(String(url)).toBe(
      "https://x.online.tableau.com/api/3.28/sites/S/tasks/extractRefreshes/TASK1",
    );
    expect((opts as { method: string }).method).toBe("DELETE");
  });

  it("runRefreshScheduleNow POSTs an empty tsRequest to the runNow path and is not retried", async () => {
    const sleep = vi.fn();
    const client = await signedInClient({ sleep });
    mockedRequest.mockResolvedValueOnce(response(429, {}, { "retry-after": "1" }) as never);

    await expect(client.runRefreshScheduleNow("TASK1")).rejects.toThrow(TableauApiError);
    expect(sleep).not.toHaveBeenCalled();
    const [url, opts] = mockedRequest.mock.calls[0]!;
    expect(String(url)).toBe(
      "https://x.online.tableau.com/api/3.28/sites/S/tasks/extractRefreshes/TASK1/runNow",
    );
    expect((opts as { method: string; body: string }).method).toBe("POST");
    expect((opts as { method: string; body: string }).body).toBe("<tsRequest></tsRequest>");
  });
});
