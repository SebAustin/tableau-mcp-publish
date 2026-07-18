/**
 * Unit tests for Slice 2 — schema growth (Phase 1 §2).
 *
 * Verifies that:
 *  1. Existing minimal {title, markType, rows, cols, measures} sheets still parse
 *     (backward-compatibility guard).
 *  2. New optional SheetSpec fields (kind / color / kpi / scatter / geo) parse
 *     correctly.
 *  3. New optional DashboardPlan fields (dashboardTitle / dashboardSubtitle /
 *     textZones / layoutGrammar) parse correctly.
 *  4. DashboardProposalSchema parses a complete proposal payload.
 *  5. isDashboardProposal type-guard correctly discriminates.
 *  6. isDashboardPlan type-guard is unaffected.
 *  7. New MarkTypeEnum values ("scatter", "map_filled") are accepted.
 *  8. ClarifyingQuestions max raised to 10.
 *
 * All tests are purely deterministic — no LLM calls, no network.
 */

import { describe, it, expect } from "vitest";
import {
  SheetSpecSchema,
  DashboardPlanSchema,
  DashboardProposalSchema,
  ClarifyingQuestionsSchema,
  DesignThemeSchema,
  MarkTypeEnum,
  SheetKindEnum,
  isDashboardPlan,
  isDashboardProposal,
  SCHEMA_VERSION,
} from "../src/planner/schema.js";

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

/** Minimal old-style sheet — must still parse unchanged (backward-compat). */
const MINIMAL_SHEET = {
  title: "Revenue by Region",
  markType: "bar",
  rows: ["Region"],
  cols: [],
  measures: ["Sales"],
};

/** Minimal valid DashboardPlan (old-style, no new fields). */
const MINIMAL_PLAN = {
  schemaVersion: SCHEMA_VERSION,
  kind: "plan" as const,
  workbookName: "My Workbook",
  datasourceLuid: "abc-123",
  datasourceName: "Superstore",
  projectName: "Finance",
  audience: "exec" as const,
  rationale: "Show KPIs for Q1",
  dashboardLayout: "tiled_vertical" as const,
  sheets: [MINIMAL_SHEET],
};

// ---------------------------------------------------------------------------
// §1 — Backward-compatibility: minimal sheet still parses
// ---------------------------------------------------------------------------

describe("backward-compatibility — minimal sheet", () => {
  it("parses a plain {title, markType, rows, cols, measures} sheet unchanged", () => {
    const result = SheetSpecSchema.safeParse(MINIMAL_SHEET);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.title).toBe("Revenue by Region");
      expect(result.data.markType).toBe("bar");
      expect(result.data.kind).toBeUndefined();
      expect(result.data.color).toBeUndefined();
      expect(result.data.kpi).toBeUndefined();
      expect(result.data.scatter).toBeUndefined();
      expect(result.data.geo).toBeUndefined();
    }
  });

  it("parses a minimal DashboardPlan without any new fields", () => {
    const result = DashboardPlanSchema.safeParse(MINIMAL_PLAN);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.dashboardTitle).toBeUndefined();
      expect(result.data.dashboardSubtitle).toBeUndefined();
      expect(result.data.textZones).toBeUndefined();
      expect(result.data.layoutGrammar).toBeUndefined();
    }
  });
});

// ---------------------------------------------------------------------------
// §2 — New MarkTypeEnum values
// ---------------------------------------------------------------------------

describe("MarkTypeEnum extensions", () => {
  it('accepts "scatter"', () => {
    const result = MarkTypeEnum.safeParse("scatter");
    expect(result.success).toBe(true);
  });

  it('accepts "map_filled"', () => {
    const result = MarkTypeEnum.safeParse("map_filled");
    expect(result.success).toBe(true);
  });

  it('still accepts legacy values "bar", "line", "text", "map"', () => {
    for (const v of ["bar", "line", "text", "map"] as const) {
      expect(MarkTypeEnum.safeParse(v).success).toBe(true);
    }
  });

  it('rejects unknown "heatmap"', () => {
    expect(MarkTypeEnum.safeParse("heatmap").success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §3 — SheetKindEnum
// ---------------------------------------------------------------------------

describe("SheetKindEnum", () => {
  it('accepts "chart" and "kpi_tile"', () => {
    expect(SheetKindEnum.safeParse("chart").success).toBe(true);
    expect(SheetKindEnum.safeParse("kpi_tile").success).toBe(true);
  });

  it('rejects unknown "sparkline"', () => {
    expect(SheetKindEnum.safeParse("sparkline").success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §4 — SheetSpec with optional encoding blocks
// ---------------------------------------------------------------------------

describe("SheetSpec — optional color block", () => {
  it("parses a sheet with a dimension color encoding", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      color: { field: "Category", kind: "dimension" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.color?.field).toBe("Category");
      expect(result.data.color?.kind).toBe("dimension");
    }
  });

  it("parses a sheet with a measure color encoding", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      markType: "map_filled",
      color: { field: "Profit", kind: "measure" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.color?.kind).toBe("measure");
    }
  });

  it("parses a sheet with measure_names color kind", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      color: { field: "Measure Names", kind: "measure_names" },
    });
    expect(result.success).toBe(true);
  });

  it("rejects color with an invalid kind", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      color: { field: "Category", kind: "categorical" },
    });
    expect(result.success).toBe(false);
  });
});

describe("SheetSpec — optional kpi block", () => {
  it("parses a kpi_tile sheet with a full kpi block", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Sales KPI",
      markType: "text",
      rows: [],
      cols: [],
      measures: ["CP Sales"],
      kind: "kpi_tile",
      kpi: {
        primaryMeasure: "CP Sales",
        comparisonMeasure: "PP Sales",
        deltaMeasure: "Sales Difference",
        deltaIsPositiveGood: true,
        sparklineField: "Order Date",
        valuePrefix: "$",
        valueSuffix: "K",
      },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kind).toBe("kpi_tile");
      expect(result.data.kpi?.primaryMeasure).toBe("CP Sales");
      expect(result.data.kpi?.comparisonMeasure).toBe("PP Sales");
      expect(result.data.kpi?.deltaIsPositiveGood).toBe(true);
      expect(result.data.kpi?.valuePrefix).toBe("$");
    }
  });

  it("parses a kpi_tile sheet with only primaryMeasure", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Profit KPI",
      markType: "text",
      rows: [],
      cols: [],
      measures: ["Profit"],
      kind: "kpi_tile",
      kpi: { primaryMeasure: "Profit" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kpi?.comparisonMeasure).toBeUndefined();
    }
  });
});

describe("SheetSpec — optional scatter block", () => {
  it("parses a scatter sheet with x, y, and breakdown", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Sales vs Profit",
      markType: "scatter",
      rows: ["Sales"],
      cols: ["Profit"],
      measures: ["Sales", "Profit"],
      scatter: { x: "Sales", y: "Profit", breakdown: "Category" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.scatter?.x).toBe("Sales");
      expect(result.data.scatter?.breakdown).toBe("Category");
    }
  });

  it("parses a scatter sheet with only x and y (no breakdown)", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Qty vs Discount",
      markType: "scatter",
      rows: ["Quantity"],
      cols: ["Discount"],
      measures: ["Quantity", "Discount"],
      scatter: { x: "Quantity", y: "Discount" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.scatter?.breakdown).toBeUndefined();
    }
  });

  it("rejects scatter block missing y", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      markType: "scatter",
      scatter: { x: "Sales" },
    });
    expect(result.success).toBe(false);
  });
});

describe("SheetSpec — optional geo block", () => {
  it("parses a map_filled sheet with a geo block", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Profit by State",
      markType: "map_filled",
      rows: [],
      cols: ["State"],
      measures: ["Profit"],
      geo: { geoField: "State", geoRole: "state", colorMeasure: "Profit" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.geo?.geoRole).toBe("state");
      expect(result.data.geo?.colorMeasure).toBe("Profit");
    }
  });

  it("parses geo with geoRole country and no colorMeasure", () => {
    const result = SheetSpecSchema.safeParse({
      title: "Global Map",
      markType: "map_filled",
      rows: [],
      cols: ["Country"],
      measures: [],
      geo: { geoField: "Country", geoRole: "country" },
    });
    expect(result.success).toBe(true);
  });

  it("rejects geo with an invalid geoRole", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      markType: "map_filled",
      geo: { geoField: "State", geoRole: "province" },
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §5 — DashboardPlan with new optional dashboard-level fields
// ---------------------------------------------------------------------------

describe("DashboardPlan — new optional fields", () => {
  it("parses a plan with dashboardTitle and dashboardSubtitle", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      dashboardTitle: "Executive Overview",
      dashboardSubtitle: "Q1 2024 Performance",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.dashboardTitle).toBe("Executive Overview");
      expect(result.data.dashboardSubtitle).toBe("Q1 2024 Performance");
    }
  });

  it("parses a plan with textZones array", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      textZones: [
        { text: "Q1 2024 Executive Dashboard", position: "header" },
        { text: "Confidential — Internal use only", position: "footer" },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.textZones).toHaveLength(2);
      expect(result.data.textZones?.[0]?.position).toBe("header");
    }
  });

  it("parses a plan with layoutGrammar kpi_band_over_charts", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      layoutGrammar: {
        kind: "kpi_band_over_charts",
        kpiTileTitles: ["Sales KPI", "Profit KPI"],
        chartTitles: ["Revenue by Region", "Sales by Category"],
      },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.layoutGrammar?.kind).toBe("kpi_band_over_charts");
      expect(result.data.layoutGrammar?.kpiTileTitles).toHaveLength(2);
    }
  });

  it("rejects textZones with an invalid position value", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      textZones: [{ text: "Title", position: "sidebar" }],
    });
    expect(result.success).toBe(false);
  });

  it("rejects layoutGrammar with an unknown kind", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      layoutGrammar: { kind: "floating_grid" },
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §6 — DashboardPlan DatasourceSpec with optional encoding/delimiter
// ---------------------------------------------------------------------------

describe("DatasourceSpec — optional encoding and delimiter", () => {
  it("parses a datasourceSpec with explicit encoding and delimiter", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      datasourceSpec: {
        datasourceName: "Superstore",
        filePath: "/data/superstore.csv",
        fileType: "csv",
        encoding: "utf-16",
        delimiter: "\t",
      },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.datasourceSpec?.encoding).toBe("utf-16");
      expect(result.data.datasourceSpec?.delimiter).toBe("\t");
    }
  });
});

// ---------------------------------------------------------------------------
// §7 — DashboardProposal schema and isDashboardProposal guard
// ---------------------------------------------------------------------------

/** A complete valid proposal payload. */
const VALID_PROPOSAL = {
  schemaVersion: SCHEMA_VERSION,
  kind: "proposal" as const,
  workbookName: "Superstore Executive",
  audience: "exec" as const,
  datasourceName: "Superstore",
  projectName: "Finance",
  summary: "A KPI-strip dashboard showing Sales, Profit, and Orders for Q1 2024.",
  dashboardTitle: "Executive Overview",
  dashboardSubtitle: "Q1 2024 Performance",
  kpiStrip: [
    {
      label: "Sales",
      primaryMeasure: "CP Sales",
      comparisonMeasure: "PP Sales",
      deltaMeasure: "Sales Difference",
      direction: "up_good" as const,
    },
    {
      label: "Profit",
      primaryMeasure: "CP Profit",
      direction: "up_good" as const,
    },
  ],
  views: [
    {
      title: "Sales by Category",
      chartType: "bar",
      encodingSummary: "Sales by Category, colored by Sub-Category",
      fields: ["Category", "Sales"],
    },
    {
      title: "Profit by State",
      chartType: "map_filled",
      encodingSummary: "Profit colored by state",
      fields: ["State", "Profit"],
    },
  ],
  layoutSummary: "KPI strip at top; category bar and filled map below.",
  openQuestions: ["Should discount be included?"],
  plan: MINIMAL_PLAN,
};

describe("DashboardProposalSchema", () => {
  it("parses a complete proposal payload", () => {
    const result = DashboardProposalSchema.safeParse(VALID_PROPOSAL);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kind).toBe("proposal");
      expect(result.data.kpiStrip).toHaveLength(2);
      expect(result.data.views).toHaveLength(2);
      expect(result.data.openQuestions).toHaveLength(1);
      expect(result.data.plan.kind).toBe("plan");
    }
  });

  it("parses a proposal without optional fields (openQuestions, dashboardSubtitle)", () => {
    const { openQuestions: _openQuestions, dashboardSubtitle: _dashboardSubtitle, ...minimal } = VALID_PROPOSAL;
    const result = DashboardProposalSchema.safeParse(minimal);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.openQuestions).toBeUndefined();
      expect(result.data.dashboardSubtitle).toBeUndefined();
    }
  });

  it("rejects a proposal missing required views array", () => {
    const { views: _views, ...bad } = VALID_PROPOSAL;
    const result = DashboardProposalSchema.safeParse(bad);
    expect(result.success).toBe(false);
  });

  it('rejects a proposal with an invalid direction value in kpiStrip', () => {
    const bad = {
      ...VALID_PROPOSAL,
      kpiStrip: [
        { label: "Sales", primaryMeasure: "Sales", direction: "always_good" },
      ],
    };
    const result = DashboardProposalSchema.safeParse(bad);
    expect(result.success).toBe(false);
  });

  it("rejects a proposal with an empty views array", () => {
    const result = DashboardProposalSchema.safeParse({
      ...VALID_PROPOSAL,
      views: [],
    });
    expect(result.success).toBe(false);
  });
});

describe("isDashboardProposal type-guard", () => {
  it("returns true for a valid proposal", () => {
    expect(isDashboardProposal(VALID_PROPOSAL)).toBe(true);
  });

  it("returns false for a DashboardPlan (kind mismatch)", () => {
    expect(isDashboardProposal(MINIMAL_PLAN)).toBe(false);
  });

  it("returns false for null", () => {
    expect(isDashboardProposal(null)).toBe(false);
  });

  it("returns false for an object with wrong kind", () => {
    expect(isDashboardProposal({ kind: "report", schemaVersion: 1 })).toBe(false);
  });

  it("returns false for a structurally invalid proposal", () => {
    expect(isDashboardProposal({ kind: "proposal", schemaVersion: 1 })).toBe(false);
  });
});

describe("isDashboardPlan type-guard (regression)", () => {
  it("still returns true for a valid minimal plan", () => {
    expect(isDashboardPlan(MINIMAL_PLAN)).toBe(true);
  });

  it("returns false for a proposal (kind mismatch)", () => {
    expect(isDashboardPlan(VALID_PROPOSAL)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §8 — ClarifyingQuestions max raised to 10
// ---------------------------------------------------------------------------

describe("ClarifyingQuestions max raised to 10", () => {
  const makeQuestion = (id: string) => ({
    id,
    question: `Question ${id}?`,
  });

  it("accepts exactly 10 questions", () => {
    const result = ClarifyingQuestionsSchema.safeParse({
      schemaVersion: SCHEMA_VERSION,
      kind: "questions",
      questions: Array.from({ length: 10 }, (_, i) => makeQuestion(`q${i + 1}`)),
    });
    expect(result.success).toBe(true);
  });

  it("rejects 11 questions", () => {
    const result = ClarifyingQuestionsSchema.safeParse({
      schemaVersion: SCHEMA_VERSION,
      kind: "questions",
      questions: Array.from({ length: 11 }, (_, i) => makeQuestion(`q${i + 1}`)),
    });
    expect(result.success).toBe(false);
  });

  it("still accepts 7 questions (within old and new limits)", () => {
    const result = ClarifyingQuestionsSchema.safeParse({
      schemaVersion: SCHEMA_VERSION,
      kind: "questions",
      questions: Array.from({ length: 7 }, (_, i) => makeQuestion(`q${i + 1}`)),
    });
    expect(result.success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §9 — Design Excellence, Slice D1: designTheme (carry-only wire plumbing)
// ---------------------------------------------------------------------------

/** A complete designTheme payload exercising every optional block. */
const FULL_DESIGN_THEME = {
  name: "executive_dark",
  dashboardBackground: "#0b1f3a",
  spacing: { outerMargin: 16, gutter: 8 },
  chartCard: {
    background: "#ffffff",
    border: { color: "#d9d9d9", style: "solid", width: 1 },
    padding: 12,
    margin: 8,
    cornerRadius: 6,
  },
  kpiTile: {
    background: "#132b4d",
    border: { color: "#25406b", style: "solid", width: 1 },
    padding: 10,
    banColor: "#ffffff",
    useSemanticDeltaColors: true,
  },
  header: {
    background: "#0b1f3a",
    titleColor: "#ffffff",
    subtitleColor: "#c9d4e3",
  },
  chrome: {
    hideGridlines: true,
    hideZeroline: true,
    hideAxisTicks: true,
    showMarkLabels: true,
    datalabel: {
      fontSize: 11,
      fontWeight: "bold",
      colorMode: "auto",
      // Design Excellence, Slice D4: wire completion — the sidecar builder
      // already read this defensively via .get() since D3.
      color: "#ffffff",
    },
    // Design Excellence, Slice D4: wire completion for a field the sidecar
    // builder already read defensively via .get() since D3.
    titleColor: "#2f2e41",
  },
};

describe("DesignThemeSchema", () => {
  it("parses a full designTheme payload with every optional block populated", () => {
    const result = DesignThemeSchema.safeParse(FULL_DESIGN_THEME);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data).toEqual(FULL_DESIGN_THEME);
    }
  });

  it("parses a designTheme with only the required `name` field", () => {
    const result = DesignThemeSchema.safeParse({ name: "analyst_clean" });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe("analyst_clean");
      expect(result.data.dashboardBackground).toBeUndefined();
      expect(result.data.spacing).toBeUndefined();
      expect(result.data.chartCard).toBeUndefined();
      expect(result.data.kpiTile).toBeUndefined();
      expect(result.data.header).toBeUndefined();
      expect(result.data.chrome).toBeUndefined();
    }
  });

  it("rejects a designTheme missing the required `name` field", () => {
    const result = DesignThemeSchema.safeParse({ dashboardBackground: "#0b1f3a" });
    expect(result.success).toBe(false);
  });
});

describe("DashboardPlan — designTheme (optional, carry-only)", () => {
  it("parses a plan without designTheme unchanged (backward-compat)", () => {
    const result = DashboardPlanSchema.safeParse(MINIMAL_PLAN);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.designTheme).toBeUndefined();
    }
  });

  it("round-trips a full designTheme through DashboardPlanSchema.parse", () => {
    const plan = DashboardPlanSchema.parse({
      ...MINIMAL_PLAN,
      designTheme: FULL_DESIGN_THEME,
    });
    expect(plan.designTheme).toEqual(FULL_DESIGN_THEME);
  });

  it("rejects a plan whose designTheme is missing the required `name` field", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      designTheme: { dashboardBackground: "#0b1f3a" },
    });
    expect(result.success).toBe(false);
  });
});

describe("SheetSpec — optional styleRules block (carry-only)", () => {
  it("parses a sheet with styleRules and round-trips element/formats", () => {
    const result = SheetSpecSchema.safeParse({
      ...MINIMAL_SHEET,
      styleRules: [
        { element: "worksheet-title", formats: { "font-color": "#0b1f3a", bold: "true" } },
        { element: "axis-label", formats: { "font-size": "10" } },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.styleRules).toHaveLength(2);
      expect(result.data.styleRules?.[0]?.element).toBe("worksheet-title");
      expect(result.data.styleRules?.[0]?.formats).toEqual({
        "font-color": "#0b1f3a",
        bold: "true",
      });
    }
  });

  it("parses a sheet without styleRules unchanged (backward-compat)", () => {
    const result = SheetSpecSchema.safeParse(MINIMAL_SHEET);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.styleRules).toBeUndefined();
    }
  });
});

describe("DashboardPlanSchema — unknown-key handling (consistent with existing behavior)", () => {
  it("strips an unrecognized top-level key rather than rejecting the plan", () => {
    // DashboardPlanSchema is a plain z.object() (no .strict()), so unknown
    // keys are silently stripped by zod's default behavior — this has been
    // true since before this slice (e.g. the schema has never used
    // .strict()). This test locks in that this slice did not change it.
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      someFutureUnknownField: "should be stripped, not rejected",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data).not.toHaveProperty("someFutureUnknownField");
    }
  });

  it("strips an unrecognized key inside designTheme rather than rejecting it", () => {
    const result = DashboardPlanSchema.safeParse({
      ...MINIMAL_PLAN,
      designTheme: { name: "executive_dark", unknownThemeField: "ignored" },
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.designTheme).not.toHaveProperty("unknownThemeField");
    }
  });
});
