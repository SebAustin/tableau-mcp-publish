/**
 * validate_brand — Phase E1, Slice A.
 *
 * Loads and validates brand.yaml (or a caller-supplied path) and reports the
 * result. This tool NEVER throws on an invalid/malformed brand file — it
 * always returns { valid, warnings, personas, summary } so an agent can
 * surface actionable feedback to the user without a hard failure.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { loadBrand } from "../branding/load.js";

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function registerValidateBrand(server: McpServer, ctx: ToolContext): void {
  void ctx; // brand loading is local file I/O — no REST/sidecar dependency needed

  server.registerTool(
    "validate_brand",
    {
      title: "Validate brand.yaml",
      description:
        "Loads and validates the brand kit file (brand.yaml) that drives palette, typography, " +
        "number formats, and named personas used by design_dashboard. Never throws on an invalid " +
        "or missing file — always returns { valid, warnings, personas, summary } so problems can " +
        "be surfaced to the user instead of failing the call.",
      inputSchema: {
        path: z
          .string()
          .min(1)
          .optional()
          .describe("Optional path to brand.yaml. Defaults to the repo-root brand.yaml."),
      },
      outputSchema: {
        valid: z.boolean(),
        warnings: z.array(z.string()),
        personas: z.array(z.string()),
        summary: z.string(),
      },
    },
    async ({ path }) => {
      try {
        const { brand, warnings } = loadBrand(path);
        const personas = Object.keys(brand.personas).sort();
        const summary =
          `Brand "${brand.brand.name}" is valid with ${personas.length} persona(s): ` +
          `${personas.length > 0 ? personas.join(", ") : "(none)"}.` +
          (warnings.length > 0 ? ` ${warnings.length} warning(s): ${warnings.join(" ")}` : "");

        return toolResult(summary, { valid: true, warnings, personas, summary });
      } catch (err: unknown) {
        const message = getErrorMessage(err);
        const summary = `Brand file is invalid: ${message}`;

        return toolResult(summary, {
          valid: false,
          warnings: [message],
          personas: [],
          summary,
        });
      }
    },
  );
}
