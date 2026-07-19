/**
 * build_from_plan — M6
 *
 * Accepts a DashboardPlan (from design_dashboard) and:
 *   1. Builds a .twbx file with worksheets + a dashboard via the sidecar.
 *   2. Publishes the .twbx to the specified Tableau Cloud project.
 *
 * Returns { workbookLuid, url }.
 *
 * ## Embedded-extract requirement
 *
 * Tableau Cloud only renders a workbook when it can resolve all datasource
 * bindings at publish time.  The only self-contained path is the *embedded
 * extract* (federated connection): the .hyper file produced by
 * `buildDatasourceFromFile` is zipped directly into the .twbx.
 *
 * `build_from_plan` therefore requires the datasource to be built from a
 * local file in the same call via `datasourceSpec.filePath`.  Two scenarios
 * are rejected loudly:
 *
 * 1. **LUID-only (no `datasourceSpec`)** — the .hyper file is not available
 *    on disk; embedding is impossible.  Binding to a pre-published datasource
 *    via `datasourceLuid` alone produces a non-rendering workbook (error
 *    400011 on Tableau Cloud).
 *
 * 2. **`datasourceSpec.sql` (query branch)** — `buildDatasourceFromQuery` does
 *    not materialise a .hyper file that can be embedded; only a .tdsx is
 *    produced.  Embedding is not possible on this path.
 *
 * In both cases the tool throws an actionable error rather than silently
 * publishing a workbook that will fail to render.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { DashboardPlanSchema } from "../planner/schema.js";
import { AUDIENCE_CONSTRAINTS } from "../planner/audience.js";
import type { DashboardPlan } from "../planner/schema.js";
import { loadBrand } from "../branding/load.js";
import { toBuilderBrand } from "../branding/builderBrand.js";
import type { WorkbookBrand, Story } from "../sidecar.js";

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

// ---------------------------------------------------------------------------
// Phase E4: storyArc capturedSheet re-validation (defense in depth)
// ---------------------------------------------------------------------------

/**
 * Re-validates that every `plan.storyArc[].capturedSheet` names one of this
 * plan's own sheet titles. `generatePlan()`'s `buildStoryArc` only ever
 * emits references to `plan.sheets[].title`, but a hand-edited plan could
 * violate that — this is a build-time guard, additional to (not a
 * replacement for) the sidecar's own validation against the actual built
 * workbook (`twb_builder._build_story` raises `ValueError` there too).
 */
function assertStoryArcCapturedSheetsExist(plan: DashboardPlan): void {
  if (!plan.storyArc || plan.storyArc.length === 0) return;
  const validTitles = new Set(plan.sheets.map((s) => s.title));
  for (const point of plan.storyArc) {
    if (!validTitles.has(point.capturedSheet)) {
      throw new Error(
        `build_from_plan: storyArc point "${point.caption}" references capturedSheet ` +
          `"${point.capturedSheet}" which is not one of this plan's sheet titles: ` +
          `${[...validTitles].join(", ")}.`,
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
        "it to Tableau Cloud. Returns { workbookLuid, url }.\n\n" +
        "IMPORTANT: Rendering a dashboard on Tableau Cloud requires embedding the source " +
        "extract directly in the workbook (federated connection). This is only possible " +
        "when the datasource is built from a local file in the same call via " +
        "`datasourceSpec.filePath`. Supplying only a pre-published `datasourceLuid` " +
        "(no `datasourceSpec`) or using `datasourceSpec.sql` will cause the tool to " +
        "throw an actionable error rather than publish a non-rendering workbook.",
      inputSchema: {
        plan: z
          .record(z.string(), z.unknown())
          .describe(
            "DashboardPlan object as returned by design_dashboard. " +
              "Must have schemaVersion: 1 and kind: 'plan'.",
          ),
        overwrite: z.boolean().default(false).describe("Overwrite existing workbook with same name."),
        brandPath: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Optional path to brand.yaml, used to apply branding (palette, typography, " +
              "number formats) to the built workbook. Defaults to the repo-root brand.yaml " +
              "when this is omitted but `plan.personaName` or `plan.brandName` is set (i.e. " +
              "the plan came from a persona-resolved design_dashboard call). Branding is " +
              "skipped entirely when none of the three are present.",
          ),
      },
      outputSchema: {
        workbookLuid: z.string(),
        url: z.string(),
        datasourceLuid: z.string().optional(),
      },
    },
    async ({ plan: rawPlan, overwrite, brandPath }) => {
      // Step 1: Parse + schema validate (schemaVersion guard fires here)
      const plan = DashboardPlanSchema.parse(rawPlan);

      // Step 1b: R-7 — reject placeholder tokens before any sidecar/REST work
      assertNoPlaceholders(plan);

      // Step 1c: R-5 — re-validate audience invariants (defense in depth)
      assertAudienceInvariants(plan);

      // Step 1d: Phase E4 — re-validate storyArc capturedSheet references
      // (defense in depth, mirrors R-5's rationale)
      assertStoryArcCapturedSheetsExist(plan);

      // ---------------------------------------------------------------------
      // Phase E1 (Slice B): resolve branding for the workbook.
      //
      // Triggered when the plan carries persona/brand provenance (set by
      // design_dashboard when `persona` was supplied) OR when this call
      // explicitly passes `brandPath`. The plan itself does not persist the
      // brandPath used at design time, so this always reads brand.yaml at the
      // DEFAULT_BRAND_PATH unless `brandPath` overrides it — matching the
      // design_dashboard/loadBrand convention (only I/O in the tool layer).
      // ---------------------------------------------------------------------
      let brand: WorkbookBrand | undefined;
      if (plan.personaName || plan.brandName || brandPath) {
        const { brand: brandFile } = loadBrand(brandPath);
        brand = toBuilderBrand(brandFile);
      }

      // ---------------------------------------------------------------------------
      // E2E-2: Build + publish the datasource when a datasourceSpec is provided.
      // ---------------------------------------------------------------------------
      // Rendering a dashboard on Tableau Cloud requires embedding the .hyper
      // extract directly in the .twbx (federated connection, self-contained).
      // Only `datasourceSpec.filePath` yields a hyperPath that can be embedded.
      //
      // Two non-embeddable scenarios are rejected loudly:
      //   • LUID-only (no datasourceSpec): the source extract is not on disk.
      //   • datasourceSpec.sql: buildDatasourceFromQuery produces no hyperPath.
      // ---------------------------------------------------------------------------

      if (!plan.datasourceSpec) {
        throw new Error(
          "build_from_plan: rendering a dashboard requires building the datasource in the same " +
            "call (provide `datasourceSpec` with a `filePath`) so the extract can be embedded " +
            "into the workbook. Binding to a pre-published datasource via `datasourceLuid` alone " +
            "produces a non-rendering workbook (Tableau Cloud error 400011). " +
            "Add `datasourceSpec.filePath` to supply the source file.",
        );
      }

      const spec = plan.datasourceSpec;

      if (!spec.filePath) {
        // sql/query branch — buildDatasourceFromQuery does not produce a .hyper extract.
        throw new Error(
          "build_from_plan: rendering a dashboard requires an embeddable extract. " +
            "The `datasourceSpec.sql` (query) path does not produce a .hyper file that can be " +
            "embedded in the workbook. Use `datasourceSpec.filePath` to supply a local CSV, " +
            "Excel, JSON, or Parquet file so the extract can be built and embedded.",
        );
      }

      const dsProjectId = await ctx.rest.resolveProjectId(plan.projectName);
      const { tdsxPath, hyperPath } = await ctx.sidecar.buildDatasourceFromFile({
        name: spec.datasourceName,
        filePath: spec.filePath,
        fileType: spec.fileType,
        excelSheet: spec.excelSheet,
        jsonPath: spec.jsonPath,
      });

      const ds = await ctx.rest.publishDatasource(
        tdsxPath,
        spec.datasourceName,
        dsProjectId,
        overwrite,
      );
      const datasourceLuid = ds.id;
      const newDatasourceLuid: string = ds.id;

      // Resolve the datasource's contentUrl (needed to bind the workbook)
      const { contentUrl } = await ctx.rest.getDatasource(datasourceLuid);

      // Determine canvas dimensions from the audience constraints
      const constraints = AUDIENCE_CONSTRAINTS[plan.audience];

      // Phase E4: thread plan.storyArc → a single Story, applying the
      // "Story: " name prefix the Tableau story dashboard name requires
      // (see twb_builder._build_story's docstring for the verified shape).
      // Undefined when the plan carries no storyArc, keeping the pre-story
      // request byte-identical.
      const stories: Story[] | undefined =
        plan.storyArc && plan.storyArc.length > 0
          ? [
              {
                name: `Story: ${plan.storyName ?? plan.dashboardTitle ?? plan.workbookName}`,
                points: plan.storyArc.map((p) => ({
                  caption: p.caption,
                  capturedSheet: p.capturedSheet,
                })),
              },
            ]
          : undefined;

      // Build the .twbx with the embedded extract via the sidecar.
      // Passing hyperPath uses the federated-connection path in server.py
      // (/workbook/dashboard → build_embedded_twbx) so the workbook renders
      // on Tableau Cloud without a separately published datasource binding.
      const { twbxPath } = await ctx.sidecar.buildDashboardWorkbook({
        datasourceName: plan.datasourceName,
        datasourceContentUrl: contentUrl,
        site: ctx.config.siteName,
        serverUrl: ctx.config.server,
        // Thread the FULL sheet spec — the rich encoding fields (kind/color/
        // kpi/scatter/geo) are what make the dashboard executive-grade; the
        // dashboard-level fields carry the title/layout grammar. Dropping any
        // of these here would silently downgrade the tool output vs. the plan
        // the user confirmed (tool-vs-demo divergence guard).
        sheets: plan.sheets.map((s) => ({
          title: s.title,
          markType: s.markType,
          rows: s.rows,
          cols: s.cols,
          measures: s.measures,
          ...(s.kind ? { kind: s.kind } : {}),
          ...(s.color ? { color: s.color } : {}),
          ...(s.kpi ? { kpi: s.kpi } : {}),
          ...(s.scatter ? { scatter: s.scatter } : {}),
          ...(s.geo ? { geo: s.geo } : {}),
        })),
        dashboardSheetTitles: plan.sheets.map((s) => s.title),
        dashboardLayout: plan.dashboardLayout as "tiled_vertical" | "tiled_horizontal",
        canvasWidth: constraints.canvasWidth,
        canvasHeight: constraints.canvasHeight,
        hyperPath,
        brand,
        ...(plan.dashboardTitle ? { dashboardTitle: plan.dashboardTitle } : {}),
        ...(plan.dashboardSubtitle ? { dashboardSubtitle: plan.dashboardSubtitle } : {}),
        ...(plan.textZones ? { textZones: plan.textZones } : {}),
        ...(plan.layoutGrammar ? { layoutGrammar: plan.layoutGrammar } : {}),
        // Design Excellence, Slice D5: forward the plan's resolved design
        // theme (attached by design_dashboard's selectTheme/toDesignTheme
        // step) so the sidecar actually applies it — carrying it this far
        // and dropping it here would silently discard everything D2-D5
        // built on top of it.
        ...(plan.designTheme ? { designTheme: plan.designTheme } : {}),
        ...(stories ? { stories } : {}),
        // Design Excellence, Slice D7: forward the plan's interaction
        // toggles (auto-set by design_dashboard when a theme was selected
        // AND the plan has >=2 chart sheets) — same forwarding discipline as
        // designTheme/stories above.
        ...(plan.interactions ? { interactions: plan.interactions } : {}),
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
        datasourceLuid: newDatasourceLuid,
      });
    },
  );
}
