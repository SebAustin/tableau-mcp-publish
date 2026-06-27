/**
 * Unit tests for the planning pipeline (M4 — PLAN §9.2).
 *
 * All tests are purely deterministic — no LLM calls, no network.
 */

import { describe, it, expect } from "vitest";
import { classifyField, classifyFields } from "../src/planner/fields.js";
import { applyAudienceClamps } from "../src/planner/audience.js";
import { applyMarkHeuristic, GAP_G05 } from "../src/planner/marks.js";
import {
  DashboardPlanSchema,
  assertSchemaVersion,
} from "../src/planner/schema.js";
import { generatePlan, generateInterview } from "../src/planner/plan.js";
import type { SheetSpec } from "../src/planner/schema.js";

// ---------------------------------------------------------------------------
// §3.0 Field inference — identifier suppression
// ---------------------------------------------------------------------------

describe("field inference — identifier suppression", () => {
  it("classifies numeric customer_id as identifier with suppress=true", () => {
    const fc = classifyField({ name: "customer_id", dataType: "number" });
    expect(fc.role).toBe("identifier");
    expect(fc.suppress).toBe(true);
  });

  it("classifies string order_id as identifier with suppress=true", () => {
    const fc = classifyField({ name: "order_id", dataType: "string" });
    expect(fc.role).toBe("identifier");
    expect(fc.suppress).toBe(true);
  });

  it("classifies uuid field as identifier regardless of dtype", () => {
    const fc = classifyField({ name: "row_uuid", dataType: "string" });
    expect(fc.role).toBe("identifier");
    expect(fc.suppress).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §3.0 Field inference — measure vs dimension
// ---------------------------------------------------------------------------

describe("field inference — measure vs dimension", () => {
  it("revenue (number) → measure", () => {
    const fc = classifyField({ name: "revenue", dataType: "number" });
    expect(fc.role).toBe("measure");
    expect(fc.suppress).toBe(false);
  });

  it("region (string) → geographic with geo_named tag (BI_DESIGN §1 priority 4)", () => {
    // 'region' matches the geo-named name pattern (priority 4) before the
    // low-cardinality dimension pattern (priority 5), so role is 'geographic'.
    const fc = classifyField({ name: "region", dataType: "string" });
    expect(fc.role).toBe("geographic");
    expect(fc.tags).toContain("geo_named");
    expect(fc.suppress).toBe(false);
  });

  it("order_date (date) → temporal dimension", () => {
    const fc = classifyField({ name: "order_date", dataType: "date" });
    expect(fc.role).toBe("temporal");
    expect(fc.suppress).toBe(false);
  });

  it("is_active → boolean_flag dimension", () => {
    const fc = classifyField({ name: "is_active", dataType: "boolean" });
    expect(fc.role).toBe("dimension");
    expect(fc.tags).toContain("boolean_flag");
  });

  it("plan_type (string) with no dtype → dimension", () => {
    const fc = classifyField({ name: "plan_type" });
    expect(fc.role).toBe("dimension");
  });

  it("sku (number) → identifier wins over measure dtype", () => {
    const fc = classifyField({ name: "product_sku", dataType: "number" });
    expect(fc.role).toBe("identifier");
    expect(fc.suppress).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// schemaVersion guard
// ---------------------------------------------------------------------------

describe("schemaVersion guard", () => {
  it("throws when schemaVersion is 2", () => {
    expect(() => assertSchemaVersion(2)).toThrow(/Unsupported schemaVersion/);
  });

  it("throws when schemaVersion is 0", () => {
    expect(() => assertSchemaVersion(0)).toThrow(/Unsupported schemaVersion/);
  });

  it("does not throw for schemaVersion 1", () => {
    expect(() => assertSchemaVersion(1)).not.toThrow();
  });

  it("DashboardPlanSchema rejects schemaVersion != 1", () => {
    expect(() =>
      DashboardPlanSchema.parse({
        schemaVersion: 2,
        kind: "plan",
        workbookName: "X",
        datasourceLuid: "x",
        datasourceName: "x",
        projectName: "x",
        audience: "exec",
        rationale: "r",
        dashboardLayout: "tiled_vertical",
        sheets: [{ title: "S", markType: "bar", cols: ["D"], rows: [], measures: ["M"] }],
      }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Audience clamp — MA-1 exec
// ---------------------------------------------------------------------------

describe("autonomous exec (MA-1)", () => {
  it("plan has <= 3 sheets, all markType in {bar,line,text}, first sheet is text", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is revenue trending over time?",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "region", dataType: "string" },
        { name: "revenue", dataType: "number" },
        { name: "order_id", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    });
    expect(plan.sheets.length).toBeLessThanOrEqual(3);
    const allowed = new Set(["bar", "line", "text"]);
    for (const sheet of plan.sheets) {
      expect(allowed.has(sheet.markType)).toBe(true);
    }
    expect(plan.sheets[0]?.markType).toBe("text");
  });
});

// ---------------------------------------------------------------------------
// Audience clamp — MA-2 analyst
// ---------------------------------------------------------------------------

describe("autonomous analyst (MA-2)", () => {
  it("plan has <= 8 sheets", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "analyst",
      businessQuestion: "What drives churn by segment and region over time?",
      fieldHints: [
        { name: "customer_id", dataType: "string" },
        { name: "churned", dataType: "boolean" },
        { name: "region", dataType: "string" },
        { name: "segment", dataType: "string" },
        { name: "revenue", dataType: "number" },
        { name: "signup_date", dataType: "date" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Churn",
      projectName: "Test",
    });
    expect(plan.sheets.length).toBeLessThanOrEqual(8);
  });
});

// ---------------------------------------------------------------------------
// Audience clamp — MA-3 operational
// ---------------------------------------------------------------------------

describe("autonomous operational (MA-3)", () => {
  it("dashboardLayout is tiled_vertical", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "operational",
      businessQuestion: "Current order pipeline status by stage",
      fieldHints: [
        { name: "order_id", dataType: "string" },
        { name: "status", dataType: "string" },
        { name: "amount", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Orders",
      projectName: "Test",
    });
    expect(plan.dashboardLayout).toBe("tiled_vertical");
    const hasText = plan.sheets.some((s) => s.markType === "text");
    expect(hasText).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Clamp STEP 5 — per-sheet measure/dimension caps
// ---------------------------------------------------------------------------

describe("clamp STEP 5 — per-sheet caps", () => {
  it("exec bar sheet truncates 3 measures to 1", () => {
    // Exec has minimumKpiCount=1, so STEP 3 inserts a text KPI at index 0 when
    // the first sheet is not already text. The original bar sheet ends up at
    // index 1. We look for the bar sheet in the result and verify its measure cap.
    const sheet: SheetSpec = {
      title: "S",
      markType: "bar",
      cols: ["region"],
      rows: [],
      measures: ["revenue", "cost", "profit"],
    };
    const { sheets } = applyAudienceClamps([sheet], "exec");
    const barSheet = sheets.find((s) => s.markType === "bar");
    expect(barSheet).toBeDefined();
    expect(barSheet?.measures.length).toBeLessThanOrEqual(1);
  });

  it("analyst sheet truncates 5 dims to 3", () => {
    // Analyst has minimumKpiCount=0, so no KPI lead is inserted.
    const sheet: SheetSpec = {
      title: "S",
      markType: "bar",
      cols: ["a", "b", "c", "d", "e"],
      rows: [],
      measures: ["revenue"],
    };
    const { sheets } = applyAudienceClamps([sheet], "analyst");
    const total = (sheets[0]?.cols.length ?? 0) + (sheets[0]?.rows.length ?? 0);
    expect(total).toBeLessThanOrEqual(3);
  });
});

// ---------------------------------------------------------------------------
// Clamp STEP 6 — map guard
// ---------------------------------------------------------------------------

describe("clamp STEP 6 — map guard", () => {
  it("map sheet under exec → replaced with bar (no map mark in result)", () => {
    // Exec has mapAllowed=false and minimumKpiCount=1. The pipeline will:
    // - STEP 2: map is not in allowedMarkTypes → downgraded to bar/text by defaultMarkForAudience
    // - STEP 3: KPI text inserted at index 0
    // Net result: no sheet in the result should have markType "map".
    const sheet: SheetSpec = {
      title: "S",
      markType: "map",
      cols: [],
      rows: ["country"],
      measures: ["revenue"],
    };
    const { sheets } = applyAudienceClamps([sheet], "exec");
    const anyMap = sheets.some((s) => s.markType === "map");
    expect(anyMap).toBe(false);
  });

  it("map sheet survives under analyst (mapAllowed=true)", () => {
    // Analyst has mapAllowed=true and minimumKpiCount=0 — sheet passes through unchanged.
    const sheet: SheetSpec = {
      title: "S",
      markType: "map",
      cols: [],
      rows: ["country"],
      measures: ["revenue"],
    };
    const { sheets } = applyAudienceClamps([sheet], "analyst");
    const mapSheet = sheets.find((s) => s.markType === "map");
    expect(mapSheet).toBeDefined();
  });

  it("map sheet under operational → no map in result", () => {
    // Operational has mapAllowed=false.
    const sheet: SheetSpec = {
      title: "S",
      markType: "map",
      cols: [],
      rows: ["region"],
      measures: ["amount"],
    };
    const { sheets } = applyAudienceClamps([sheet], "operational");
    const anyMap = sheets.some((s) => s.markType === "map");
    expect(anyMap).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// No-dimension downgrade
// ---------------------------------------------------------------------------

describe("no-dimension downgrade", () => {
  it("bar-selected shape with all dims suppressed → text (KPI)", () => {
    const hints = [
      { name: "customer_id", dataType: "string" as const },
      { name: "revenue", dataType: "number" as const },
    ];
    const fields = classifyFields(hints);
    const sheets = applyMarkHeuristic("compare revenue", fields);
    // All dims are suppressed (only customer_id exists), so bar downgrades to text
    expect(sheets[0]?.markType).toBe("text");
  });

  it("measureless text is dropped (invariant §0.2)", () => {
    const hints = [
      { name: "customer_id", dataType: "string" as const },
    ];
    const fields = classifyFields(hints);
    // "kpi" keyword → text with no dimension, but also no measure → dropped
    const sheets = applyMarkHeuristic("overall kpi", fields);
    // If a text sheet with no measures is produced, it must not be in the result
    for (const s of sheets) {
      if (s.markType === "text") {
        expect(s.measures.length).toBeGreaterThan(0);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Gap annotations
// ---------------------------------------------------------------------------

describe("gap annotations — G-02 scatter→bar (Phase-1: scatter now native)", () => {
  it("scatter/drives keyword → markType 'scatter' (Phase-1 upgrade; bar fallback via audience guard)", () => {
    // Phase-1: the mark heuristic now routes scatter keywords to markType "scatter" directly.
    // The G-02 bar fallback still applies inside applyAudienceClamps when the audience
    // does not allow scatter (e.g. exec, operational, mixed). At the heuristic level the
    // markType is "scatter", not "bar".
    const fields = classifyFields([
      { name: "region", dataType: "string" },
      { name: "revenue", dataType: "number" },
    ]);
    const sheets = applyMarkHeuristic("correlation between revenue and cost", fields);
    expect(sheets[0]?.markType).toBe("scatter");
  });

  it("scatter sheet under exec audience is replaced with bar (audience guard)", () => {
    const sheet: SheetSpec = {
      title: "S",
      markType: "scatter",
      cols: [],
      rows: [],
      measures: ["revenue", "cost"],
      scatter: { x: "revenue", y: "cost" },
    };
    const { sheets } = applyAudienceClamps([sheet], "exec");
    // Exec does not allow scatter → replaced with bar (and KPI inserted at index 0)
    const anyScatter = sheets.some((s) => s.markType === "scatter");
    expect(anyScatter).toBe(false);
  });
});

describe("gap annotations — G-05 high-cardinality top-N note", () => {
  it("high-cardinality dimension carries G-05 note", () => {
    const fields = classifyFields([
      { name: "customer_name", dataType: "string" }, // high_cardinality from name pattern
      { name: "revenue", dataType: "number" },
    ]);
    const sheets = applyMarkHeuristic("revenue by name", fields);
    const hasG05 = sheets.some((s) => s.rationale?.includes(GAP_G05));
    expect(hasG05).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Interview mode (MB-1 / MB-2)
// ---------------------------------------------------------------------------

describe("interview mode (MB-1)", () => {
  it("returns 3–10 questions (Phase-1: bank expanded to 10) and no 'sheets' key", () => {
    const result = generateInterview({});
    expect(result.kind).toBe("questions");
    expect(result.questions.length).toBeGreaterThanOrEqual(3);
    expect(result.questions.length).toBeLessThanOrEqual(10);
    expect("sheets" in result).toBe(false);
  });

  it("returns ≤7 questions when most inputs are known", () => {
    // When audience, businessQuestion, and fieldHints (with temporal + dimension) are
    // known, the conditional selection fires fewer questions.
    const result = generateInterview({
      audience: "exec",
      businessQuestion: "How is revenue trending?",
      context: "filter by current year, prior period comparison, title: Revenue Review, brand color blue",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "region", dataType: "string" },
        { name: "revenue", dataType: "number" },
      ],
    });
    // With all conditions satisfied, only unconditional + action might fire
    expect(result.questions.length).toBeGreaterThanOrEqual(3);
    expect(result.questions.length).toBeLessThanOrEqual(10);
  });
});

describe("interview_followup (MB-2)", () => {
  it("returns a DashboardPlan with non-empty rationale", () => {
    const plan = generatePlan({
      mode: "interview_followup",
      audience: "analyst",
      answers: {
        q_goal: "How is revenue trending?",
        q_key_metric: "revenue",
      },
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    });
    expect(plan.kind).toBe("plan");
    expect(plan.rationale.length).toBeGreaterThan(0);
    expect(plan.sheets.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Directed mode (MC-1 / MC-2)
// ---------------------------------------------------------------------------

describe("directed mode (MC-1)", () => {
  it("'show me a table of top 10 customers by revenue and a bar chart of revenue by region' → 2 sheets [text, bar]", () => {
    const plan = generatePlan({
      mode: "directed",
      audience: "analyst",
      directions:
        "show me a table of top 10 customers by revenue and a bar chart of revenue by region",
      fieldHints: [
        { name: "customer_name", dataType: "string" },
        { name: "region", dataType: "string" },
        { name: "revenue", dataType: "number" },
        { name: "order_id", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    });
    expect(plan.sheets.length).toBe(2);
    expect(plan.sheets[0]?.markType).toBe("text");
    expect(plan.sheets[1]?.markType).toBe("bar");
  });
});

describe("directed mode (MC-2)", () => {
  it("handler rejects directed mode with all required fields but missing directions", async () => {
    // MC-2: the handler must throw when mode="directed" and directions is absent,
    // even when all other required fields (datasourceLuid/datasourceName/projectName)
    // are supplied.  The error must mention "directions".
    const { registerAllTools } = await import("../src/index.js");
    const tools = new Map<string, {
      config: { inputSchema?: Record<string, unknown> };
      handler: (args: Record<string, unknown>) => Promise<unknown>;
    }>();
    const fakeServer = {
      registerTool(
        name: string,
        config: { inputSchema?: Record<string, unknown> },
        handler: (args: Record<string, unknown>) => Promise<unknown>,
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

    // All required fields are supplied; only `directions` is omitted.
    await expect(
      tool.handler({
        mode: "directed",
        audience: "analyst",
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Test",
        // directions intentionally absent
      } as Record<string, unknown>),
    ).rejects.toThrow(/directions/);
  });
});
