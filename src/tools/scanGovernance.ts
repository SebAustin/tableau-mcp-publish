import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import {
  scanContent,
  DEFAULT_STALE_DAYS,
  type ContentRef,
} from "../governance/checks.js";

/**
 * `scan_governance` — a governance-lite site audit (external-skill-suite Governance-Scanner,
 * deterministic subset). Reads the existing `list_content` surface and flags
 * three hygiene issues: stale content, publishing to the Default project, and
 * inconsistent naming. Owner-concentration, adoption/view-count, and
 * certification checks are intentionally out of scope — they need read
 * introspection this publish-oriented server does not carry (ADR-0014, GAPS §4).
 */
export function registerScanGovernance(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "scan_governance",
    {
      title: "Scan site governance (lite)",
      description:
        "Audit published content for three deterministic governance issues: stale (not updated in staleDays), Default-project residence, and inconsistent naming. Read-only. Owner/adoption/certification checks are NOT covered (out of this server's read scope). Returns { findings, summary }.",
      inputSchema: {
        staleDays: z
          .number()
          .int()
          .positive()
          .default(DEFAULT_STALE_DAYS)
          .describe("Content not updated in this many days flags 'stale' (default 180)."),
        namingPattern: z
          .string()
          .max(200) // NX-01: bound the caller-supplied regex source to blunt ReDoS backtracking.
          .optional()
          .describe(
            "Optional regex source; a content name matching it flags 'naming'. Defaults to a leading/trailing/double-whitespace check.",
          ),
      },
      outputSchema: {
        findings: z.array(
          z.object({
            item: z.object({
              id: z.string(),
              name: z.string(),
              type: z.enum(["datasource", "workbook"]),
            }),
            check: z.enum(["stale", "default_project", "naming"]),
            severity: z.enum(["info", "warn"]),
            detail: z.string(),
          }),
        ),
        summary: z.object({
          scanned: z.number(),
          staleUndetermined: z.number(),
          byCheck: z.object({
            stale: z.number(),
            default_project: z.number(),
            naming: z.number(),
          }),
        }),
      },
    },
    async ({ staleDays, namingPattern }) => {
      const items = (await ctx.rest.listContent()) as ContentRef[];
      const result = scanContent(items, {
        nowIso: new Date().toISOString(),
        staleDays,
        ...(namingPattern ? { namingPattern: new RegExp(namingPattern) } : {}),
      });
      const n = result.findings.length;
      return toolResult(
        `Scanned ${result.summary.scanned} item(s); ${n} governance finding(s).`,
        { findings: result.findings, summary: result.summary },
      );
    },
  );
}
