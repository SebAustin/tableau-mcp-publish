import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

const contentTypeSchema = z.enum(["datasource", "workbook"]);

export function registerContentTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "list_content",
    {
      title: "List content",
      description:
        "List published datasources and workbooks on the site (id, name, type, project). Returns { items: [...] }.",
      inputSchema: {},
      outputSchema: {
        items: z.array(
          z.object({
            id: z.string(),
            name: z.string(),
            type: contentTypeSchema,
            projectName: z.string().optional(),
            updatedAt: z.string().optional(),
          }),
        ),
      },
    },
    async () => {
      const items = await ctx.rest.listContent();
      return toolResult(`Found ${items.length} content item(s).`, { items });
    },
  );

  server.registerTool(
    "refresh_datasource",
    {
      title: "Refresh datasource extract",
      description:
        "Trigger an extract refresh for a published datasource by its LUID. Returns { ok: true, datasourceId }.",
      inputSchema: {
        datasourceId: z.string().min(1).describe("LUID of the published datasource to refresh."),
      },
      outputSchema: { ok: z.boolean(), datasourceId: z.string() },
    },
    async ({ datasourceId }) => {
      await ctx.rest.refreshDatasource(datasourceId);
      return toolResult(`Queued refresh for datasource ${datasourceId}.`, {
        ok: true,
        datasourceId,
      });
    },
  );

  server.registerTool(
    "delete_content",
    {
      title: "Delete content",
      description:
        "Delete a published datasource or workbook by LUID. DESTRUCTIVE and irreversible — requires confirm=true. Returns { deleted, contentType, luid }.",
      inputSchema: {
        contentType: contentTypeSchema.describe("Whether the LUID is a datasource or a workbook."),
        luid: z.string().min(1).describe("LUID of the content to delete."),
        confirm: z
          .boolean()
          .default(false)
          .describe("Must be explicitly true; deletion is refused otherwise."),
      },
      outputSchema: { deleted: z.boolean(), contentType: contentTypeSchema, luid: z.string() },
    },
    async ({ contentType, luid, confirm }) => {
      if (!confirm) {
        throw new Error(
          `Refusing to delete ${contentType} ${luid}: pass confirm=true to authorize this irreversible action.`,
        );
      }
      // Never delete content in the Default project (governance guardrail).
      const items = await ctx.rest.listContent();
      const target = items.find((i) => i.type === contentType && i.id === luid);
      if (target?.projectName?.toLowerCase() === "default") {
        throw new Error(
          `Refusing to delete ${contentType} ${luid}: it lives in the Default project.`,
        );
      }
      await ctx.rest.deleteContent(contentType, luid);
      return toolResult(`Deleted ${contentType} ${luid}.`, { deleted: true, contentType, luid });
    },
  );
}
