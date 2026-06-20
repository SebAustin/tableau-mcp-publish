import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

/**
 * Allowlist of Tableau permission capability names. set_permissions rejects any
 * capability outside this set so a typo can't silently widen or fail to apply access.
 */
export const ALLOWED_CAPABILITIES: ReadonlySet<string> = new Set([
  "Read",
  "Write",
  "Connect",
  "Delete",
  "ChangePermissions",
  "ChangeHierarchy",
  "ExportData",
  "ExportImage",
  "ExportXml",
  "Filter",
  "ProjectLeader",
  "ViewComments",
  "AddComment",
  "ViewUnderlyingData",
  "ShareView",
  "WebAuthoring",
  "SaveAs",
  "CreateRefreshMetrics",
  "RunExplainData",
]);

/**
 * High-impact capabilities. Granting these to the wrong grantee can widen access
 * or enable deletion, so a prompt-injected agent must pass confirmElevated=true.
 */
export const ELEVATED_CAPABILITIES: ReadonlySet<string> = new Set([
  "ChangePermissions",
  "Delete",
  "ProjectLeader",
  "ChangeHierarchy",
]);

export function registerPermissionTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "set_permissions",
    {
      title: "Set permissions",
      description:
        "Grant or deny capabilities on a published datasource or workbook for users or groups. Capabilities are validated against an allowlist; elevated ones (ChangePermissions, Delete, ProjectLeader, ChangeHierarchy) require confirmElevated=true. Returns { ok, grantsApplied }.",
      inputSchema: {
        contentType: z.enum(["datasource", "workbook"]),
        contentId: z.string().min(1).describe("LUID of the content."),
        grants: z
          .array(
            z.object({
              granteeId: z.string().min(1).describe("LUID of the user or group."),
              granteeType: z.enum(["user", "group"]).default("user"),
              capability: z.string().min(1).describe("Capability name, e.g. Read, Write, Connect."),
              mode: z.enum(["Allow", "Deny"]),
            }),
          )
          .min(1),
        confirmElevated: z
          .boolean()
          .default(false)
          .describe("Required to grant high-impact capabilities (Delete, ChangePermissions, …)."),
      },
      outputSchema: { ok: z.boolean(), grantsApplied: z.number() },
    },
    async ({ contentType, contentId, grants, confirmElevated }) => {
      const invalid = grants.filter((g) => !ALLOWED_CAPABILITIES.has(g.capability));
      if (invalid.length > 0) {
        throw new Error(
          `Unknown capability/capabilities: ${invalid.map((g) => g.capability).join(", ")}. ` +
            `Allowed: ${[...ALLOWED_CAPABILITIES].join(", ")}.`,
        );
      }
      const elevated = grants.filter((g) => ELEVATED_CAPABILITIES.has(g.capability));
      if (elevated.length > 0 && !confirmElevated) {
        throw new Error(
          `Refusing to grant elevated capabilities (${elevated.map((g) => g.capability).join(", ")}) ` +
            `without confirmElevated=true.`,
        );
      }
      await ctx.rest.setPermissions(contentType, contentId, grants);
      return toolResult(`Applied ${grants.length} grant(s) to ${contentType} ${contentId}.`, {
        ok: true,
        grantsApplied: grants.length,
      });
    },
  );
}
