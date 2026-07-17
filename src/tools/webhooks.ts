/**
 * Webhook tools — Phase E2 slice B.
 *
 * See `src/rest/webhooks.ts` for the full API + payload documentation.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { CreateWebhookInputSchema, validateCreateWebhookInput } from "../rest/webhooks.js";

export function registerWebhookTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_webhook",
    {
      title: "Create a webhook",
      description:
        "Create a site webhook that POSTs to an HTTPS destination when a Tableau event fires " +
        "(DatasourceRefreshStarted|Succeeded|Failed, DatasourceCreated|Updated|Deleted, and the " +
        "Workbook* equivalents). Requires site-administrator privileges on the signed-in PAT — a " +
        "403 from Tableau is surfaced with that explanation rather than a bare 'Forbidden'. The " +
        "destination receives a JSON payload shaped like " +
        "{ resource, event_type, resource_name, site_luid, resource_luid, created_at }. " +
        "Returns { webhookId, name, event }.",
      inputSchema: CreateWebhookInputSchema.shape,
      outputSchema: { webhookId: z.string(), name: z.string(), event: z.string() },
    },
    async (input) => {
      const validated = validateCreateWebhookInput(input);
      const webhook = await ctx.rest.createWebhook(validated);
      return toolResult(
        `Created webhook "${webhook.name}" (${webhook.webhookId}) for event ${webhook.event}.`,
        { webhookId: webhook.webhookId, name: webhook.name, event: webhook.event },
      );
    },
  );

  server.registerTool(
    "list_webhooks",
    {
      title: "List webhooks",
      description: "List every webhook configured on the site. Returns { webhooks: [...] }.",
      inputSchema: {},
      outputSchema: {
        webhooks: z.array(
          z.object({
            webhookId: z.string(),
            name: z.string(),
            event: z.string(),
            url: z.string().optional(),
          }),
        ),
      },
    },
    async () => {
      const webhooks = await ctx.rest.listWebhooks();
      return toolResult(`Found ${webhooks.length} webhook(s).`, { webhooks });
    },
  );

  server.registerTool(
    "delete_webhook",
    {
      title: "Delete a webhook",
      description:
        "Delete a webhook by its webhookId. DESTRUCTIVE and irreversible — requires confirm=true. " +
        "Returns { deleted, webhookId }.",
      inputSchema: {
        webhookId: z.string().min(1).describe("webhookId from create_webhook or list_webhooks."),
        confirm: z
          .boolean()
          .default(false)
          .describe("Must be explicitly true; deletion is refused otherwise."),
      },
      outputSchema: { deleted: z.boolean(), webhookId: z.string() },
    },
    async ({ webhookId, confirm }) => {
      if (!confirm) {
        throw new Error(
          `Refusing to delete webhook ${webhookId}: pass confirm=true to authorize this irreversible action.`,
        );
      }
      await ctx.rest.deleteWebhook(webhookId);
      return toolResult(`Deleted webhook ${webhookId}.`, { deleted: true, webhookId });
    },
  );
}
