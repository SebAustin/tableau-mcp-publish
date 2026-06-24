/**
 * design_dashboard — M5
 *
 * Generates a DashboardPlan (or ClarifyingQuestions) from a set of inputs
 * using the pure planning pipeline (src/planner/).  No sidecar or REST calls.
 * The resulting plan can be passed to build_from_plan (M6).
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import { generatePlan, generateInterview } from "../planner/plan.js";
import type { PlanInput } from "../planner/plan.js";
import { FieldHintSchema, AudienceEnum, DashboardLayoutEnum } from "../planner/schema.js";

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
// Registration
// ---------------------------------------------------------------------------

export function registerDesignDashboard(server: McpServer, ctx: ToolContext): void {
  void ctx; // context not needed for pure planning — required by ToolContext signature

  server.registerTool(
    "design_dashboard",
    {
      title: "Design a dashboard plan from a business question",
      description:
        "Generates a DashboardPlan JSON object (or a ClarifyingQuestions object) from a " +
        "business question, target audience, and optional field hints. Three modes:\n" +
        "  • autonomous: derive everything from businessQuestion + fieldHints.\n" +
        "  • interview: return clarifying questions before planning (pass answers back as interview_followup).\n" +
        "  • interview_followup: use the answers dict to produce the final plan.\n" +
        "  • directed: use an explicit directions string (ignores businessQuestion).\n" +
        "The returned plan can be passed to build_from_plan to generate and publish a workbook.",
      inputSchema: {
        mode: z
          .enum(["autonomous", "interview", "interview_followup", "directed"])
          .describe(
            "Planning mode. " +
              "Use 'interview' to get clarifying questions first. " +
              "Use 'directed' to provide explicit sheet directions.",
          ),
        audience: audienceField,
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
        plan: z.unknown().describe("DashboardPlan or ClarifyingQuestions JSON object."),
      },
    },
    async ({
      mode,
      audience,
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
      if (mode === "interview") {
        const result = generateInterview({
          audience,
          businessQuestion,
          fieldHints,
        });
        return toolResult("Clarifying questions generated. Pass answers to design_dashboard (interview_followup mode).", {
          plan: result,
        });
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
        audience: audience ?? "mixed",
        businessQuestion,
        directions,
        answers,
        fieldHints,
        datasourceLuid,
        datasourceName,
        projectName,
        workbookName,
        requestedLayout,
      };

      const plan = generatePlan(planInput);
      return toolResult(`Dashboard plan "${plan.workbookName}" generated (${plan.sheets.length} sheet(s)).`, {
        plan,
      });
    },
  );
}
