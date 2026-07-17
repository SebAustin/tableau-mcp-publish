/**
 * get_datasource_fields — Phase E2, Foundation slice.
 *
 * Fetches REAL field names, data types, and default aggregations for a
 * published datasource via the VizQL Data Service (VDS) `read-metadata`
 * endpoint. Use this before `design_dashboard` to supply accurate
 * `fieldHints` (instead of guessing column names), and as a pre-flight
 * check before creating a Pulse metric definition (Phase E3).
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

const MAX_FIELDS_IN_SUMMARY = 8;

export function registerGetDatasourceFields(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "get_datasource_fields",
    {
      title: "Get real field names for a published datasource",
      description:
        "Fetch the real field names, captions, data types, and default aggregations for a " +
        "published Tableau datasource via the VizQL Data Service (VDS). Use this before " +
        "design_dashboard so its fieldHints reference fields that actually exist, and as a " +
        "pre-flight check before creating a Pulse metric definition. " +
        "Returns { fields: [{ name, caption?, dataType?, defaultAggregation? }], count }.",
      inputSchema: {
        datasourceLuid: z
          .string()
          .min(1)
          .describe(
            "LUID of a published datasource, e.g. from publish_datasource / create_datasource_from_* results.",
          ),
      },
      outputSchema: {
        fields: z.array(
          z.object({
            name: z.string(),
            caption: z.string().optional(),
            dataType: z.string().optional(),
            defaultAggregation: z.string().optional(),
          }),
        ),
        count: z.number(),
      },
    },
    async ({ datasourceLuid }) => {
      const rawFields = await ctx.rest.getDatasourceFields(datasourceLuid);
      const fields = rawFields.map((f) => ({
        name: f.fieldName,
        ...(f.fieldCaption !== undefined ? { caption: f.fieldCaption } : {}),
        ...(f.dataType !== undefined ? { dataType: f.dataType } : {}),
        ...(f.defaultAggregation !== undefined ? { defaultAggregation: f.defaultAggregation } : {}),
      }));

      const summary =
        fields.length > 0
          ? `Found ${fields.length} field(s) on datasource ${datasourceLuid}: ` +
            `${fields
              .slice(0, MAX_FIELDS_IN_SUMMARY)
              .map((f) => f.name)
              .join(", ")}${fields.length > MAX_FIELDS_IN_SUMMARY ? ", …" : ""}.`
          : `Datasource ${datasourceLuid} returned no fields.`;

      return toolResult(summary, { fields, count: fields.length });
    },
  );
}
