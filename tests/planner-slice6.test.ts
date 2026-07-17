/**
 * Slice 6 tests — Phase E1 Slice C: Few/visionary design-excellence rules
 * (BI_DESIGN §9) + the remaining persona overrides (chartDeny, kpiEmphasis,
 * preferredArtifact, tone).
 *
 * Covers:
 * - chartDeny replaces a persona-denied mark type with bar + a rationale note.
 * - kpiEmphasis caps the KPI band to 4 / 3 / 2 tiles (high / medium / low).
 * - FEW-1: no-pie regression — part-to-whole/share/proportion questions never
 *   resolve to anything other than bar (or its text/KPI downgrade); "pie" is
 *   not a representable MarkType at all.
 * - FEW-6: the TOP_N_LIMIT constant formalizes the G-05 top-N threshold and
 *   the annotation still fires end-to-end for a high-cardinality dimension.
 * - FEW-7: the small-multiples hint is appended to a bar-colored-by-a-second-
 *   dimension sheet when the question signals a cross-dimension comparison.
 * - preferredArtifact "pulse" surfaces an honest openQuestion naming the
 *   phase where that capability lands; "dashboard" (or unset) does not.
 *   "story" now ships (Phase E4): it drives a deterministic storyArc instead
 *   of a stub openQuestion — see tests/planner-storyArc.test.ts for coverage
 *   of that behavior; this file only asserts the openQuestion is gone.
 * - tone "concise" trims the proposal summary to one sentence + the layout
 *   line while still preserving persona provenance.
 * - Determinism: identical inputs (including the new override fields) yield
 *   byte-identical plans/proposals across repeated calls.
 *
 * All tests are purely deterministic — no LLM calls, no network, no
 * filesystem I/O (brand.yaml resolution is exercised elsewhere, in
 * tests/tools.test.ts and tests/branding.test.ts — this file only exercises
 * the already-resolved values the pure planner receives).
 */

import { describe, it, expect } from "vitest";
import { generatePlan } from "../src/planner/plan.js";
import { buildProposal } from "../src/planner/proposal.js";
import {
  applyAudienceClamps,
  KPI_EMPHASIS_MAX_TILES,
  DEFAULT_MAX_KPI_TILES,
} from "../src/planner/audience.js";
import { classifyFields } from "../src/planner/fields.js";
import type { FieldHint } from "../src/planner/fields.js";
import {
  applyMarkHeuristic,
  GAP_G05,
  TOP_N_LIMIT,
  FEW_07_SMALL_MULTIPLES_NOTE,
  appendSmallMultiplesHintIfApplicable,
} from "../src/planner/marks.js";
import { MarkTypeEnum, type SheetSpec } from "../src/planner/schema.js";

// ---------------------------------------------------------------------------
// §1 — chartDeny (BI_DESIGN §9, persona override)
// ---------------------------------------------------------------------------

describe("chartDeny — persona override replaces denied marks with bar (BI_DESIGN §9)", () => {
  it("denies a scatter mark the audience would otherwise allow, falling back to bar with a rationale note", () => {
    const sheet: SheetSpec = {
      title: "Sales vs Profit",
      markType: "scatter",
      cols: [],
      rows: [],
      measures: ["Sales", "Profit"],
      scatter: { x: "Sales", y: "Profit" },
    };
    // Analyst normally allows scatter — chartDeny overrides that.
    const { sheets } = applyAudienceClamps([sheet], "analyst", "", undefined, {
      chartDeny: ["scatter"],
    });
    expect(sheets).toHaveLength(1);
    expect(sheets[0]?.markType).toBe("bar");
    expect(sheets[0]?.scatter).toBeUndefined();
    expect(sheets[0]?.rationale).toMatch(/chartDeny/i);
  });

  it("is case-insensitive and clears the geo block when denying map_filled", () => {
    const sheet: SheetSpec = {
      title: "Sales by State",
      markType: "map_filled",
      cols: [],
      rows: ["State"],
      measures: ["Sales"],
      geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
    };
    const { sheets } = applyAudienceClamps([sheet], "analyst", "", undefined, {
      chartDeny: ["MAP_FILLED"],
    });
    expect(sheets[0]?.markType).toBe("bar");
    expect(sheets[0]?.geo).toBeUndefined();
  });

  it("leaves sheets untouched when chartDeny is absent or empty", () => {
    const sheet: SheetSpec = {
      title: "Sales by Region",
      markType: "bar",
      cols: ["Region"],
      rows: [],
      measures: ["Sales"],
    };
    const { sheets: noOverride } = applyAudienceClamps([sheet], "analyst");
    const { sheets: emptyDeny } = applyAudienceClamps([sheet], "analyst", "", undefined, {
      chartDeny: [],
    });
    expect(noOverride[0]?.markType).toBe("bar");
    expect(emptyDeny[0]?.markType).toBe("bar");
  });

  it("a mark type absent from chartDeny is unaffected", () => {
    const sheet: SheetSpec = {
      title: "Sales by Region",
      markType: "bar",
      cols: ["Region"],
      rows: [],
      measures: ["Sales"],
    };
    const { sheets } = applyAudienceClamps([sheet], "analyst", "", undefined, {
      chartDeny: ["scatter", "map_filled"],
    });
    expect(sheets[0]?.markType).toBe("bar");
    expect(sheets[0]?.rationale ?? "").not.toMatch(/chartDeny/i);
  });
});

// ---------------------------------------------------------------------------
// §2 — kpiEmphasis (BI_DESIGN §9, persona override)
// ---------------------------------------------------------------------------

function makeKpiTile(measure: string): SheetSpec {
  return {
    title: measure,
    markType: "text",
    kind: "kpi_tile",
    cols: [],
    rows: [],
    measures: [measure],
    kpi: { primaryMeasure: measure },
  };
}

describe("kpiEmphasis — persona override caps the KPI band (BI_DESIGN §9)", () => {
  const fourTiles = ["Sales", "Profit", "Profit Ratio", "Quantity"].map(makeKpiTile);

  it("exports the emphasis→tile-count map as named constants", () => {
    expect(KPI_EMPHASIS_MAX_TILES).toEqual({ high: 4, medium: 3, low: 2 });
    expect(DEFAULT_MAX_KPI_TILES).toBe(4);
  });

  it("defaults to 4 tiles when kpiEmphasis is not set (unchanged current behavior)", () => {
    const { sheets } = applyAudienceClamps(fourTiles, "exec");
    expect(sheets.filter((s) => s.kind === "kpi_tile")).toHaveLength(4);
  });

  it('kpiEmphasis "high" keeps all 4 tiles', () => {
    const { sheets } = applyAudienceClamps(fourTiles, "exec", "", undefined, {
      kpiEmphasis: "high",
    });
    expect(sheets.filter((s) => s.kind === "kpi_tile")).toHaveLength(4);
  });

  it('kpiEmphasis "medium" caps to 3 tiles with a rationale note on the last survivor', () => {
    const { sheets } = applyAudienceClamps(fourTiles, "exec", "", undefined, {
      kpiEmphasis: "medium",
    });
    const tiles = sheets.filter((s) => s.kind === "kpi_tile");
    expect(tiles).toHaveLength(3);
    expect(tiles[tiles.length - 1]?.rationale).toMatch(/kpiEmphasis/i);
    // The first N-1 tiles are untouched (no note leaked onto them).
    expect(tiles[0]?.rationale ?? "").not.toMatch(/kpiEmphasis/i);
  });

  it('kpiEmphasis "low" caps to 2 tiles', () => {
    const { sheets } = applyAudienceClamps(fourTiles, "exec", "", undefined, {
      kpiEmphasis: "low",
    });
    expect(sheets.filter((s) => s.kind === "kpi_tile")).toHaveLength(2);
  });

  it("end-to-end via generatePlan: medium kpiEmphasis caps the exec KPI band to 3 tiles", () => {
    const fields: FieldHint[] = [
      { name: "Sales", dataType: "number" },
      { name: "Profit", dataType: "number" },
      { name: "Profit Ratio", dataType: "number" },
      { name: "Quantity", dataType: "number" },
      { name: "Category", dataType: "string" },
    ];
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing by category?",
      fieldHints: fields,
      datasourceLuid: "DS",
      datasourceName: "Test",
      projectName: "Test",
      constraintOverrides: { kpiEmphasis: "medium" },
    });
    const tiles = plan.sheets.filter((s) => s.kind === "kpi_tile");
    expect(tiles).toHaveLength(3);
    expect(plan.layoutGrammar?.kpiTileTitles).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// §3 — FEW-1: no-pie default (regression)
// ---------------------------------------------------------------------------

describe('FEW-1 — no-pie default (BI_DESIGN §9)', () => {
  it('"pie" is not a representable MarkType — structurally impossible to emit', () => {
    expect(MarkTypeEnum.safeParse("pie").success).toBe(false);
  });

  const PART_TO_WHOLE_QUESTIONS = [
    "What is our market share by region?",
    "Show the proportion of sales by category",
    "part-to-whole breakdown of revenue by segment",
    "What percentage of profit comes from each region?",
    "composition of orders by channel",
  ];

  for (const audience of ["exec", "analyst", "operational", "mixed"] as const) {
    it(`"${audience}" audience never emits anything but bar/text for part-to-whole questions`, () => {
      for (const businessQuestion of PART_TO_WHOLE_QUESTIONS) {
        const plan = generatePlan({
          mode: "autonomous",
          audience,
          businessQuestion,
          fieldHints: [
            { name: "Region", dataType: "string" },
            { name: "Sales", dataType: "number" },
          ],
          datasourceLuid: "DS",
          datasourceName: "Test",
          projectName: "Test",
        });
        for (const sheet of plan.sheets) {
          expect(["bar", "text"]).toContain(sheet.markType);
        }
      }
    });
  }
});

// ---------------------------------------------------------------------------
// §4 — FEW-6: top-N threshold formalized as a named constant
// ---------------------------------------------------------------------------

describe("FEW-6 — top-N discipline formalized (BI_DESIGN §9)", () => {
  it("TOP_N_LIMIT is a named constant kept in sync with the GAP_G05 rationale text", () => {
    expect(TOP_N_LIMIT).toBe(10);
    expect(GAP_G05).toBe(
      `High-cardinality dimension — apply Top ${TOP_N_LIMIT} filter in Tableau Desktop: right-click field > Filter > Top > By field.`,
    );
  });

  it("a high-cardinality dimension still carries the G-05 top-N note end-to-end", () => {
    const plan = generatePlan({
      mode: "directed",
      audience: "analyst",
      directions: "revenue by customer name",
      fieldHints: [
        { name: "customer_name", dataType: "string" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Test",
      projectName: "Test",
    });
    const hasG05 = plan.sheets.some((s) => s.rationale?.includes(GAP_G05));
    expect(hasG05).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §5 — FEW-7: small-multiples hint
// ---------------------------------------------------------------------------

describe("FEW-7 — small-multiples hint (BI_DESIGN §9)", () => {
  it("bar colored by a second dimension gets the hint when the question signals a cross-dimension comparison", () => {
    const fields = classifyFields([
      { name: "Category", dataType: "string" },
      { name: "Segment", dataType: "string" },
      { name: "Sales", dataType: "number" },
    ]);
    const sheets = applyMarkHeuristic("compare sales by category and segment", fields);
    const bar = sheets.find((s) => s.markType === "bar" && s.color?.kind === "dimension");
    expect(bar).toBeDefined();
    expect(bar?.rationale).toContain(FEW_07_SMALL_MULTIPLES_NOTE);
  });

  it("does not append the hint when the question has no comparison language", () => {
    const fields = classifyFields([
      { name: "Category", dataType: "string" },
      { name: "Segment", dataType: "string" },
      { name: "Sales", dataType: "number" },
    ]);
    const sheets = applyMarkHeuristic("sales by category", fields);
    const bar = sheets.find((s) => s.markType === "bar");
    expect(bar?.rationale ?? "").not.toContain(FEW_07_SMALL_MULTIPLES_NOTE);
  });

  it("does not append the hint to a bar with no second-dimension color", () => {
    const fields = classifyFields([
      { name: "Category", dataType: "string" },
      { name: "Sales", dataType: "number" },
    ]);
    const sheets = applyMarkHeuristic("compare sales across category", fields);
    const bar = sheets.find((s) => s.markType === "bar");
    expect(bar?.rationale ?? "").not.toContain(FEW_07_SMALL_MULTIPLES_NOTE);
  });

  it("is a no-op for non-bar marks (direct unit test)", () => {
    const lineSheet = {
      title: "Sales over Time",
      markType: "line" as const,
      cols: ["Order Date"],
      rows: [],
      measures: ["Sales"],
      color: { field: "Segment", kind: "dimension" as const },
    };
    const result = appendSmallMultiplesHintIfApplicable(lineSheet, "compare sales across segment");
    expect(result.rationale).toBeUndefined();
  });

  it("end-to-end via generatePlan: exec KPI-band bar picks up the hint", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "compare sales across category and segment",
      fieldHints: [
        { name: "Category", dataType: "string" },
        { name: "Segment", dataType: "string" },
        { name: "Sales", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Test",
      projectName: "Test",
    });
    const bar = plan.sheets.find((s) => s.markType === "bar");
    expect(bar?.rationale ?? "").toContain(FEW_07_SMALL_MULTIPLES_NOTE);
  });
});

// ---------------------------------------------------------------------------
// §6 — preferredArtifact: honest openQuestion surfacing
// ---------------------------------------------------------------------------

const ARTIFACT_TEST_FIELDS: FieldHint[] = [
  { name: "order_date", dataType: "date" },
  { name: "revenue", dataType: "number" },
];

const artifactBaseInput = {
  mode: "autonomous" as const,
  audience: "exec" as const,
  businessQuestion: "How is revenue trending?",
  fieldHints: ARTIFACT_TEST_FIELDS,
  datasourceLuid: "DS",
  datasourceName: "Sales",
  projectName: "Sales",
  personaName: "cfo",
};

describe("preferredArtifact — honest capability surfacing (BI_DESIGN §9)", () => {
  // Phase E4: "story" shipped — generatePlan() now emits a storyArc for it
  // instead of a "not yet available" stub. Full storyArc/storyOutline
  // coverage lives in tests/planner-storyArc.test.ts; this test only pins
  // that the old openQuestion is gone.
  it('"story" no longer appends a "not yet available" openQuestion (Phase E4 shipped)', () => {
    const plan = generatePlan({ ...artifactBaseInput, personaPreferredArtifact: "story" });
    const proposal = buildProposal(plan);
    const hasNotAvailableNote =
      proposal.openQuestions?.some((q) => /story/i.test(q) && /not yet available/i.test(q)) ??
      false;
    expect(hasNotAvailableNote).toBe(false);
    expect(plan.storyArc?.length).toBeGreaterThan(0);
  });

  it('"pulse" appends an openQuestion naming Phase E3', () => {
    const plan = generatePlan({ ...artifactBaseInput, personaPreferredArtifact: "pulse" });
    const proposal = buildProposal(plan);
    expect(proposal.openQuestions?.some((q) => /pulse/i.test(q) && /Phase E3/.test(q))).toBe(true);
  });

  it('"dashboard" does not append an artifact openQuestion', () => {
    const plan = generatePlan({ ...artifactBaseInput, personaPreferredArtifact: "dashboard" });
    const proposal = buildProposal(plan);
    const hasArtifactNote = proposal.openQuestions?.some((q) => /Phase E[34]/.test(q)) ?? false;
    expect(hasArtifactNote).toBe(false);
  });

  it("no preferredArtifact set does not append an artifact openQuestion", () => {
    const plan = generatePlan(artifactBaseInput);
    const proposal = buildProposal(plan);
    const hasArtifactNote = proposal.openQuestions?.some((q) => /Phase E[34]/.test(q)) ?? false;
    expect(hasArtifactNote).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §7 — tone: concise trims the summary
// ---------------------------------------------------------------------------

describe("tone — concise trims the proposal summary (BI_DESIGN §9)", () => {
  const toneBaseInput = {
    mode: "autonomous" as const,
    audience: "exec" as const,
    businessQuestion: "How is revenue trending?",
    fieldHints: ARTIFACT_TEST_FIELDS,
    datasourceLuid: "DS",
    datasourceName: "Sales",
    projectName: "Sales",
    personaName: "cfo",
    brandName: "Acme Corp",
  };

  it("concise tone trims the summary to one sentence plus the layout line, preserving persona provenance", () => {
    const plan = generatePlan({ ...toneBaseInput, personaTone: "concise" });
    const proposal = buildProposal(plan);
    expect(proposal.summary).toContain(proposal.layoutSummary);
    expect(proposal.summary).not.toContain("Tailored for the");
    expect(proposal.summary).toMatch(/cfo/i);
  });

  it("detailed tone keeps the full persona-tailored summary (no layout line appended)", () => {
    const plan = generatePlan({ ...toneBaseInput, personaTone: "detailed" });
    const proposal = buildProposal(plan);
    expect(proposal.summary).toContain("Tailored for the");
    expect(proposal.summary).not.toContain(proposal.layoutSummary);
  });

  it("no tone set behaves like detailed (backward-compatible default)", () => {
    const plan = generatePlan(toneBaseInput);
    const proposal = buildProposal(plan);
    expect(proposal.summary).toContain("Tailored for the");
  });
});

// ---------------------------------------------------------------------------
// §8 — Determinism (new override fields included)
// ---------------------------------------------------------------------------

describe("determinism — Few/visionary layer + persona overrides (Slice 6)", () => {
  it("identical input (with chartDeny + kpiEmphasis) produces byte-identical plans", () => {
    const input = {
      mode: "autonomous" as const,
      audience: "analyst" as const,
      businessQuestion: "correlation between sales and profit across category and segment",
      fieldHints: [
        { name: "Sales", dataType: "number" as const },
        { name: "Profit", dataType: "number" as const },
        { name: "Category", dataType: "string" as const },
        { name: "Segment", dataType: "string" as const },
      ],
      datasourceLuid: "DS",
      datasourceName: "Test",
      projectName: "Test",
      constraintOverrides: { chartDeny: ["scatter"], kpiEmphasis: "low" as const },
    };
    const plan1 = generatePlan(input);
    const plan2 = generatePlan(input);
    expect(JSON.stringify(plan1)).toBe(JSON.stringify(plan2));
  });

  it("identical input (with preferredArtifact + tone) produces byte-identical proposals", () => {
    const input = {
      ...artifactBaseInput,
      personaPreferredArtifact: "pulse" as const,
      personaTone: "concise" as const,
    };
    const proposal1 = buildProposal(generatePlan(input));
    const proposal2 = buildProposal(generatePlan(input));
    expect(JSON.stringify(proposal1)).toBe(JSON.stringify(proposal2));
  });
});
