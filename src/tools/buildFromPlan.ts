/**
 * build_from_plan — M6
 *
 * Accepts a DashboardPlan (from design_dashboard) and:
 *   1. Builds a .twbx file with worksheets + a dashboard via the sidecar.
 *   2. Publishes the .twbx to the specified Tableau Cloud project.
 *
 * Returns { workbookLuid, url }.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { DashboardPlanSchema } from "../planner/schema.js";
import { AUDIENCE_CONSTRAINTS } from "../planner/audience.js";
import type { DashboardPlan } from "../planner/schema.js";

// ---------------------------------------------------------------------------
// R-7: Placeholder token rejection
// ---------------------------------------------------------------------------

const PLACEHOLDER_RE = /^<(measure|dimension)>$/;

/**
 * Throws if any sheet field still contains a placeholder token
 * (e.g. "<measure>" or "<dimension>").  Called before any sidecar/REST work.
 */
function assertNoPlaceholders(plan: DashboardPlan): void {
  for (const sheet of plan.sheets) {
    const allFields = [...sheet.cols, ...sheet.rows, ...sheet.measures];
    for (const field of allFields) {
      if (PLACEHOLDER_RE.test(field)) {
        throw new Error(
          `build_from_plan: sheet "${sheet.title}" contains placeholder token "${field}". ` +
            "Replace all <measure> / <dimension> tokens with real field names before calling build_from_plan.",
        );
      }
    }
  }
}

// ---------------------------------------------------------------------------
// R-5: Audience invariant re-validation (defense in depth, PLAN §4.2)
// ---------------------------------------------------------------------------

/**
 * Re-validates per-sheet audience invariants from PLAN §4.2:
 *   - markType ∈ allowedMarkTypes[audience]
 *   - measures.length ≤ maxMeasures[audience]
 *   - cols.length + rows.length ≤ maxDimensions[audience]
 *
 * This is a build-time guard that catches hand-edited plans that violate the
 * constraints the planner enforced at design time.
 */
function assertAudienceInvariants(plan: DashboardPlan): void {
  const c = AUDIENCE_CONSTRAINTS[plan.audience];
  for (const sheet of plan.sheets) {
    if (!c.allowedMarkTypes.includes(sheet.markType)) {
      throw new Error(
        `build_from_plan: sheet "${sheet.title}" has markType "${sheet.markType}" which is not ` +
          `allowed for audience "${plan.audience}". Allowed: ${c.allowedMarkTypes.join(", ")}.`,
      );
    }
    if (sheet.measures.length > c.maxMeasuresPerSheet) {
      throw new Error(
        `build_from_plan: sheet "${sheet.title}" has ${sheet.measures.length} measures but ` +
          `audience "${plan.audience}" allows at most ${c.maxMeasuresPerSheet}.`,
      );
    }
    const dimTotal = sheet.cols.length + sheet.rows.length;
    if (dimTotal > c.maxDimensionsPerSheet) {
      throw new Error(
        `build_from_plan: sheet "${sheet.title}" has ${dimTotal} dimensions (cols+rows) but ` +
          `audience "${plan.audience}" allows at most ${c.maxDimensionsPerSheet}.`,
      );
    }
  }
}

export function registerBuildFromPlan(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "build_from_plan",
    {
      title: "Build and publish a workbook from a DashboardPlan",
      description:
        "Takes a DashboardPlan JSON object (produced by design_dashboard) and builds a " +
        ".twbx workbook with all planned worksheets plus a tiled dashboard, then publishes " +
        "it to Tableau Cloud. Returns { workbookLuid, url }.",
      inputSchema: {
        plan: z
          .record(z.string(), z.unknown())
          .describe(
            "DashboardPlan object as returned by design_dashboard. " +
              "Must have schemaVersion: 1 and kind: 'plan'.",
          ),
        overwrite: z.boolean().default(false).describe("Overwrite existing workbook with same name."),
      },
      outputSchema: {
        workbookLuid: z.string(),
        url: z.string(),
        datasourceLuid: z.string().optional(),
      },
    },
    async ({ plan: rawPlan, overwrite }) => {
      // Step 1: Parse + schema validate (schemaVersion guard fires here)
      const plan = DashboardPlanSchema.parse(rawPlan);

      // Step 1b: R-7 — reject placeholder tokens before any sidecar/REST work
      assertNoPlaceholders(plan);

      // Step 1c: R-5 — re-validate audience invariants (defense in depth)
      assertAudienceInvariants(plan);

      // E2E-2: if a datasourceSpec is provided, build + publish the datasource first.
      let datasourceLuid = plan.datasourceLuid;
      let newDatasourceLuid: string | undefined;
      if (plan.datasourceSpec) {
        const spec = plan.datasourceSpec;
        const dsProjectId = await ctx.rest.resolveProjectId(plan.projectName);
        let tdsxPath: string;
        if (spec.filePath) {
          ({ tdsxPath } = await ctx.sidecar.buildDatasourceFromFile({
            name: spec.datasourceName,
            filePath: spec.filePath,
            fileType: spec.fileType,
            excelSheet: spec.excelSheet,
            jsonPath: spec.jsonPath,
          }));
        } else {
          ({ tdsxPath } = await ctx.sidecar.buildDatasourceFromQuery({
            connection: spec.connection ?? {},
            sql: spec.sql ?? "",
            name: spec.datasourceName,
          }));
        }
        const ds = await ctx.rest.publishDatasource(
          tdsxPath,
          spec.datasourceName,
          dsProjectId,
          overwrite,
        );
        datasourceLuid = ds.id;
        newDatasourceLuid = ds.id;
      }

      // Resolve the datasource's contentUrl (needed to bind the workbook)
      const { contentUrl } = await ctx.rest.getDatasource(datasourceLuid);

      // Determine canvas dimensions from the audience constraints
      const constraints = AUDIENCE_CONSTRAINTS[plan.audience];

      // Build the .twbx with dashboard via the sidecar
      const { twbxPath } = await ctx.sidecar.buildDashboardWorkbook({
        datasourceName: plan.datasourceName,
        datasourceContentUrl: contentUrl,
        site: ctx.config.siteName,
        serverUrl: ctx.config.server,
        sheets: plan.sheets.map((s) => ({
          title: s.title,
          markType: s.markType,
          rows: s.rows,
          cols: s.cols,
          measures: s.measures,
        })),
        dashboardSheetTitles: plan.sheets.map((s) => s.title),
        dashboardLayout: plan.dashboardLayout as "tiled_vertical" | "tiled_horizontal",
        canvasWidth: constraints.canvasWidth,
        canvasHeight: constraints.canvasHeight,
      });

      // Publish to Tableau Cloud
      const projectId = await ctx.rest.resolveProjectId(plan.projectName);
      const { id, url } = await ctx.rest.publishWorkbook(
        twbxPath,
        plan.workbookName,
        projectId,
        overwrite,
      );

      return toolResult(`Published workbook "${plan.workbookName}" → ${url}`, {
        workbookLuid: id,
        url,
        ...(newDatasourceLuid !== undefined ? { datasourceLuid: newDatasourceLuid } : {}),
      });
    },
  );
}
