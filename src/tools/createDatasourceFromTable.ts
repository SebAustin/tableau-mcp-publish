import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerCreateDatasourceFromTable(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_datasource_from_table",
    {
      title: "Create published datasource from a table (CSV or records)",
      description:
        "Materialize a CSV file or an array of JSON records as a Hyper extract, package it as a .tdsx, and publish it to a Tableau Cloud project. Provide either csvPath or records. Returns { datasourceLuid, url }.",
      inputSchema: {
        csvPath: z.string().optional().describe("Absolute path to a local CSV file."),
        records: z
          .array(z.record(z.unknown()))
          .optional()
          .describe("Array of row objects (alternative to csvPath)."),
        datasourceName: z.string().min(1).describe("Name for the published datasource."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        overwrite: z.boolean().default(false),
      },
      outputSchema: { datasourceLuid: z.string(), url: z.string() },
    },
    async ({ csvPath, records, datasourceName, projectName, overwrite }) => {
      if (!csvPath && !(records && records.length > 0)) {
        throw new Error("Provide either csvPath or a non-empty records array.");
      }
      const { tdsxPath } = await ctx.sidecar.buildDatasourceFromTable({
        name: datasourceName,
        csvPath,
        records,
      });
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, url } = await ctx.rest.publishDatasource(
        tdsxPath,
        datasourceName,
        projectId,
        overwrite,
      );
      return toolResult(`Published datasource "${datasourceName}" → ${url}`, {
        datasourceLuid: id,
        url,
      });
    },
  );
}
