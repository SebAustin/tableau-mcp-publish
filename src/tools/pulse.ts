/**
 * Tableau Pulse metric tools — Phase E3 (Pillar D).
 *
 * See `src/rest/pulse.ts` for the full API + VERIFY-LIVE documentation
 * (endpoints, wire body shapes, retry policy, pre-flight validation).
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { CreatePulseDefinitionInputSchema, CreatePulseMetricInputSchema } from "../rest/pulse.js";

/**
 * Always surfaced on `create_pulse_definition` output, mirroring the
 * always-included connectivity note pattern in `tools/schedules.ts`: Pulse's
 * Cloud-only / enablement / permission requirements can't be reliably
 * detected from a successful response, so this is stated unconditionally
 * rather than only on failure.
 */
const PULSE_REQUIREMENTS_NOTE =
  "Tableau Pulse is Cloud-only (not available on Tableau Server) and must be enabled for the site " +
  "under Site Settings > Pulse. The signed-in PAT needs tableau:insight_definitions:create/update " +
  "scope, and the datasource must be a single PUBLISHED datasource on which this PAT has write+" +
  "publish permission. Personas with preferredArtifact=\"pulse\" in brand.yaml (see validate_brand) " +
  "should prefer this tool over dashboards/stories for single-metric, subscription-style tracking.";

export function registerPulseTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_pulse_definition",
    {
      title: "Create a Tableau Pulse metric definition",
      description:
        "Create a Tableau Pulse metric definition (Cloud-only). Runs a VDS-backed pre-flight " +
        "check before calling Pulse: verifies the measure and time_dimension fields exist on the " +
        "datasource (failing loudly with the full field list on a mismatch) and warns — without " +
        "blocking — when the measure's VDS defaultAggregation disagrees with the requested " +
        "aggregation. Pass skipPreflight=true to skip this check (e.g. when VDS itself is " +
        "unavailable); when VDS fails on its own, pre-flight is automatically skipped with a " +
        "warning rather than blocking creation outright. " +
        PULSE_REQUIREMENTS_NOTE +
        " Returns { definitionId, name, note }.",
      inputSchema: {
        ...CreatePulseDefinitionInputSchema.shape,
        skipPreflight: z
          .boolean()
          .default(false)
          .describe("Skip the VDS pre-flight field check. Use when VDS is known to be unavailable."),
      },
      outputSchema: {
        definitionId: z.string(),
        name: z.string(),
        note: z.string(),
      },
    },
    async ({ skipPreflight, ...definitionInput }) => {
      const { definition, warnings } = await ctx.rest.createPulseDefinition(definitionInput, {
        skipPreflight,
      });
      const note = warnings.length > 0 ? `${PULSE_REQUIREMENTS_NOTE} Pre-flight notes: ${warnings.join(" ")}` : PULSE_REQUIREMENTS_NOTE;
      return toolResult(`Created Pulse metric definition "${definition.name}" (${definition.definitionId}).`, {
        definitionId: definition.definitionId,
        name: definition.name,
        note,
      });
    },
  );

  server.registerTool(
    "list_pulse_definitions",
    {
      title: "List Tableau Pulse metric definitions",
      description:
        "List every Tableau Pulse metric definition on the site (Cloud-only; requires " +
        "tableau:insight_definitions_metrics:read PAT scope). Returns { definitions: [...], count }.",
      inputSchema: {},
      outputSchema: {
        definitions: z.array(
          z.object({
            definitionId: z.string(),
            name: z.string(),
            datasourceLuid: z.string().optional(),
          }),
        ),
        count: z.number(),
      },
    },
    async () => {
      const definitions = await ctx.rest.listPulseDefinitions();
      return toolResult(`Found ${definitions.length} Pulse metric definition(s).`, {
        definitions,
        count: definitions.length,
      });
    },
  );

  server.registerTool(
    "create_pulse_metric",
    {
      title: "Create a Tableau Pulse metric",
      description:
        "Create a Tableau Pulse metric instance from an existing definition (Cloud-only; requires " +
        "tableau:insight_metrics:create PAT scope). granularity/range/comparison default to " +
        "GRANULARITY_BY_MONTH / RANGE_LAST_COMPLETE / TIME_COMPARISON_PREVIOUS_PERIOD — the only " +
        "tokens confirmed against Tableau's official pulse-api-utilities reference; any other " +
        "accepted value is VERIFY-LIVE (unconfirmed against a live site). Returns { metricId }.",
      inputSchema: CreatePulseMetricInputSchema.shape,
      outputSchema: { metricId: z.string() },
    },
    async (input) => {
      const metric = await ctx.rest.createPulseMetric(input);
      return toolResult(`Created Pulse metric ${metric.metricId} for definition ${input.definitionId}.`, {
        metricId: metric.metricId,
      });
    },
  );

  server.registerTool(
    "delete_pulse_definition",
    {
      title: "Delete a Tableau Pulse metric definition",
      description:
        "Delete a Tableau Pulse metric definition by id (Cloud-only). DESTRUCTIVE and " +
        "irreversible, and also removes any metrics built on it — requires confirm=true. The " +
        "exact delete response shape is unconfirmed against a live site (VERIFY-LIVE) — per " +
        "Tableau's own guidance, create one definition in the UI and GET it back to lock a " +
        "fixture before relying on this in production. Returns { deleted, definitionId }.",
      inputSchema: {
        definitionId: z.string().min(1).describe("id from create_pulse_definition or list_pulse_definitions."),
        confirm: z
          .boolean()
          .default(false)
          .describe("Must be explicitly true; deletion is refused otherwise."),
      },
      outputSchema: { deleted: z.boolean(), definitionId: z.string() },
    },
    async ({ definitionId, confirm }) => {
      if (!confirm) {
        throw new Error(
          `Refusing to delete Pulse definition ${definitionId}: pass confirm=true to authorize this irreversible action.`,
        );
      }
      await ctx.rest.deletePulseDefinition(definitionId);
      return toolResult(`Deleted Pulse metric definition ${definitionId}.`, {
        deleted: true,
        definitionId,
      });
    },
  );
}
