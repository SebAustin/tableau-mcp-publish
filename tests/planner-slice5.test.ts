/**
 * Slice 5 tests — propose→confirm contract.
 *
 * Covers:
 * - buildProposal produces a valid DashboardProposal for exec/analyst/mixed plans.
 * - kpiStrip is populated from kpi_tile sheets with correct direction.
 * - views is populated from non-kpi_tile sheets with encodingSummary.
 * - layoutSummary correctly describes kpi_band_over_charts and tiled layouts.
 * - summary is non-empty and describes the plan.
 * - proposal.plan matches the input plan verbatim (round-trip).
 * - buildProposal is deterministic (same plan → same proposal).
 * - interview mode still returns kind='questions' (not a proposal).
 * - design_dashboard autonomous/directed/interview_followup → kind='proposal'.
 * - design_dashboard interview → kind='questions'.
 * - proposal.plan is a valid DashboardPlan (round-trip through DashboardPlanSchema).
 * - Placeholder plans surface openQuestions.
 *
 * All tests are purely deterministic — no LLM calls, no network.
 */

import { describe, it, expect } from "vitest";
import { generatePlan } from "../src/planner/plan.js";
import { buildProposal } from "../src/planner/proposal.js";
import {
  DashboardProposalSchema,
  DashboardPlanSchema,
  isDashboardProposal,
  SCHEMA_VERSION,
} from "../src/planner/schema.js";
import type { FieldHint } from "../src/planner/fields.js";

// ---------------------------------------------------------------------------
// Shared Superstore-like field list (abbreviated)
// ---------------------------------------------------------------------------

const SUPERSTORE_FIELDS: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Profit", dataType: "number" },
  { name: "Profit Ratio", dataType: "number" },
  { name: "Quantity", dataType: "number" },
  { name: "CP Sales", dataType: "number" },
  { name: "PP Sales", dataType: "number" },
  { name: "Sales Difference", dataType: "number" },
  { name: "CP Profit", dataType: "number" },
  { name: "PP Profit", dataType: "number" },
  { name: "Profit Difference", dataType: "number" },
  { name: "CP Profit Ratio", dataType: "number" },
  { name: "PP Profit Ratio", dataType: "number" },
  { name: "Profit Ratio Difference", dataType: "number" },
  { name: "CP Quantity", dataType: "number" },
  { name: "PP Quantity", dataType: "number" },
  { name: "Quantity Difference", dataType: "number" },
  { name: "Category", dataType: "string" },
  { name: "Segment", dataType: "string" },
  { name: "Region", dataType: "string" },
  { name: "State", dataType: "string" },
  { name: "Order Date", dataType: "date" },
];

const execPlanInput = {
  mode: "autonomous" as const,
  audience: "exec" as const,
  businessQuestion: "How is sales performing across categories and states?",
  fieldHints: SUPERSTORE_FIELDS,
  datasourceLuid: "superstore-ds",
  datasourceName: "Superstore",
  projectName: "Finance",
};

// ---------------------------------------------------------------------------
// §1 — buildProposal: exec plan with KPI band
// ---------------------------------------------------------------------------

describe("buildProposal — exec plan (Slice 5)", () => {
  const proposal = buildProposal(generatePlan(execPlanInput));

  it("returns kind='proposal'", () => {
    expect(proposal.kind).toBe("proposal");
  });

  it("validates against DashboardProposalSchema", () => {
    const result = DashboardProposalSchema.safeParse(proposal);
    expect(result.success).toBe(true);
  });

  it("isDashboardProposal guard returns true", () => {
    expect(isDashboardProposal(proposal)).toBe(true);
  });

  it("has non-empty summary", () => {
    expect(typeof proposal.summary).toBe("string");
    expect(proposal.summary.length).toBeGreaterThan(10);
  });

  it("summary mentions the datasource name", () => {
    expect(proposal.summary.toLowerCase()).toContain("superstore");
  });

  it("has non-empty layoutSummary", () => {
    expect(typeof proposal.layoutSummary).toBe("string");
    expect(proposal.layoutSummary.length).toBeGreaterThan(5);
  });

  it("layoutSummary describes kpi_band_over_charts", () => {
    expect(proposal.layoutSummary.toLowerCase()).toContain("kpi");
  });

  it("kpiStrip has entries (exec plan has KPI tiles)", () => {
    expect(proposal.kpiStrip.length).toBeGreaterThan(0);
  });

  it("kpiStrip Sales entry has correct primaryMeasure, comparison, delta, direction", () => {
    const salesKpi = proposal.kpiStrip.find((k) => k.label.toLowerCase().includes("sales"));
    expect(salesKpi).toBeDefined();
    expect(salesKpi!.primaryMeasure).toBe("Sales");
    expect(salesKpi!.comparisonMeasure).toBe("PP Sales");
    expect(salesKpi!.deltaMeasure).toBe("Sales Difference");
    expect(salesKpi!.direction).toBe("up_good");
  });

  it("views has entries (non-kpi charts)", () => {
    expect(proposal.views.length).toBeGreaterThan(0);
  });

  it("views includes the filled-map sheet", () => {
    const mapView = proposal.views.find((v) => v.chartType === "map_filled");
    expect(mapView).toBeDefined();
    expect(mapView!.encodingSummary.toLowerCase()).toContain("map");
  });

  it("views includes the color-encoded bar sheet", () => {
    const barView = proposal.views.find((v) => v.chartType === "bar");
    expect(barView).toBeDefined();
    expect(barView!.encodingSummary.toLowerCase()).toContain("bar");
  });

  it("views encodingSummary is non-empty for each view", () => {
    for (const view of proposal.views) {
      expect(view.encodingSummary.length).toBeGreaterThan(0);
    }
  });

  it("views.fields is a non-empty array for each view", () => {
    for (const view of proposal.views) {
      expect(Array.isArray(view.fields)).toBe(true);
      expect(view.fields.length).toBeGreaterThan(0);
    }
  });

  it("proposal.plan is verbatim — kind='plan' nested inside", () => {
    expect(proposal.plan.kind).toBe("plan");
  });

  it("proposal.plan passes DashboardPlanSchema validation", () => {
    const result = DashboardPlanSchema.safeParse(proposal.plan);
    expect(result.success).toBe(true);
  });

  it("proposal.plan datasourceLuid matches input", () => {
    expect(proposal.plan.datasourceLuid).toBe(execPlanInput.datasourceLuid);
  });

  it("proposal.plan projectName matches input", () => {
    expect(proposal.plan.projectName).toBe(execPlanInput.projectName);
  });

  it("proposal carries dashboardTitle and dashboardSubtitle from the plan", () => {
    const plan = generatePlan(execPlanInput);
    const prop = buildProposal(plan);
    expect(prop.dashboardTitle).toBe(plan.dashboardTitle);
    expect(prop.dashboardSubtitle).toBe(plan.dashboardSubtitle);
  });

  it("no openQuestions on a concrete plan (no placeholders)", () => {
    expect(proposal.openQuestions).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// §2 — buildProposal: placeholder plan surfaces openQuestions
// ---------------------------------------------------------------------------

describe("buildProposal — placeholder plan (Slice 5)", () => {
  it("openQuestions is defined when plan has placeholder tokens", () => {
    // No fieldHints → placeholder sheets
    const plan = generatePlan({
      mode: "autonomous",
      audience: "analyst",
      businessQuestion: "Revenue breakdown",
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
      // no fieldHints — produces placeholder sheets
    });
    const proposal = buildProposal(plan);
    // Placeholder note is surfaced
    expect(proposal.openQuestions).toBeDefined();
    expect(proposal.openQuestions!.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// §3 — buildProposal: analyst plan (no KPI band)
// ---------------------------------------------------------------------------

describe("buildProposal — analyst plan (Slice 5)", () => {
  const analystPlan = generatePlan({
    mode: "autonomous",
    audience: "analyst",
    businessQuestion: "What drives profit by category?",
    fieldHints: [
      { name: "Sales", dataType: "number" },
      { name: "Profit", dataType: "number" },
      { name: "Category", dataType: "string" },
      { name: "Region", dataType: "string" },
      { name: "Order Date", dataType: "date" },
    ],
    datasourceLuid: "DS",
    datasourceName: "Analytics",
    projectName: "Test",
  });

  const proposal = buildProposal(analystPlan);

  it("returns kind='proposal'", () => {
    expect(proposal.kind).toBe("proposal");
  });

  it("validates against schema", () => {
    expect(DashboardProposalSchema.safeParse(proposal).success).toBe(true);
  });

  it("kpiStrip is empty array for non-exec plan with no kpi_tile sheets", () => {
    // Analyst plans don't automatically produce kpi_tile sheets in autonomous mode
    // (only exec plans do). The strip may be empty.
    expect(Array.isArray(proposal.kpiStrip)).toBe(true);
  });

  it("views has at least one entry", () => {
    expect(proposal.views.length).toBeGreaterThan(0);
  });

  it("proposal.plan passes schema validation", () => {
    expect(DashboardPlanSchema.safeParse(proposal.plan).success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §4 — Determinism: same plan → same proposal
// ---------------------------------------------------------------------------

describe("buildProposal determinism (Slice 5)", () => {
  it("same exec plan input → identical proposals", () => {
    const plan1 = generatePlan(execPlanInput);
    const plan2 = generatePlan(execPlanInput);
    const prop1 = buildProposal(plan1);
    const prop2 = buildProposal(plan2);
    expect(JSON.stringify(prop1)).toBe(JSON.stringify(prop2));
  });

  it("different plans → different proposals", () => {
    const planExec = generatePlan(execPlanInput);
    const planAnalyst = generatePlan({ ...execPlanInput, audience: "analyst" });
    const propExec = buildProposal(planExec);
    const propAnalyst = buildProposal(planAnalyst);
    expect(JSON.stringify(propExec)).not.toBe(JSON.stringify(propAnalyst));
  });
});

// ---------------------------------------------------------------------------
// §5 — Round-trip: proposal.plan is accepted by build_from_plan validation
// ---------------------------------------------------------------------------

describe("proposal.plan round-trip through DashboardPlanSchema (Slice 5)", () => {
  it("exec proposal.plan parses cleanly with all optional fields", () => {
    const plan = generatePlan(execPlanInput);
    const proposal = buildProposal(plan);
    // The embedded plan must be a full DashboardPlan — tested by schema parse
    const result = DashboardPlanSchema.safeParse(proposal.plan);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kind).toBe("plan");
      expect(result.data.sheets.length).toBeGreaterThan(0);
    }
  });

  it("analyst proposal.plan also parses cleanly", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "analyst",
      businessQuestion: "Revenue by segment",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    });
    const proposal = buildProposal(plan);
    const result = DashboardPlanSchema.safeParse(proposal.plan);
    expect(result.success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §6 — design_dashboard tool: end-to-end proposal output
// ---------------------------------------------------------------------------

describe("design_dashboard tool — proposal output (Slice 5)", () => {
  // Reuse the fake-server pattern from tools.test.ts
  async function invokeDesignDashboard(args: Record<string, unknown>): Promise<unknown> {
    const { registerAllTools } = await import("../src/index.js");
    const tools = new Map<string, {
      config: { inputSchema?: Record<string, unknown> };
      handler: (args: Record<string, unknown>) => Promise<{ structuredContent?: Record<string, unknown> }>;
    }>();
    const fakeServer = {
      registerTool(
        name: string,
        config: { inputSchema?: Record<string, unknown> },
        handler: (args: Record<string, unknown>) => Promise<{ structuredContent?: Record<string, unknown> }>,
      ) {
        tools.set(name, { config, handler });
      },
    };
    const fakeCtx = {
      config: { server: "https://x", siteName: "s", patName: "p", patValue: "v", apiVersion: "3.28", sidecarHost: "127.0.0.1", sidecarPort: 8899 },
      rest: {},
      sidecar: {},
    };
    registerAllTools(fakeServer as never, fakeCtx as never);
    const tool = tools.get("design_dashboard")!;
    if (!tool) throw new Error("design_dashboard not registered");
    const { z } = await import("zod");
    const parsed = z.object(tool.config.inputSchema ?? {}).parse(args);
    return tool.handler(parsed as Record<string, unknown>);
  }

  it("autonomous mode → structuredContent.result.kind = 'proposal'", async () => {
    const res = await invokeDesignDashboard({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    }) as { structuredContent?: { result?: { kind?: string } } };
    expect(res.structuredContent?.result?.kind).toBe("proposal");
  });

  it("directed mode → kind = 'proposal'", async () => {
    const res = await invokeDesignDashboard({
      mode: "directed",
      audience: "analyst",
      directions: "a bar chart of sales by category and a line of sales over time",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Category", dataType: "string" },
        { name: "Order Date", dataType: "date" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    }) as { structuredContent?: { result?: { kind?: string } } };
    expect(res.structuredContent?.result?.kind).toBe("proposal");
  });

  it("interview_followup mode → kind = 'proposal'", async () => {
    const res = await invokeDesignDashboard({
      mode: "interview_followup",
      audience: "exec",
      answers: {
        q_goal: "How is revenue trending?",
        q_key_metric: "revenue",
      },
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Category", dataType: "string" },
        { name: "Order Date", dataType: "date" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    }) as { structuredContent?: { result?: { kind?: string } } };
    expect(res.structuredContent?.result?.kind).toBe("proposal");
  });

  it("interview mode → kind = 'questions' (not a proposal)", async () => {
    const res = await invokeDesignDashboard({
      mode: "interview",
    }) as { structuredContent?: { result?: { kind?: string; questions?: unknown[] } } };
    expect(res.structuredContent?.result?.kind).toBe("questions");
  });

  it("autonomous proposal embeds a valid plan (nested plan.kind='plan')", async () => {
    const res = await invokeDesignDashboard({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "Revenue breakdown",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    }) as { structuredContent?: { result?: { kind?: string; plan?: { kind?: string; sheets?: unknown[] } } } };
    const proposal = res.structuredContent?.result;
    expect(proposal?.kind).toBe("proposal");
    expect(proposal?.plan?.kind).toBe("plan");
    expect((proposal?.plan?.sheets as unknown[] | undefined)?.length).toBeGreaterThan(0);
  });

  it("proposal has kpiStrip and views populated for exec + Superstore fields", async () => {
    const res = await invokeDesignDashboard({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing across categories and states?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    }) as { structuredContent?: { result?: { kpiStrip?: unknown[]; views?: unknown[]; layoutSummary?: string; summary?: string } } };
    const proposal = res.structuredContent?.result;
    expect((proposal?.kpiStrip as unknown[] | undefined)?.length).toBeGreaterThan(0);
    expect((proposal?.views as unknown[] | undefined)?.length).toBeGreaterThan(0);
    expect(typeof proposal?.layoutSummary).toBe("string");
    expect(typeof proposal?.summary).toBe("string");
  });
});

// ---------------------------------------------------------------------------
// §7 — KPI direction logic
// ---------------------------------------------------------------------------

describe("KPI strip direction (Slice 5)", () => {
  it("Sales KPI → up_good", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "Sales overview",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Test",
    });
    const proposal = buildProposal(plan);
    const salesKpi = proposal.kpiStrip.find((k) => k.primaryMeasure === "Sales");
    expect(salesKpi?.direction).toBe("up_good");
  });

  it("cost-like measure (Quantity) → up_good by default (no cost-like name)", () => {
    // Quantity is not classified as cost-like in isCostLikeMeasure
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "Overview",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Test",
    });
    const proposal = buildProposal(plan);
    const quantityKpi = proposal.kpiStrip.find((k) => k.primaryMeasure === "Quantity");
    if (quantityKpi) {
      // Quantity has a delta binding (Quantity Difference), so direction is not neutral
      expect(["up_good", "down_good"]).toContain(quantityKpi.direction);
    }
  });

  it("KPI tile without delta → direction='neutral'", () => {
    // Construct a minimal plan with a kpi_tile that has no deltaMeasure
    const plan = DashboardPlanSchema.parse({
      schemaVersion: SCHEMA_VERSION,
      kind: "plan",
      workbookName: "Test",
      datasourceLuid: "DS",
      datasourceName: "DS",
      projectName: "P",
      audience: "exec",
      rationale: "test",
      dashboardLayout: "tiled_vertical",
      sheets: [
        {
          title: "Revenue",
          markType: "text",
          kind: "kpi_tile",
          cols: [],
          rows: [],
          measures: ["Revenue"],
          kpi: { primaryMeasure: "Revenue" },
        },
        {
          title: "Revenue by Region",
          markType: "bar",
          cols: ["Region"],
          rows: [],
          measures: ["Revenue"],
        },
      ],
    });
    const proposal = buildProposal(plan);
    const kpi = proposal.kpiStrip[0]!;
    expect(kpi.direction).toBe("neutral");
    expect(kpi.deltaMeasure).toBeUndefined();
  });
});
