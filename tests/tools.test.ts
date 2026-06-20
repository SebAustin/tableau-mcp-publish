import { describe, it, expect, beforeEach, vi } from "vitest";
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
  };
  const sidecar = {
    buildDatasourceFromQuery: vi.fn().mockResolvedValue({ tdsxPath: "/tmp/x.tdsx" }),
    buildDatasourceFromTable: vi.fn().mockResolvedValue({ tdsxPath: "/tmp/t.tdsx" }),
    buildStarterWorkbook: vi.fn().mockResolvedValue({ twbxPath: "/tmp/w.twbx" }),
  };
  return { config: cfg, rest, sidecar };
}

let server: FakeServer;
let ctx: ReturnType<typeof makeCtx>;

beforeEach(() => {
  server = new FakeServer();
  ctx = makeCtx();
  registerAllTools(server as never, ctx as never);
});

async function invoke(name: string, rawArgs: Record<string, unknown>) {
  const tool = server.tools.get(name);
  if (!tool) throw new Error(`tool ${name} not registered`);
  // Parse through the declared input schema so zod defaults (e.g. overwrite) apply.
  const parsed = z.object(tool.config.inputSchema ?? {}).parse(rawArgs);
  return tool.handler(parsed as Record<string, unknown>);
}

describe("tool registration", () => {
  it("registers all 11 tools, each with a description and declared schemas", () => {
    expect(server.tools.size).toBe(11);
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
