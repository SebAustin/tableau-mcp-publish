import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

const sheetSchema = z.object({
  title: z.string().min(1).describe("Worksheet title."),
  markType: z
    .enum(["bar", "line", "text", "map"])
    .default("bar")
    .describe("Mark type. bar/line/text are fully supported; map is experimental."),
  rows: z.array(z.string()).default([]).describe("Field names on the Rows shelf."),
  cols: z.array(z.string()).default([]).describe("Field names on the Columns shelf."),
  measures: z.array(z.string()).default([]).describe("Measure field names (aggregated as SUM)."),
});

export function registerCreateStarterWorkbook(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_starter_workbook",
    {
      title: "Create a starter workbook on a published datasource",
      description:
        "Generate a .twbx workbook bound to an existing published datasource (by name), with one worksheet per sheet spec, then publish it to a Tableau Cloud project. Supported mark types: bar, line, text (map is experimental). Returns { workbookLuid, url }.",
      inputSchema: {
        datasourceLuid: z.string().min(1).describe("LUID of the published datasource to bind to."),
        datasourceName: z
          .string()
          .min(1)
          .describe("Name of the published datasource (used in the workbook's connection)."),
        workbookName: z.string().min(1).describe("Name for the published workbook."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        sheets: z.array(sheetSchema).min(1).describe("One or more worksheet specifications."),
        overwrite: z.boolean().default(false),
      },
      outputSchema: { workbookLuid: z.string(), url: z.string() },
    },
    async ({ datasourceLuid, datasourceName, workbookName, projectName, sheets, overwrite }) => {
      // Resolve the server-assigned contentUrl so the workbook binds to the published datasource.
      const { contentUrl } = await ctx.rest.getDatasource(datasourceLuid);
      const { twbxPath } = await ctx.sidecar.buildStarterWorkbook({
        datasourceName,
        datasourceContentUrl: contentUrl,
        site: ctx.config.siteName,
        sheets,
      });
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, url } = await ctx.rest.publishWorkbook(
        twbxPath,
        workbookName,
        projectId,
        overwrite,
      );
      return toolResult(`Published workbook "${workbookName}" → ${url}`, {
        workbookLuid: id,
        url,
      });
    },
  );
}
