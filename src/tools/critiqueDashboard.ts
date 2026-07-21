import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { critiqueDashboardPlan, type BusinessNorms, type CritiquePlan } from "../planner/critique.js";

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
/** Repo-root committed norms, resolved like DEFAULT_THEMES_DIR / DEFAULT_BRAND_PATH. */
export const DEFAULT_VISUAL_NORMS_PATH = resolve(
  MODULE_DIR,
  "..",
  "..",
  "design",
  "corpus",
  "stats",
  "visual_norms.yaml",
);

/** Load the business_dashboard stratum from the committed visual_norms.yaml. */
export function loadBusinessNorms(path: string = DEFAULT_VISUAL_NORMS_PATH): BusinessNorms {
  const doc = parseYaml(readFileSync(path, "utf8")) as {
    strata?: { business_dashboard?: BusinessNorms };
  };
  return doc.strata?.business_dashboard ?? {};
}

/**
 * `critique_dashboard` — VizCritique-lite. Scores a generated DashboardPlan
 * against the committed top-100 business_dashboard norms + WCAG contrast + the
 * ENFORCED Few rules. Deterministic; no LLM (A-01). Reads the norms YAML at the
 * tool boundary; the scorer itself is pure.
 */
export function registerCritiqueDashboard(server: McpServer, ctx: ToolContext): void {
  void ctx; // norms come from the committed corpus, not the live site
  server.registerTool(
    "critique_dashboard",
    {
      title: "Critique a dashboard plan against mined norms",
      description:
        "Deterministically score a generated dashboard plan against the top-100 business-dashboard " +
        "design norms (layout, KPI count, chart mix, title, WCAG contrast). Returns a 0-100 score plus " +
        "per-dimension pass/warn/note verdicts, each citing the mined stat. No LLM judgment. " +
        "Returns { overallScore, dimensions: [{ dimension, verdict, finding, normCited? }] }.",
      inputSchema: {
        plan: z
          .object({
            dashboardTitle: z.string().optional(),
            layoutGrammar: z.object({ kind: z.string().optional() }).passthrough().optional(),
            sheets: z
              .array(
                z
                  .object({
                    kind: z.string().optional(),
                    markType: z.string().optional(),
                    kpi: z
                      .object({
                        comparisonMeasure: z.string().optional(),
                        comparisonKind: z.string().optional(),
                        deltaMeasure: z.string().optional(),
                      })
                      .passthrough()
                      .optional(),
                  })
                  .passthrough(),
              )
              .optional(),
            designTheme: z
              .object({
                dashboardBackground: z.string().optional(),
                header: z.object({}).passthrough().optional(),
                kpiTile: z.object({}).passthrough().optional(),
              })
              .passthrough()
              .optional(),
          })
          .passthrough()
          .describe("A DashboardPlan (from design_dashboard). Extra fields are ignored."),
      },
      outputSchema: {
        overallScore: z.number(),
        dimensions: z.array(
          z.object({
            dimension: z.string(),
            verdict: z.enum(["pass", "warn", "note"]),
            finding: z.string(),
            normCited: z
              .object({
                stat: z.string(),
                observed: z.string(),
                expected: z.string(),
                n: z.number(),
                confidence: z.string(),
              })
              .optional(),
          }),
        ),
      },
    },
    async ({ plan }) => {
      const norms = loadBusinessNorms();
      const result = critiqueDashboardPlan(plan as CritiquePlan, norms);
      const warns = result.dimensions.filter((d) => d.verdict === "warn").length;
      return toolResult(
        `Critique score ${result.overallScore}/100 (${warns} warning(s)).`,
        { overallScore: result.overallScore, dimensions: result.dimensions },
      );
    },
  );
}
