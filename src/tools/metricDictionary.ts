import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { deriveMetricDictionary, type DatasourceField } from "../planner/metricDict.js";

/**
 * `generate_metric_dictionary` — a structured metric dictionary for a published
 * datasource (the external skill suite's Scribe "Metric Builder", deterministic subset). Reuses
 * the same VDS field fetch as `get_datasource_fields`, then derives per-field
 * role / suggested aggregation / display format / definition template via the
 * planner's own role inference. Pure derivation; no LLM prose generation (that
 * is the calling agent's job).
 */
export function registerMetricDictionary(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "generate_metric_dictionary",
    {
      title: "Generate a metric dictionary for a datasource",
      description:
        "Derive a structured metric dictionary (role, suggested aggregation, display format, and a " +
        "definition template per field) for a published datasource, from its VDS field metadata. " +
        "Deterministic — reuses the planner's field-role inference and currency/percent word-hints. " +
        "Returns { metrics: [{ name, caption?, role, suggestedAggregation?, format, definitionTemplate }], count }.",
      inputSchema: {
        datasourceLuid: z
          .string()
          .min(1)
          .describe("LUID of a published datasource (as from publish_datasource / get_datasource_fields)."),
      },
      outputSchema: {
        metrics: z.array(
          z.object({
            name: z.string(),
            caption: z.string().optional(),
            role: z.enum(["measure", "dimension", "date"]),
            suggestedAggregation: z.string().optional(),
            format: z.enum(["currency", "percent", "number", "text"]),
            definitionTemplate: z.string(),
          }),
        ),
        count: z.number(),
      },
    },
    async ({ datasourceLuid }) => {
      const rawFields = await ctx.rest.getDatasourceFields(datasourceLuid);
      const fields: DatasourceField[] = rawFields.map((f) => ({
        name: f.fieldName,
        ...(f.fieldCaption !== undefined ? { caption: f.fieldCaption } : {}),
        ...(f.dataType !== undefined ? { dataType: f.dataType } : {}),
        ...(f.defaultAggregation !== undefined ? { defaultAggregation: f.defaultAggregation } : {}),
      }));
      const dict = deriveMetricDictionary(fields);
      const measures = dict.metrics.filter((m) => m.role === "measure").length;
      return toolResult(
        `Metric dictionary for ${datasourceLuid}: ${dict.count} field(s), ${measures} measure(s).`,
        { metrics: dict.metrics, count: dict.count },
      );
    },
  );
}
