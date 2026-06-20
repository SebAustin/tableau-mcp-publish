import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerPublishWorkbook(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "publish_workbook",
    {
      title: "Publish an existing workbook file",
      description:
        "Publish an existing local .twb or .twbx file to a Tableau Cloud project. Uses chunked upload automatically for files over 64 MB. Returns { workbookLuid, url }.",
      inputSchema: {
        filePath: z.string().min(1).describe("Absolute path to a local .twb/.twbx file."),
        name: z.string().min(1).describe("Name for the published workbook."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        overwrite: z.boolean().default(false),
      },
      outputSchema: { workbookLuid: z.string(), url: z.string() },
    },
    async ({ filePath, name, projectName, overwrite }) => {
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, url } = await ctx.rest.publishWorkbook(filePath, name, projectId, overwrite);
      return toolResult(`Published workbook "${name}" → ${url}`, { workbookLuid: id, url });
    },
  );
}
