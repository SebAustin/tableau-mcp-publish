import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerPublishDatasource(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "publish_datasource",
    {
      title: "Publish an existing datasource file",
      description:
        "Publish an existing local .tdsx or .hyper file to a Tableau Cloud project. Uses chunked upload automatically for files over 64 MB. Returns { datasourceLuid, url }.",
      inputSchema: {
        filePath: z.string().min(1).describe("Absolute path to a local .tdsx/.hyper file."),
        name: z.string().min(1).describe("Name for the published datasource."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        overwrite: z.boolean().default(false),
      },
      outputSchema: { datasourceLuid: z.string(), url: z.string() },
    },
    async ({ filePath, name, projectName, overwrite }) => {
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, url } = await ctx.rest.publishDatasource(filePath, name, projectId, overwrite);
      return toolResult(`Published datasource "${name}" → ${url}`, { datasourceLuid: id, url });
    },
  );
}
