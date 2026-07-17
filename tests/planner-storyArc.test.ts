/**
 * Phase E4 (Stories, Pillar E) — planner storyArc tests.
 *
 * Covers:
 * - Schema: StoryArcPointSchema validates {caption, capturedSheet}; storyArc/
 *   storyName are optional on DashboardPlanSchema; storyOutline is optional
 *   on DashboardProposalSchema.
 * - wantsStoryArc(): true when personaPreferredArtifact === "story" OR the
 *   question uses story/narrative/presentation language; false otherwise.
 * - buildStoryArc(): headline point (the plan's leading sheet) + one point
 *   per remaining sheet, in plan order; every capturedSheet equals a sheet
 *   title; tone "concise" uses the bare sheet title, default/"detailed" uses
 *   a narrative sentence.
 * - generatePlan() end-to-end: persona preference and question-language paths
 *   both emit a storyArc; a plain dashboard question emits none.
 * - buildProposal(): storyOutline mirrors plan.storyArc captions and is
 *   rendered into the summary text.
 * - Determinism: identical inputs produce byte-identical plans/proposals.
 *
 * All tests are purely deterministic — no LLM calls, no network, no
 * filesystem I/O.
 */

import { describe, it, expect } from "vitest";
import { generatePlan, wantsStoryArc, buildStoryArc } from "../src/planner/plan.js";
import { buildProposal } from "../src/planner/proposal.js";
import {
  StoryArcPointSchema,
  DashboardPlanSchema,
  DashboardProposalSchema,
  type SheetSpec,
} from "../src/planner/schema.js";
import type { FieldHint } from "../src/planner/fields.js";

// ---------------------------------------------------------------------------
// §1 — Schema
// ---------------------------------------------------------------------------

describe("StoryArcPointSchema", () => {
  it("accepts a valid {caption, capturedSheet} object", () => {
    const result = StoryArcPointSchema.safeParse({
      caption: "Here's the headline.",
      capturedSheet: "Sales KPI",
    });
    expect(result.success).toBe(true);
  });

  it("rejects an empty caption", () => {
    expect(
      StoryArcPointSchema.safeParse({ caption: "", capturedSheet: "Sales KPI" }).success,
    ).toBe(false);
  });

  it("rejects an empty capturedSheet", () => {
    expect(StoryArcPointSchema.safeParse({ caption: "x", capturedSheet: "" }).success).toBe(
      false,
    );
  });

  it("rejects a missing capturedSheet", () => {
    expect(StoryArcPointSchema.safeParse({ caption: "x" }).success).toBe(false);
  });
});

describe("DashboardPlanSchema — storyArc / storyName are optional", () => {
  const basePlan = {
    schemaVersion: 1,
    kind: "plan",
    workbookName: "WB",
    datasourceLuid: "DS",
    datasourceName: "Sales",
    projectName: "Sales",
    audience: "exec",
    rationale: "r",
    dashboardLayout: "tiled_vertical",
    sheets: [{ title: "Rev", markType: "bar", cols: ["Region"], rows: [], measures: ["Revenue"] }],
  };

  it("parses without storyArc/storyName (backward compatible)", () => {
    const result = DashboardPlanSchema.safeParse(basePlan);
    expect(result.success).toBe(true);
  });

  it("parses with a valid storyArc + storyName", () => {
    const result = DashboardPlanSchema.safeParse({
      ...basePlan,
      storyName: "Revenue Story",
      storyArc: [{ caption: "Headline.", capturedSheet: "Rev" }],
    });
    expect(result.success).toBe(true);
  });

  it("rejects a storyArc entry with an empty caption", () => {
    const result = DashboardPlanSchema.safeParse({
      ...basePlan,
      storyArc: [{ caption: "", capturedSheet: "Rev" }],
    });
    expect(result.success).toBe(false);
  });
});

describe("DashboardProposalSchema — storyOutline is optional", () => {
  const baseProposal = {
    schemaVersion: 1,
    kind: "proposal",
    workbookName: "WB",
    audience: "exec",
    datasourceName: "Sales",
    projectName: "Sales",
    summary: "s",
    kpiStrip: [],
    views: [{ title: "Rev", chartType: "bar", encodingSummary: "bar: Revenue", fields: ["Revenue"] }],
    layoutSummary: "1 chart.",
    plan: {
      schemaVersion: 1,
      kind: "plan",
      workbookName: "WB",
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      audience: "exec",
      rationale: "r",
      dashboardLayout: "tiled_vertical",
      sheets: [{ title: "Rev", markType: "bar", cols: ["Region"], rows: [], measures: ["Revenue"] }],
    },
  };

  it("parses without storyOutline (backward compatible)", () => {
    expect(DashboardProposalSchema.safeParse(baseProposal).success).toBe(true);
  });

  it("parses with a storyOutline array of captions", () => {
    const result = DashboardProposalSchema.safeParse({
      ...baseProposal,
      storyOutline: ["Headline.", "A closer look at Rev."],
    });
    expect(result.success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §2 — wantsStoryArc
// ---------------------------------------------------------------------------

describe("wantsStoryArc — determines when a story arc is warranted", () => {
  it('true when personaPreferredArtifact === "story", regardless of question text', () => {
    expect(wantsStoryArc("story", "How is revenue trending?")).toBe(true);
    expect(wantsStoryArc("story", "")).toBe(true);
  });

  it('false when personaPreferredArtifact is "dashboard" or "pulse" and the question has no story language', () => {
    expect(wantsStoryArc("dashboard", "How is revenue trending?")).toBe(false);
    expect(wantsStoryArc("pulse", "How is revenue trending?")).toBe(false);
  });

  it('false when personaPreferredArtifact is undefined and the question has no story language', () => {
    expect(wantsStoryArc(undefined, "How is revenue trending?")).toBe(false);
  });

  it.each(["Tell the story of our Q4 performance", "I need a narrative for the board", "Build a presentation on revenue"])(
    'true when the question contains story/narrative/presentation language: "%s"',
    (question) => {
      expect(wantsStoryArc(undefined, question)).toBe(true);
      expect(wantsStoryArc("dashboard", question)).toBe(true);
    },
  );

  it("is case-insensitive for question language", () => {
    expect(wantsStoryArc(undefined, "STORY time")).toBe(true);
    expect(wantsStoryArc(undefined, "NARRATIVE")).toBe(true);
  });

  it("does not false-positive on an ordinary business question with no story language", () => {
    expect(wantsStoryArc(undefined, "revenue by category")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §3 — buildStoryArc
// ---------------------------------------------------------------------------

const KPI_SHEET: SheetSpec = {
  title: "Sales",
  markType: "text",
  kind: "kpi_tile",
  cols: [],
  rows: [],
  measures: ["Sales"],
  kpi: { primaryMeasure: "Sales" },
};

const BAR_SHEET: SheetSpec = {
  title: "Sales by Category",
  markType: "bar",
  cols: ["Category"],
  rows: [],
  measures: ["Sales"],
};

const MAP_SHEET: SheetSpec = {
  title: "Sales by State",
  markType: "map_filled",
  cols: [],
  rows: ["State"],
  measures: ["Sales"],
  geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
};

const SCATTER_SHEET: SheetSpec = {
  title: "Sales vs Profit",
  markType: "scatter",
  cols: [],
  rows: [],
  measures: ["Sales", "Profit"],
  scatter: { x: "Sales", y: "Profit" },
};

describe("buildStoryArc — deterministic headline + chart points", () => {
  it("returns an empty array for an empty sheet list", () => {
    expect(buildStoryArc([], "Title", undefined)).toEqual([]);
  });

  it("emits exactly one point for a single-sheet plan (headline only)", () => {
    const points = buildStoryArc([KPI_SHEET], "Executive Overview", undefined);
    expect(points).toHaveLength(1);
    expect(points[0]?.capturedSheet).toBe("Sales");
  });

  it("headline point captures the FIRST sheet; remaining sheets get one point each, in order", () => {
    const points = buildStoryArc([KPI_SHEET, BAR_SHEET, MAP_SHEET], "Executive Overview", undefined);
    expect(points).toHaveLength(3);
    expect(points.map((p) => p.capturedSheet)).toEqual(["Sales", "Sales by Category", "Sales by State"]);
  });

  it("every capturedSheet equals an existing sheet title (no invented references)", () => {
    const sheets = [KPI_SHEET, BAR_SHEET, MAP_SHEET, SCATTER_SHEET];
    const points = buildStoryArc(sheets, "Executive Overview", undefined);
    const validTitles = new Set(sheets.map((s) => s.title));
    for (const point of points) {
      expect(validTitles.has(point.capturedSheet)).toBe(true);
    }
  });

  it('tone "concise" uses the bare sheet title (headline uses dashboardTitle) for every caption', () => {
    const points = buildStoryArc([KPI_SHEET, BAR_SHEET], "Executive Overview", "concise");
    expect(points[0]?.caption).toBe("Executive Overview");
    expect(points[1]?.caption).toBe("Sales by Category");
  });

  it('default/"detailed" tone produces a narrative sentence, not the bare title', () => {
    const points = buildStoryArc([KPI_SHEET, BAR_SHEET], "Executive Overview", undefined);
    expect(points[0]?.caption).not.toBe("Executive Overview");
    expect(points[0]?.caption).toContain("Executive Overview");
    expect(points[1]?.caption).not.toBe("Sales by Category");
  });

  it("derives a geo-flavored caption for a map_filled sheet (detailed tone)", () => {
    const points = buildStoryArc([KPI_SHEET, MAP_SHEET], "Executive Overview", "detailed");
    expect(points[1]?.caption.toLowerCase()).toContain("state");
  });

  it("derives a vs-flavored caption for a scatter sheet (detailed tone)", () => {
    const points = buildStoryArc([KPI_SHEET, SCATTER_SHEET], "Executive Overview", "detailed");
    expect(points[1]?.caption).toContain("vs");
  });

  it("is pure/deterministic: identical inputs produce byte-identical output", () => {
    const sheets = [KPI_SHEET, BAR_SHEET, MAP_SHEET];
    const a = buildStoryArc(sheets, "Executive Overview", undefined);
    const b = buildStoryArc(sheets, "Executive Overview", undefined);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});

// ---------------------------------------------------------------------------
// §4 — generatePlan() end-to-end
// ---------------------------------------------------------------------------

const STORY_FIELDS: FieldHint[] = [
  { name: "Category", dataType: "string" },
  { name: "Sales", dataType: "number" },
  { name: "Order Date", dataType: "date" },
];

describe("generatePlan — storyArc end-to-end", () => {
  it("personaPreferredArtifact 'story' emits a storyArc + storyName over the plan's own sheets", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "story",
    });
    expect(plan.storyArc).toBeDefined();
    expect(plan.storyArc!.length).toBeGreaterThan(0);
    expect(plan.storyName).toBe(plan.dashboardTitle);
    const validTitles = new Set(plan.sheets.map((s) => s.title));
    for (const point of plan.storyArc!) {
      expect(validTitles.has(point.capturedSheet)).toBe(true);
    }
  });

  it("question language ('tell the story of...') emits a storyArc without any persona preference", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "analyst",
      businessQuestion: "Tell the story of our sales performance by category",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    expect(plan.storyArc).toBeDefined();
    expect(plan.storyArc!.length).toBeGreaterThan(0);
  });

  it("a plain dashboard question with no persona preference emits no storyArc", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    expect(plan.storyArc).toBeUndefined();
    expect(plan.storyName).toBeUndefined();
  });

  it("personaPreferredArtifact 'dashboard' does not emit a storyArc", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "dashboard",
    });
    expect(plan.storyArc).toBeUndefined();
  });

  it("determinism: identical inputs produce a byte-identical storyArc across repeated calls", () => {
    const input = {
      mode: "autonomous" as const,
      audience: "exec" as const,
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "story" as const,
    };
    const plan1 = generatePlan(input);
    const plan2 = generatePlan(input);
    expect(JSON.stringify(plan1)).toBe(JSON.stringify(plan2));
  });
});

// ---------------------------------------------------------------------------
// §5 — buildProposal() storyOutline
// ---------------------------------------------------------------------------

describe("buildProposal — storyOutline surfacing", () => {
  it("mirrors plan.storyArc captions in proposal.storyOutline, in order", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "story",
    });
    const proposal = buildProposal(plan);
    expect(proposal.storyOutline).toBeDefined();
    expect(proposal.storyOutline).toEqual(plan.storyArc!.map((p) => p.caption));
  });

  it("renders the story outline into the human-readable summary text", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "story",
    });
    const proposal = buildProposal(plan);
    expect(proposal.summary).toMatch(/story/i);
    expect(proposal.summary).toContain(String(proposal.storyOutline!.length));
  });

  it("omits storyOutline entirely when the plan has no storyArc", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const proposal = buildProposal(plan);
    expect(proposal.storyOutline).toBeUndefined();
  });

  it("determinism: identical plans produce a byte-identical storyOutline across repeated calls", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: STORY_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
      personaPreferredArtifact: "story",
    });
    const proposal1 = buildProposal(plan);
    const proposal2 = buildProposal(plan);
    expect(JSON.stringify(proposal1)).toBe(JSON.stringify(proposal2));
  });
});
