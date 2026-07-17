#!/usr/bin/env node
import { pathToFileURL } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { TableauRestClient } from "./restClient.js";
import { AuthoringSidecar } from "./sidecar.js";
import type { ToolContext } from "./tools/context.js";
import { registerProjectTools } from "./tools/projects.js";
import { registerContentTools } from "./tools/content.js";
import { registerPermissionTools } from "./tools/permissions.js";
import { registerCreateDatasourceFromQuery } from "./tools/createDatasourceFromQuery.js";
import { registerCreateDatasourceFromTable } from "./tools/createDatasourceFromTable.js";
import { registerCreateStarterWorkbook } from "./tools/createStarterWorkbook.js";
import { registerPublishDatasource } from "./tools/publishDatasource.js";
import { registerPublishWorkbook } from "./tools/publishWorkbook.js";
import { registerCreateDatasourceFromFile } from "./tools/createDatasourceFromFile.js";
import { registerDesignDashboard } from "./tools/designDashboard.js";
import { registerBuildFromPlan } from "./tools/buildFromPlan.js";
import { registerValidateBrand } from "./tools/validateBrand.js";
import { registerGetDatasourceFields } from "./tools/getDatasourceFields.js";
import { registerScheduleTools } from "./tools/schedules.js";
import { registerWebhookTools } from "./tools/webhooks.js";
import { registerCreateLiveDatasource } from "./tools/createLiveDatasource.js";

/** stderr only — stdout is reserved for the MCP stdio transport. */
function log(message: string): void {
  process.stderr.write(`[tableau-mcp-publish] ${message}\n`);
}

/** Register all 23 tools onto the server. Exported for tests. */
export function registerAllTools(server: McpServer, ctx: ToolContext): void {
  registerProjectTools(server, ctx);
  registerContentTools(server, ctx);
  registerPermissionTools(server, ctx);
  registerCreateDatasourceFromQuery(server, ctx);
  registerCreateDatasourceFromTable(server, ctx);
  registerCreateStarterWorkbook(server, ctx);
  registerPublishDatasource(server, ctx);
  registerPublishWorkbook(server, ctx);
  // M2 — file ingest
  registerCreateDatasourceFromFile(server, ctx);
  // M5 — planner
  registerDesignDashboard(server, ctx);
  // M6 — build + publish from plan
  registerBuildFromPlan(server, ctx);
  // E1 — brand kit validation
  registerValidateBrand(server, ctx);
  // E2 — VDS field metadata (Foundation slice)
  registerGetDatasourceFields(server, ctx);
  // E2 slice B — Cloud extract-refresh scheduling + webhooks
  registerScheduleTools(server, ctx);
  registerWebhookTools(server, ctx);
  // E2 slice C — live Cloud connections (Snowflake/Presto) + embedded credentials
  registerCreateLiveDatasource(server, ctx);
}

async function main(): Promise<void> {
  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);
  const server = new McpServer({ name: "tableau-mcp-publish", version: "0.1.0" });

  registerAllTools(server, { config, rest, sidecar });

  // Sign in to Tableau and start the authoring sidecar before serving requests.
  log(`Signing in to ${config.server} (site "${config.siteName}")…`);
  await rest.signIn();
  log("Starting Python authoring sidecar…");
  await sidecar.start();

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    log(`Received ${signal}, shutting down…`);
    try {
      await sidecar.stop();
    } finally {
      await rest.signOut().catch(() => undefined);
      process.exit(0);
    }
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  const transport = new StdioServerTransport();
  await server.connect(transport);
  log("Ready. Tableau publishing tools are available over stdio.");
}

// Only auto-start when run as the executable, not when imported (e.g. by tests).
const isEntrypoint =
  !!process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isEntrypoint) {
  main().catch((err) => {
    process.stderr.write(`Fatal: ${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(1);
  });
}
