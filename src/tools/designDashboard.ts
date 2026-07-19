/**
 * design_dashboard — M5 / Slice 5: propose→confirm contract.
 *
 * Returns a DashboardProposal (kind:"proposal") once enough context is known,
 * or a ClarifyingQuestions (kind:"questions") in interview mode.
 *
 * This tool NEVER builds or publishes anything.  Building is a separate step
 * triggered by the user confirming the proposal.
 *
 * ## Agent loop (STATELESS — the agent drives)
 *
 *  1. Agent calls `design_dashboard` (autonomous / directed / interview_followup).
 *  2. Server returns a `kind:"proposal"` object.
 *  3. Agent presents the proposal to the user:
 *       - Human-readable text: summary, kpiStrip, views, layoutSummary.
 *       - Optional: render an SVG/HTML wireframe from kpiStrip/views/layoutSummary
 *         using a visualise tool (presentation only; no server call required).
 *  4a. User says "change X" → agent re-calls `design_dashboard` in directed mode
 *      with updated `directions` for a fresh proposal (server is stateless; no
 *      session state is stored).
 *  4b. User says "confirm" → agent calls `build_from_plan` with `proposal.plan`
 *      VERBATIM.  Do NOT modify proposal.plan before passing it.
 *
 * No state is stored server-side between calls.  Each call to `design_dashboard`
 * is independent.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { generatePlan, generateInterview } from "../planner/plan.js";
import type { PlanInput } from "../planner/plan.js";
import { buildProposal } from "../planner/proposal.js";
import { FieldHintSchema, AudienceEnum, DashboardLayoutEnum } from "../planner/schema.js";
import type { Audience, DashboardPlan, DesignTheme } from "../planner/schema.js";
import type { AudienceConstraintOverrides } from "../planner/audience.js";
import { loadBrand, resolvePersona } from "../branding/load.js";
import { loadThemes } from "../design/loadThemes.js";
import { selectTheme } from "../design/selectTheme.js";
import { toDesignTheme } from "../design/schema.js";
import type { ThemeFile } from "../design/schema.js";

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// Shared sub-schemas
// ---------------------------------------------------------------------------

const fieldHintsArray = z
  .array(FieldHintSchema)
  .optional()
  .describe("Optional list of field names and types from the target datasource.");

const audienceField = AudienceEnum.optional().describe(
  "Target audience: exec | analyst | operational | mixed.",
);

// ---------------------------------------------------------------------------
// Human-readable proposal formatter
// ---------------------------------------------------------------------------

/**
 * Format a proposal as a readable multi-line string for the tool's text
 * content so the calling agent can relay it directly to the user.
 */
function formatProposalText(proposal: ReturnType<typeof buildProposal>): string {
  const lines: string[] = [];

  lines.push(`Dashboard Proposal: "${proposal.workbookName}"`);
  if (proposal.dashboardTitle) lines.push(`Title: ${proposal.dashboardTitle}`);
  if (proposal.dashboardSubtitle) lines.push(`Subtitle: ${proposal.dashboardSubtitle}`);
  lines.push("");
  lines.push(`Summary: ${proposal.summary}`);
  lines.push("");

  if (proposal.kpiStrip.length > 0) {
    lines.push("KPI Strip:");
    for (const kpi of proposal.kpiStrip) {
      const delta =
        kpi.deltaMeasure
          ? ` | Δ ${kpi.deltaMeasure} (${kpi.direction})`
          : ` (${kpi.direction})`;
      const comparison = kpi.comparisonMeasure ? ` vs ${kpi.comparisonMeasure}` : "";
      lines.push(`  • ${kpi.label}: ${kpi.primaryMeasure}${comparison}${delta}`);
    }
    lines.push("");
  }

  if (proposal.views.length > 0) {
    lines.push("Charts:");
    for (const view of proposal.views) {
      lines.push(`  • ${view.title}: ${view.encodingSummary}`);
    }
    lines.push("");
  }

  lines.push(`Layout: ${proposal.layoutSummary}`);

  if (proposal.openQuestions && proposal.openQuestions.length > 0) {
    lines.push("");
    lines.push("Open Questions:");
    for (const q of proposal.openQuestions) {
      lines.push(`  • ${q}`);
    }
  }

  lines.push("");
  lines.push(
    "To confirm this proposal, call build_from_plan with proposal.plan verbatim. " +
      "To request changes, call design_dashboard again in directed mode with updated directions.",
  );

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export function registerDesignDashboard(server: McpServer, ctx: ToolContext): void {
  void ctx; // context not needed for pure planning — required by ToolContext signature

  server.registerTool(
    "design_dashboard",
    {
      title: "Design a dashboard — returns a confirmable proposal",
      description:
        "Generates a DashboardProposal (kind:'proposal') or ClarifyingQuestions (kind:'questions') " +
        "from a business question, target audience, and optional field hints.\n\n" +
        "## Branding & personas\n" +
        "  • Pass `persona` (e.g. 'ceo', 'cto', 'analyst') to resolve a named audience profile " +
        "from brand.yaml — its base audience is used when `audience` is omitted, and its overrides " +
        "(e.g. maxSheets) are applied. The proposal summary names the persona and brand.\n" +
        "  • Use the `validate_brand` tool to check brand.yaml and list available persona names.\n\n" +
        "## Modes\n" +
        "  • autonomous: derive everything from businessQuestion + fieldHints.\n" +
        "  • interview: return clarifying questions before planning " +
        "(pass answers back as interview_followup).\n" +
        "  • interview_followup: use the answers dict to produce the final proposal.\n" +
        "  • directed: use an explicit directions string to adjust the design.\n\n" +
        "## Propose→confirm loop (STATELESS — the agent drives)\n" +
        "  1. Call design_dashboard → receive kind:'proposal'.\n" +
        "  2. Present the proposal to the user (text + optional wireframe from " +
        "kpiStrip/views/layoutSummary — presentation only, no server call).\n" +
        "  3a. User says 'change X' → re-call design_dashboard in directed mode " +
        "with updated directions for a fresh proposal.\n" +
        "  3b. User says 'confirm' → call build_from_plan with proposal.plan VERBATIM.\n\n" +
        "design_dashboard NEVER builds or publishes. " +
        "build_from_plan is ALWAYS a separate step triggered by explicit user confirmation.",
      inputSchema: {
        mode: z
          .enum(["autonomous", "interview", "interview_followup", "directed"])
          .describe(
            "Planning mode. " +
              "Use 'interview' to get clarifying questions first. " +
              "Use 'directed' to provide explicit sheet directions.",
          ),
        audience: audienceField,
        persona: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Named persona from brand.yaml (e.g. 'ceo', 'cto', 'analyst'). When provided, " +
              "resolves the persona's base audience (used when `audience` is omitted) and applies " +
              "the persona's overrides (e.g. maxSheets). The proposal summary names the persona " +
              "and the brand. `audience`, if also supplied, takes precedence over the persona's base.",
          ),
        brandPath: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Optional path to brand.yaml, used only when `persona` is supplied. " +
              "Defaults to the repo-root brand.yaml.",
          ),
        businessQuestion: z
          .string()
          .optional()
          .describe("The business question the dashboard should answer (autonomous/followup modes)."),
        directions: z
          .string()
          .min(1)
          .optional()
          .describe(
            "Directed mode: explicit description of desired sheets, e.g. " +
              "'a bar chart of revenue by region and a trend line over time'.",
          ),
        answers: z
          .record(z.string(), z.string())
          .optional()
          .describe("interview_followup mode: map of question IDs to user answers."),
        fieldHints: fieldHintsArray,
        datasourceLuid: z
          .string()
          .min(1)
          .optional()
          .describe("LUID of the target datasource (required for autonomous/directed/followup)."),
        datasourceName: z
          .string()
          .min(1)
          .optional()
          .describe("Display name of the target datasource."),
        projectName: z
          .string()
          .min(1)
          .optional()
          .describe("Target project name (propagated to the plan for build_from_plan)."),
        workbookName: z.string().min(1).optional().describe("Override the auto-derived workbook name."),
        requestedLayout: DashboardLayoutEnum.optional().describe(
          "Preferred dashboard layout: tiled_vertical | tiled_horizontal. " +
            "Ignored for operational audience (always tiled_vertical).",
        ),
      },
      outputSchema: {
        result: z.unknown().describe(
          "DashboardProposal (kind:'proposal') or ClarifyingQuestions (kind:'questions') JSON object. " +
            "For proposals, pass result.plan verbatim to build_from_plan on user confirmation.",
        ),
      },
    },
    async ({
      mode,
      audience,
      persona,
      brandPath,
      businessQuestion,
      directions,
      answers,
      fieldHints,
      datasourceLuid,
      datasourceName,
      projectName,
      workbookName,
      requestedLayout,
    }) => {
      // ---------------------------------------------------------------------
      // Phase E1 (Slice A + Slice C): resolve `persona` against brand.yaml.
      //
      // This is the ONLY I/O in this tool — brand.yaml is read here, once,
      // and the resolved values (audience, constraint overrides, persona
      // name, brand name, artifact/tone) are passed into the pure planner.
      // The planner itself never touches the filesystem.
      //
      // Slice C additionally threads `chartDeny` and `kpiEmphasis` into the
      // audience clamp (BI_DESIGN §9, Few/visionary layer), and surfaces
      // `preferredArtifact` / `tone` for `buildProposal` to honestly consume.
      // ---------------------------------------------------------------------
      let resolvedAudience: Audience | undefined = audience;
      let constraintOverrides: AudienceConstraintOverrides | undefined;
      let personaName: string | undefined;
      let brandName: string | undefined;
      let personaPreferredArtifact: PlanInput["personaPreferredArtifact"];
      let personaTone: PlanInput["personaTone"];

      if (persona) {
        const { brand } = loadBrand(brandPath);
        const { audience: personaAudience, overrides } = resolvePersona(brand, persona);
        // An explicit `audience` input always wins over the persona's base.
        resolvedAudience = audience ?? personaAudience;
        personaName = persona;
        brandName = brand.brand.name;
        personaPreferredArtifact = overrides.preferredArtifact;
        personaTone = overrides.tone;

        const overridesToApply: AudienceConstraintOverrides = {
          ...(overrides.maxSheets !== undefined ? { maxSheets: overrides.maxSheets } : {}),
          ...(overrides.chartDeny !== undefined ? { chartDeny: overrides.chartDeny } : {}),
          ...(overrides.kpiEmphasis !== undefined ? { kpiEmphasis: overrides.kpiEmphasis } : {}),
        };
        if (Object.keys(overridesToApply).length > 0) {
          constraintOverrides = overridesToApply;
        }
      }

      // ---------------------------------------------------------------------
      // Design Excellence, Slice D5: load the design-theme corpus.
      //
      // Fail-soft by design — the corpus is an enhancement layer, not a
      // required input: a missing/invalid `design/corpus/themes/` directory
      // (e.g. a stripped-down install) must never block `design_dashboard`
      // from returning a proposal. Selection (`selectTheme`) and mapping
      // (`toDesignTheme`) happen further below, once `plan.audience` and
      // `plan.storyArc` (which determines the `artifact` input) are known.
      // ---------------------------------------------------------------------
      let themes: ThemeFile[] = [];
      try {
        themes = loadThemes();
      } catch (err: unknown) {
        process.stderr.write(
          `design_dashboard: could not load the design theme corpus, proceeding without a ` +
            `design theme: ${getErrorMessage(err)}\n`,
        );
      }

      // interview mode — return ClarifyingQuestions, not a proposal
      if (mode === "interview") {
        const result = generateInterview({
          audience: resolvedAudience,
          businessQuestion,
          fieldHints,
        });
        return toolResult(
          "Clarifying questions generated. Pass answers to design_dashboard (interview_followup mode).",
          { result },
        );
      }

      // directed mode requires directions (MC-2)
      if (mode === "directed" && (!directions || directions.trim().length === 0)) {
        throw new Error(
          "directed mode requires `directions` — provide an explicit description of the desired sheets.",
        );
      }

      // autonomous / interview_followup / directed — all require datasourceLuid etc.
      if (!datasourceLuid) {
        throw new Error("datasourceLuid is required for modes: autonomous, interview_followup, directed.");
      }
      if (!datasourceName) {
        throw new Error("datasourceName is required for modes: autonomous, interview_followup, directed.");
      }
      if (!projectName) {
        throw new Error("projectName is required for modes: autonomous, interview_followup, directed.");
      }

      const planInput: PlanInput = {
        mode: mode === "interview_followup" ? "interview_followup" : mode as "autonomous" | "directed",
        audience: resolvedAudience ?? "mixed",
        businessQuestion,
        directions,
        answers,
        fieldHints,
        datasourceLuid,
        datasourceName,
        projectName,
        workbookName,
        requestedLayout,
        constraintOverrides,
        personaName,
        brandName,
        personaPreferredArtifact,
        personaTone,
      };

      const plan = generatePlan(planInput);

      // ---------------------------------------------------------------------
      // Design Excellence, Slice D5: select + attach a design theme.
      //
      // `artifact` mirrors `build_from_plan`'s own storyArc check
      // (buildFromPlan.ts): a plan carrying a non-empty `storyArc` is a
      // story artifact, otherwise a dashboard. The resolved `designTheme`
      // is embedded in the plan (not looked up again at build time) so
      // `build_from_plan` needs no corpus filesystem I/O and propose→build
      // cannot drift — the same discipline `brand.yaml` resolution already
      // follows for this tool.
      // ---------------------------------------------------------------------
      let designTheme: DesignTheme | undefined;
      if (themes.length > 0) {
        try {
          const artifact = plan.storyArc && plan.storyArc.length > 0 ? "story" : "dashboard";
          const selected = selectTheme(themes, {
            audience: plan.audience,
            personaName,
            artifact,
          });
          designTheme = toDesignTheme(selected);
        } catch (err: unknown) {
          process.stderr.write(
            `design_dashboard: could not select a design theme, proceeding without a design ` +
              `theme: ${getErrorMessage(err)}\n`,
          );
        }
      }

      const themedPlan: DashboardPlan = designTheme ? { ...plan, designTheme } : plan;
      const proposal = buildProposal(themedPlan);

      return toolResult(formatProposalText(proposal), { result: proposal });
    },
  );
}
