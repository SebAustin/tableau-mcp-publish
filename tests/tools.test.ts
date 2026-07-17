import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { z } from "zod";
import { registerAllTools } from "../src/index.js";
import type { Config } from "../src/config.js";

interface RegisteredTool {
  config: { description?: string; inputSchema?: z.ZodRawShape; outputSchema?: z.ZodRawShape };
  handler: (args: Record<string, unknown>) => Promise<{ structuredContent?: Record<string, unknown> }>;
}

class FakeServer {
  tools = new Map<string, RegisteredTool>();
  registerTool(name: string, config: RegisteredTool["config"], handler: RegisteredTool["handler"]) {
    this.tools.set(name, { config, handler });
  }
}

const cfg: Config = {
  server: "https://x.online.tableau.com",
  siteName: "s",
  patName: "p",
  patValue: "secret",
  apiVersion: "3.28",
  sidecarHost: "127.0.0.1",
  sidecarPort: 8899,
};

function makeCtx() {
  const rest = {
    listProjects: vi.fn().mockResolvedValue([{ id: "PID", name: "Sales" }]),
    resolveProjectId: vi.fn().mockResolvedValue("PID"),
    createProject: vi.fn().mockResolvedValue({ id: "P2", name: "New" }),
    publishDatasource: vi
      .fn()
      .mockResolvedValue({ id: "DS", url: "https://x/#/site/s/datasources/DS" }),
    publishWorkbook: vi
      .fn()
      .mockResolvedValue({ id: "WB", url: "https://x/#/site/s/workbooks/WB" }),
    getDatasource: vi.fn().mockResolvedValue({ id: "DS", name: "DS", contentUrl: "DS_url" }),
    listContent: vi.fn().mockResolvedValue([]),
    refreshDatasource: vi.fn().mockResolvedValue(undefined),
    deleteContent: vi.fn().mockResolvedValue(undefined),
    setPermissions: vi.fn().mockResolvedValue(undefined),
    getDatasourceFields: vi.fn().mockResolvedValue([
      { fieldName: "Sales", fieldCaption: "Sales", dataType: "REAL", defaultAggregation: "SUM" },
      { fieldName: "Order Date", fieldCaption: "Order Date", dataType: "DATE" },
    ]),
  };
  const sidecar = {
    buildDatasourceFromQuery: vi.fn().mockResolvedValue({ tdsxPath: "/tmp/x.tdsx" }),
    buildDatasourceFromTable: vi.fn().mockResolvedValue({ tdsxPath: "/tmp/t.tdsx" }),
    buildStarterWorkbook: vi.fn().mockResolvedValue({ twbxPath: "/tmp/w.twbx" }),
    // Returns both tdsxPath and hyperPath (the embedded-extract path required by build_from_plan).
    buildDatasourceFromFile: vi
      .fn()
      .mockResolvedValue({ tdsxPath: "/tmp/f.tdsx", hyperPath: "/tmp/f.hyper" }),
    buildDashboardWorkbook: vi.fn().mockResolvedValue({ twbxPath: "/tmp/d.twbx" }),
  };
  return { config: cfg, rest, sidecar };
}

let server: FakeServer;
let ctx: ReturnType<typeof makeCtx>;
let tmpDir: string | undefined;

beforeEach(() => {
  server = new FakeServer();
  ctx = makeCtx();
  registerAllTools(server as never, ctx as never);
});

afterEach(() => {
  if (tmpDir) {
    rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = undefined;
  }
});

async function invoke(name: string, rawArgs: Record<string, unknown>) {
  const tool = server.tools.get(name);
  if (!tool) throw new Error(`tool ${name} not registered`);
  // Parse through the declared input schema so zod defaults (e.g. overwrite) apply.
  const parsed = z.object(tool.config.inputSchema ?? {}).parse(rawArgs);
  return tool.handler(parsed as Record<string, unknown>);
}

describe("tool registration", () => {
  it("registers all 16 tools, each with a description and declared schemas", () => {
    expect(server.tools.size).toBe(16);
    for (const { config } of server.tools.values()) {
      expect(config.description && config.description.length).toBeGreaterThan(0);
      expect(config.inputSchema).toBeDefined();
      expect(config.outputSchema).toBeDefined();
    }
  });

  it("includes the headline and lifecycle tools", () => {
    const names = [...server.tools.keys()];
    for (const t of [
      "create_datasource_from_query",
      "create_datasource_from_table",
      "create_starter_workbook",
      "publish_datasource",
      "publish_workbook",
      "list_projects",
      "create_project",
      "set_permissions",
      "list_content",
      "refresh_datasource",
      "delete_content",
      // M2–M6 new tools
      "create_datasource_from_file",
      "design_dashboard",
      "build_from_plan",
      // E1 — brand kit
      "validate_brand",
      // E2 — VDS field metadata
      "get_datasource_fields",
    ]) {
      expect(names).toContain(t);
    }
  });
});

describe("create_datasource_from_query (flagship)", () => {
  it("builds via the sidecar, resolves the project, publishes, and returns the URL", async () => {
    const res = await invoke("create_datasource_from_query", {
      connection: { type: "csv", path: "/d.csv" },
      sql: "select 1",
      datasourceName: "Top Customers",
      projectName: "Sales",
    });
    expect(ctx.sidecar.buildDatasourceFromQuery).toHaveBeenCalledWith(
      expect.objectContaining({ sql: "select 1", name: "Top Customers" }),
    );
    expect(ctx.rest.resolveProjectId).toHaveBeenCalledWith("Sales");
    // overwrite defaults to false
    expect(ctx.rest.publishDatasource).toHaveBeenCalledWith(
      "/tmp/x.tdsx",
      "Top Customers",
      "PID",
      false,
    );
    expect(res.structuredContent).toEqual({
      datasourceLuid: "DS",
      url: "https://x/#/site/s/datasources/DS",
    });
  });

  it("passes overwrite=true through when requested", async () => {
    await invoke("create_datasource_from_query", {
      connection: { type: "csv", path: "/d.csv" },
      sql: "select 1",
      datasourceName: "DS",
      projectName: "Sales",
      overwrite: true,
    });
    expect(ctx.rest.publishDatasource).toHaveBeenCalledWith("/tmp/x.tdsx", "DS", "PID", true);
  });
});

describe("create_starter_workbook", () => {
  it("resolves the datasource contentUrl + site, builds the twbx, and publishes", async () => {
    const res = await invoke("create_starter_workbook", {
      datasourceLuid: "DS",
      datasourceName: "DS",
      workbookName: "Starter",
      projectName: "Sales",
      sheets: [{ title: "Rev", markType: "bar", cols: ["Region"], measures: ["Revenue"] }],
    });
    expect(ctx.rest.getDatasource).toHaveBeenCalledWith("DS");
    expect(ctx.sidecar.buildStarterWorkbook).toHaveBeenCalledWith(
      expect.objectContaining({ datasourceContentUrl: "DS_url", site: "s" }),
    );
    expect(ctx.rest.publishWorkbook).toHaveBeenCalledWith("/tmp/w.twbx", "Starter", "PID", false);
    expect(res.structuredContent).toMatchObject({ workbookLuid: "WB" });
  });
});

describe("guardrails", () => {
  it("delete_content refuses without confirm=true", async () => {
    await expect(
      invoke("delete_content", { contentType: "datasource", luid: "DS" }),
    ).rejects.toThrow(/confirm=true/);
    expect(ctx.rest.deleteContent).not.toHaveBeenCalled();
  });

  it("delete_content proceeds with confirm=true", async () => {
    await invoke("delete_content", { contentType: "workbook", luid: "WB", confirm: true });
    expect(ctx.rest.deleteContent).toHaveBeenCalledWith("workbook", "WB");
  });

  it("delete_content refuses content in the Default project", async () => {
    ctx.rest.listContent.mockResolvedValue([
      { id: "WB", name: "x", type: "workbook", projectName: "Default" },
    ]);
    await expect(
      invoke("delete_content", { contentType: "workbook", luid: "WB", confirm: true }),
    ).rejects.toThrow(/Default project/);
    expect(ctx.rest.deleteContent).not.toHaveBeenCalled();
  });

  it("set_permissions refuses elevated capabilities without confirmElevated", async () => {
    await expect(
      invoke("set_permissions", {
        contentType: "datasource",
        contentId: "DS",
        grants: [{ granteeId: "g", capability: "Delete", mode: "Allow" }],
      }),
    ).rejects.toThrow(/confirmElevated/);
    expect(ctx.rest.setPermissions).not.toHaveBeenCalled();
  });

  it("set_permissions applies elevated capabilities with confirmElevated=true", async () => {
    await invoke("set_permissions", {
      contentType: "datasource",
      contentId: "DS",
      grants: [{ granteeId: "g", capability: "Delete", mode: "Allow" }],
      confirmElevated: true,
    });
    expect(ctx.rest.setPermissions).toHaveBeenCalledTimes(1);
  });

  it("set_permissions rejects capabilities outside the allowlist", async () => {
    await expect(
      invoke("set_permissions", {
        contentType: "datasource",
        contentId: "DS",
        grants: [{ granteeId: "g", capability: "Bogus", mode: "Allow" }],
      }),
    ).rejects.toThrow(/Unknown capability/);
    expect(ctx.rest.setPermissions).not.toHaveBeenCalled();
  });

  it("set_permissions applies valid capabilities", async () => {
    await invoke("set_permissions", {
      contentType: "datasource",
      contentId: "DS",
      grants: [{ granteeId: "g", capability: "Read", mode: "Allow" }],
    });
    expect(ctx.rest.setPermissions).toHaveBeenCalledTimes(1);
  });
});

describe("create_datasource_from_table", () => {
  it("requires csvPath or records", async () => {
    await expect(
      invoke("create_datasource_from_table", { datasourceName: "DS", projectName: "Sales" }),
    ).rejects.toThrow(/csvPath or .*records/);
  });

  it("publishes from records", async () => {
    const res = await invoke("create_datasource_from_table", {
      records: [{ a: 1 }],
      datasourceName: "DS",
      projectName: "Sales",
    });
    expect(ctx.sidecar.buildDatasourceFromTable).toHaveBeenCalled();
    expect(res.structuredContent).toMatchObject({ datasourceLuid: "DS" });
  });
});

describe("create_datasource_from_file (M2)", () => {
  it("passes filePath and name to the sidecar and publishes", async () => {
    const res = await invoke("create_datasource_from_file", {
      name: "Sales CSV",
      filePath: "/data/sales.csv",
      projectName: "Sales",
    });
    expect(ctx.sidecar.buildDatasourceFromFile).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Sales CSV", filePath: "/data/sales.csv" }),
    );
    expect(ctx.rest.publishDatasource).toHaveBeenCalledWith(
      "/tmp/f.tdsx",
      "Sales CSV",
      "PID",
      false,
    );
    expect(res.structuredContent).toMatchObject({ datasourceLuid: "DS" });
  });

  it("forwards optional excelSheet and jsonPath to sidecar", async () => {
    await invoke("create_datasource_from_file", {
      name: "Excel DS",
      filePath: "/data/data.xlsx",
      fileType: "xlsx",
      excelSheet: "Sheet2",
      projectName: "Sales",
    });
    expect(ctx.sidecar.buildDatasourceFromFile).toHaveBeenCalledWith(
      expect.objectContaining({ excelSheet: "Sheet2", fileType: "xlsx" }),
    );
  });

  // PA-2: unsupported extension → error thrown BEFORE sidecar is called (zero sidecar calls)
  it("PA-2: unsupported extension throws before sidecar is called (zero sidecar calls)", async () => {
    await expect(
      invoke("create_datasource_from_file", {
        name: "Bad DS",
        filePath: "/data/file.xml",
        projectName: "Sales",
      }),
    ).rejects.toThrow(/Unsupported file extension/);
    expect(ctx.sidecar.buildDatasourceFromFile).not.toHaveBeenCalled();
  });

  // PA-2: extension with no dot in path throws
  it("PA-2: filePath with no extension throws before sidecar call", async () => {
    await expect(
      invoke("create_datasource_from_file", {
        name: "No Ext DS",
        filePath: "/data/noextension",
        projectName: "Sales",
      }),
    ).rejects.toThrow(/Unsupported file extension/);
    expect(ctx.sidecar.buildDatasourceFromFile).not.toHaveBeenCalled();
  });
});

describe("design_dashboard (M5 / Slice 5)", () => {
  it("autonomous mode returns a proposal with kind='proposal'", async () => {
    const res = await invoke("design_dashboard", {
      mode: "autonomous",
      audience: "analyst",
      businessQuestion: "How is revenue trending?",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const result = (res.structuredContent as { result: { kind: string; plan: { kind: string; sheets: unknown[] } } }).result;
    expect(result.kind).toBe("proposal");
    expect(result.plan.kind).toBe("plan");
    expect(result.plan.sheets.length).toBeGreaterThan(0);
  });

  it("interview mode returns kind='questions' with 3–10 questions", async () => {
    const res = await invoke("design_dashboard", { mode: "interview" });
    const result = (res.structuredContent as { result: { kind: string; questions: unknown[] } }).result;
    expect(result.kind).toBe("questions");
    expect(result.questions.length).toBeGreaterThanOrEqual(3);
  });

  it("throws when autonomous mode is missing datasourceLuid", async () => {
    await expect(
      invoke("design_dashboard", {
        mode: "autonomous",
        audience: "exec",
        businessQuestion: "Revenue?",
        datasourceName: "Sales",
        projectName: "Sales",
      }),
    ).rejects.toThrow(/datasourceLuid/);
  });

  describe("persona resolution (E1)", () => {
    it("persona='ceo' resolves to the exec base audience and the proposal mentions it", async () => {
      const res = await invoke("design_dashboard", {
        mode: "autonomous",
        persona: "ceo",
        businessQuestion: "How is revenue trending?",
        fieldHints: [
          { name: "order_date", dataType: "date" },
          { name: "revenue", dataType: "number" },
        ],
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Sales",
      });
      const result = (
        res.structuredContent as {
          result: { kind: string; summary: string; audience: string; plan: { audience: string; personaName?: string } };
        }
      ).result;
      expect(result.kind).toBe("proposal");
      expect(result.audience).toBe("exec");
      expect(result.plan.audience).toBe("exec");
      expect(result.plan.personaName).toBe("ceo");
      expect(result.summary).toMatch(/ceo/i);
    });

    it("an explicit `audience` input takes precedence over the persona's base", async () => {
      const res = await invoke("design_dashboard", {
        mode: "autonomous",
        audience: "exec",
        persona: "analyst", // base=analyst, but explicit audience should win
        businessQuestion: "Revenue by region",
        fieldHints: [
          { name: "region", dataType: "string" },
          { name: "revenue", dataType: "number" },
        ],
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Sales",
      });
      const result = (res.structuredContent as { result: { plan: { audience: string } } }).result;
      expect(result.plan.audience).toBe("exec");
    });

    it("throws a clear error for an unknown persona, listing available names", async () => {
      await expect(
        invoke("design_dashboard", {
          mode: "autonomous",
          persona: "nonexistent_persona_xyz",
          businessQuestion: "Revenue?",
          datasourceLuid: "DS",
          datasourceName: "Sales",
          projectName: "Sales",
        }),
      ).rejects.toThrow(/Unknown persona "nonexistent_persona_xyz".*ceo/is);
    });

    it("threads a persona's maxSheets override through the audience clamp", async () => {
      tmpDir = mkdtempSync(join(tmpdir(), "brand-tool-test-"));
      const brandPath = join(tmpDir, "brand.yaml");
      writeFileSync(
        brandPath,
        `
brand:
  name: "Vip Corp"
personas:
  vip:
    base: analyst
    maxSheets: 1
`,
        "utf8",
      );

      const res = await invoke("design_dashboard", {
        mode: "directed",
        persona: "vip",
        brandPath,
        directions: "revenue by region and profit by category and orders over time",
        fieldHints: [
          { name: "region", dataType: "string" },
          { name: "category", dataType: "string" },
          { name: "order_date", dataType: "date" },
          { name: "revenue", dataType: "number" },
          { name: "profit", dataType: "number" },
          { name: "orders", dataType: "number" },
        ],
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Sales",
      });
      const result = (
        res.structuredContent as {
          result: { plan: { audience: string; sheets: unknown[]; personaName?: string; brandName?: string } };
        }
      ).result;
      expect(result.plan.audience).toBe("analyst");
      expect(result.plan.sheets.length).toBe(1);
      expect(result.plan.personaName).toBe("vip");
      expect(result.plan.brandName).toBe("Vip Corp");
    });
  });
});

describe("validate_brand (E1)", () => {
  it("reports valid=true for the repo-root brand.yaml, listing the scaffolded personas", async () => {
    const res = await invoke("validate_brand", {});
    const result = res.structuredContent as {
      valid: boolean;
      warnings: string[];
      personas: string[];
      summary: string;
    };
    expect(result.valid).toBe(true);
    expect(result.warnings).toEqual([]);
    expect(result.personas).toEqual(
      expect.arrayContaining(["ceo", "cto", "slt_manager", "analyst", "client"]),
    );
    expect(result.summary).toMatch(/valid/i);
  });

  it("reports valid=false (never throws) for a malformed brand file", async () => {
    tmpDir = mkdtempSync(join(tmpdir(), "brand-validate-test-"));
    const brandPath = join(tmpDir, "brand.yaml");
    writeFileSync(
      brandPath,
      `
palette:
  semantic:
    good: "not-a-hex-color"
`,
      "utf8",
    );

    const res = await invoke("validate_brand", { path: brandPath });
    const result = res.structuredContent as {
      valid: boolean;
      warnings: string[];
      personas: string[];
      summary: string;
    };
    expect(result.valid).toBe(false);
    expect(result.warnings.length).toBeGreaterThan(0);
    expect(result.personas).toEqual([]);
    expect(result.summary).toMatch(/invalid/i);
  });

  it("reports valid=true with a warning when the file is absent", async () => {
    const missingPath = join(mkdtempSync(join(tmpdir(), "brand-missing-")), "does-not-exist.yaml");
    const res = await invoke("validate_brand", { path: missingPath });
    const result = res.structuredContent as { valid: boolean; warnings: string[] };
    expect(result.valid).toBe(true);
    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toMatch(/not found/i);
  });
});

describe("build_from_plan (M6)", () => {
  // basePlan always includes datasourceSpec.filePath: build_from_plan requires an
  // embeddable extract (hyperPath) which is only produced by the filePath branch.
  const basePlan = {
    schemaVersion: 1,
    kind: "plan",
    workbookName: "Test WB",
    datasourceLuid: "DS",
    datasourceName: "Sales",
    projectName: "Sales",
    audience: "analyst",
    rationale: "test",
    dashboardLayout: "tiled_vertical",
    sheets: [
      { title: "Rev by Region", markType: "bar", cols: ["region"], rows: [], measures: ["revenue"] },
    ],
    datasourceSpec: {
      datasourceName: "Sales DS",
      filePath: "/data/sales.csv",
      fileType: "csv",
    },
  };

  // basePlanLuidOnly has no datasourceSpec — used to test the loud-fail path.
  const basePlanLuidOnly = {
    schemaVersion: 1,
    kind: "plan",
    workbookName: "Test WB",
    datasourceLuid: "DS",
    datasourceName: "Sales",
    projectName: "Sales",
    audience: "analyst",
    rationale: "test",
    dashboardLayout: "tiled_vertical",
    sheets: [
      { title: "Rev by Region", markType: "bar", cols: ["region"], rows: [], measures: ["revenue"] },
    ],
  };

  it("builds dashboard workbook and publishes", async () => {
    const res = await invoke("build_from_plan", { plan: basePlan });
    expect(ctx.sidecar.buildDatasourceFromFile).toHaveBeenCalled();
    expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalled();
    expect(ctx.rest.publishWorkbook).toHaveBeenCalledWith("/tmp/d.twbx", "Test WB", "PID", false);
    expect(res.structuredContent).toMatchObject({ workbookLuid: "WB" });
  });

  // E2E-1: datasourceSpec.filePath path — 1× buildDatasourceFromFile, 1× /workbook/dashboard,
  //         1× publishWorkbook, 1× publishDatasource
  it("E2E-1: calls sidecar dashboard once, publishWorkbook once, publishDatasource once (filePath spec)", async () => {
    await invoke("build_from_plan", { plan: basePlan });
    expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledTimes(1);
    expect(ctx.rest.publishWorkbook).toHaveBeenCalledTimes(1);
    expect(ctx.rest.publishDatasource).toHaveBeenCalledTimes(1);
  });

  // E2E-1: canvasWidth/canvasHeight are derived from audience and forwarded to sidecar
  it("E2E-1: passes audience-derived canvasWidth/canvasHeight to sidecar (analyst → 1200×900)", async () => {
    await invoke("build_from_plan", { plan: basePlan }); // audience: analyst
    expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
      expect.objectContaining({ canvasWidth: 1200, canvasHeight: 900 }),
    );
  });

  // E2E-2: datasourceSpec.filePath → hyperPath threaded through to buildDashboardWorkbook
  it("E2E-2: with datasourceSpec.filePath, threads hyperPath from buildDatasourceFromFile into buildDashboardWorkbook", async () => {
    const planWithSpec = {
      ...basePlanLuidOnly,
      datasourceSpec: {
        datasourceName: "New Sales DS",
        filePath: "/data/sales.csv",
        fileType: "csv",
      },
    };
    const res = await invoke("build_from_plan", { plan: planWithSpec });
    // sidecar.buildDatasourceFromFile must be called before buildDashboardWorkbook
    expect(ctx.sidecar.buildDatasourceFromFile).toHaveBeenCalledWith(
      expect.objectContaining({ name: "New Sales DS", filePath: "/data/sales.csv" }),
    );
    // publishDatasource must be called once (for the new datasource)
    expect(ctx.rest.publishDatasource).toHaveBeenCalledTimes(1);
    // workbook build must still happen
    expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledTimes(1);
    // buildDashboardWorkbook must receive the hyperPath from buildDatasourceFromFile
    expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
      expect.objectContaining({ hyperPath: "/tmp/f.hyper" }),
    );
    // result must include datasourceLuid
    expect(res.structuredContent).toMatchObject({ workbookLuid: "WB", datasourceLuid: "DS" });
  });

  // no-embeddable-extract branch: LUID-only (no datasourceSpec) → loud actionable error
  it("LUID-only (no datasourceSpec): throws actionable error about missing embeddable extract", async () => {
    await expect(
      invoke("build_from_plan", { plan: basePlanLuidOnly }),
    ).rejects.toThrow(/datasourceSpec.*filePath|filePath.*datasourceSpec|embed/i);
    expect(ctx.sidecar.buildDashboardWorkbook).not.toHaveBeenCalled();
    expect(ctx.rest.publishWorkbook).not.toHaveBeenCalled();
  });

  // no-embeddable-extract branch: datasourceSpec.sql (no filePath) → loud actionable error
  it("datasourceSpec.sql (no filePath): throws actionable error about missing embeddable extract", async () => {
    const planWithSqlSpec = {
      ...basePlanLuidOnly,
      datasourceSpec: {
        datasourceName: "SQL DS",
        sql: "SELECT region, SUM(revenue) FROM sales GROUP BY region",
        connection: { type: "postgres", host: "db.example.com" },
      },
    };
    await expect(
      invoke("build_from_plan", { plan: planWithSqlSpec }),
    ).rejects.toThrow(/sql.*filePath|filePath.*sql|embed/i);
    expect(ctx.sidecar.buildDashboardWorkbook).not.toHaveBeenCalled();
    expect(ctx.rest.publishWorkbook).not.toHaveBeenCalled();
  });

  it("throws when plan has invalid schemaVersion", async () => {
    await expect(
      invoke("build_from_plan", {
        plan: {
          schemaVersion: 99,
          kind: "plan",
          workbookName: "X",
          datasourceLuid: "DS",
          datasourceName: "S",
          projectName: "P",
          audience: "exec",
          rationale: "r",
          dashboardLayout: "tiled_vertical",
          sheets: [],
        },
      }),
    ).rejects.toThrow();
  });

  // R-7: placeholder token rejection
  it("R-7: throws when a sheet still contains a <measure> placeholder token", async () => {
    await expect(
      invoke("build_from_plan", {
        plan: {
          ...basePlan,
          sheets: [
            { title: "Overview", markType: "bar", cols: ["region"], rows: [], measures: ["<measure>"] },
          ],
        },
      }),
    ).rejects.toThrow(/placeholder token/);
  });

  it("R-7: throws when a sheet contains a <dimension> placeholder token", async () => {
    await expect(
      invoke("build_from_plan", {
        plan: {
          ...basePlan,
          sheets: [
            { title: "Overview", markType: "bar", cols: ["<dimension>"], rows: [], measures: ["revenue"] },
          ],
        },
      }),
    ).rejects.toThrow(/placeholder token/);
  });

  it("R-7: concrete plan (no placeholders) passes through", async () => {
    // Should NOT throw — the basePlan has no placeholder tokens
    const res = await invoke("build_from_plan", { plan: basePlan });
    expect(res.structuredContent).toMatchObject({ workbookLuid: "WB" });
  });

  // R-5: audience invariant re-validation at build time
  it("R-5: throws when a sheet exceeds maxMeasures for audience (exec allows 1)", async () => {
    await expect(
      invoke("build_from_plan", {
        plan: {
          ...basePlan,
          audience: "exec",
          sheets: [
            {
              title: "Over Cap",
              markType: "bar",
              cols: ["region"],
              rows: [],
              measures: ["revenue", "cost"],  // exec cap = 1
            },
          ],
        },
      }),
    ).rejects.toThrow(/measures/);
  });

  it("R-5: throws when a sheet markType is disallowed for audience", async () => {
    // exec allows bar/line/text; map is not allowed
    await expect(
      invoke("build_from_plan", {
        plan: {
          ...basePlan,
          audience: "exec",
          sheets: [
            { title: "Map Sheet", markType: "map", cols: [], rows: ["country"], measures: ["revenue"] },
          ],
        },
      }),
    ).rejects.toThrow(/markType/);
  });

  it("R-5: throws when a sheet exceeds maxDimensions for audience (exec allows 1)", async () => {
    await expect(
      invoke("build_from_plan", {
        plan: {
          ...basePlan,
          audience: "exec",
          sheets: [
            {
              title: "Over Dims",
              markType: "bar",
              cols: ["region", "segment"],  // exec cap = 1 dimension total
              rows: [],
              measures: ["revenue"],
            },
          ],
        },
      }),
    ).rejects.toThrow(/dimensions/);
  });

  // Phase E1, Slice B: "Builder applies branding" — build_from_plan resolves
  // brand.yaml and threads a brand block into buildDashboardWorkbook.
  describe("rich-field threading (tool-vs-demo divergence guard)", () => {
    it("build_from_plan threads kind/color/kpi/geo + dashboard title/layoutGrammar to the sidecar", async () => {
      const richPlan = {
        ...basePlan,
        audience: "exec",
        dashboardTitle: "Executive Overview",
        dashboardSubtitle: "Period over Period",
        layoutGrammar: {
          kind: "kpi_band_over_charts",
          kpiTileTitles: ["Sales"],
          chartTitles: ["Rev by Region", "Sales by State"],
        },
        sheets: [
          {
            title: "Sales",
            markType: "text",
            kind: "kpi_tile",
            cols: [],
            rows: [],
            measures: ["Sales"],
            kpi: { primaryMeasure: "Sales", comparisonMeasure: "PP Sales", deltaMeasure: "Sales Difference" },
          },
          {
            title: "Rev by Region",
            markType: "bar",
            cols: ["region"],
            rows: [],
            measures: ["revenue"],
            color: { field: "Category", kind: "dimension" },
          },
          {
            title: "Sales by State",
            markType: "map_filled",
            cols: [],
            rows: ["State"],
            measures: ["Sales"],
            geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
          },
        ],
      };
      await invoke("build_from_plan", { plan: richPlan });
      expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
        expect.objectContaining({
          dashboardTitle: "Executive Overview",
          dashboardSubtitle: "Period over Period",
          layoutGrammar: expect.objectContaining({ kind: "kpi_band_over_charts" }),
          sheets: [
            expect.objectContaining({
              kind: "kpi_tile",
              kpi: expect.objectContaining({ primaryMeasure: "Sales", deltaMeasure: "Sales Difference" }),
            }),
            expect.objectContaining({
              color: expect.objectContaining({ field: "Category", kind: "dimension" }),
            }),
            expect.objectContaining({
              geo: expect.objectContaining({ geoField: "State", geoRole: "state" }),
            }),
          ],
        }),
      );
    });
  });

  describe("branding (E1, Slice B)", () => {
    it("plan.personaName set (no explicit brandPath) → brand threaded from the repo-root brand.yaml", async () => {
      await invoke("build_from_plan", {
        plan: { ...basePlan, personaName: "ceo", brandName: "My Company" },
      });
      expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
        expect.objectContaining({
          brand: expect.objectContaining({
            brandName: "My Company",
            palette: expect.objectContaining({
              categorical: expect.arrayContaining(["#4e79a7"]),
              good: "#59a14f",
              bad: "#e15759",
            }),
            typography: expect.objectContaining({
              title: expect.objectContaining({ font: "Tableau Bold" }),
            }),
            formats: expect.objectContaining({ currency: "$#,##0" }),
          }),
        }),
      );
    });

    it("no personaName/brandName/brandPath → brand is omitted entirely", async () => {
      await invoke("build_from_plan", { plan: basePlan });
      const call = ctx.sidecar.buildDashboardWorkbook.mock.calls[0]?.[0] as
        | Record<string, unknown>
        | undefined;
      expect(call).toBeDefined();
      expect(call?.["brand"]).toBeUndefined();
    });

    it("explicit brandPath input alone (no personaName/brandName) still resolves and threads a brand", async () => {
      tmpDir = mkdtempSync(join(tmpdir(), "brand-build-test-"));
      const brandPath = join(tmpDir, "brand.yaml");
      writeFileSync(
        brandPath,
        `
brand:
  name: "Custom Corp"
palette:
  categorical: ["#111111", "#222222"]
`,
        "utf8",
      );

      await invoke("build_from_plan", { plan: basePlan, brandPath });
      expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
        expect.objectContaining({
          brand: expect.objectContaining({
            brandName: "Custom Corp",
            palette: expect.objectContaining({ categorical: ["#111111", "#222222"] }),
          }),
        }),
      );
    });

    it("a missing brandPath file falls back to built-in defaults (loadBrand never throws for ENOENT)", async () => {
      // Mirrors loadBrand()'s documented behavior: file-not-found is a warning,
      // not an error — the build still proceeds, branded with DEFAULT_BRAND.
      await invoke("build_from_plan", {
        plan: basePlan,
        brandPath: "/definitely/does/not/exist/brand.yaml",
      });
      expect(ctx.sidecar.buildDashboardWorkbook).toHaveBeenCalledWith(
        expect.objectContaining({
          brand: expect.objectContaining({ brandName: "My Company" }),
        }),
      );
    });
  });
});

describe("get_datasource_fields (E2 Foundation)", () => {
  it("maps VDS fields to { name, caption, dataType, defaultAggregation } and returns count", async () => {
    const res = await invoke("get_datasource_fields", { datasourceLuid: "DS1" });
    expect(ctx.rest.getDatasourceFields).toHaveBeenCalledWith("DS1");
    expect(res.structuredContent).toEqual({
      fields: [
        { name: "Sales", caption: "Sales", dataType: "REAL", defaultAggregation: "SUM" },
        { name: "Order Date", caption: "Order Date", dataType: "DATE" },
      ],
      count: 2,
    });
  });

  it("returns an empty fields array with count 0 when the datasource has no fields", async () => {
    ctx.rest.getDatasourceFields.mockResolvedValueOnce([]);
    const res = await invoke("get_datasource_fields", { datasourceLuid: "DS-empty" });
    expect(res.structuredContent).toEqual({ fields: [], count: 0 });
  });

  it("propagates a not-found error from the REST client", async () => {
    ctx.rest.getDatasourceFields.mockRejectedValueOnce(
      new Error("Tableau API request failed (404) on POST /api/v1/vizql-data-service/read-metadata: Datasource not found or VDS unavailable"),
    );
    await expect(invoke("get_datasource_fields", { datasourceLuid: "missing" })).rejects.toThrow(
      /not found/,
    );
  });
});
