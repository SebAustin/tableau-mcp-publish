/**
 * Tests for M2/M3 — computed comparison-period wiring on `buildKpiStrip` and
 * the exec KPI-band plan (external skill backlog #1/#2, GAPS.md Sec 4).
 *
 * Closes ACCEPTANCE.md's documented NULL-delta residual: a KPI tile whose
 * data carries no pre-existing delta COLUMN (the common case — e.g. the
 * Superstore CSV) now gets a COMPUTED comparison (`comparisonKind` +
 * `dateField`) instead of a permanently-NULL delta, whenever a usable date
 * dimension exists.
 *
 * M3 precedence fix (live-probe finding): a computed YoY now WINS over an
 * auto-paired `*_Difference`/`*_Delta` column whenever a usable date
 * dimension exists — auto-pairing is a NAME heuristic that can silently be
 * empty (proven by the real Superstore dataset: "Sales" auto-pairs to
 * "Sales Difference", which is 100% NULL, the exact bug being fixed). See
 * `buildKpiStrip`'s and `comparison.ts`'s docstrings for the full four-tier
 * precedence order.
 */

import { describe, it, expect } from "vitest";
import { classifyFields } from "../src/planner/fields.js";
import type { FieldHint } from "../src/planner/fields.js";
import { buildKpiStrip } from "../src/planner/marks.js";
import { generatePlan } from "../src/planner/plan.js";

// Superstore-shaped fields with NO period-compare (CP/PP/Difference) columns
// — the real-world shape ACCEPTANCE.md's residual describes.
const FIELDS_NO_DELTA_WITH_DATE: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Profit", dataType: "number" },
  { name: "Order Date", dataType: "date" },
  { name: "Region", dataType: "string" },
];

const FIELDS_NO_DELTA_NO_DATE: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Region", dataType: "string" },
];

const FIELDS_WITH_EXPLICIT_DELTA: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "PP Sales", dataType: "number" },
  { name: "Sales Difference", dataType: "number" },
  { name: "Order Date", dataType: "date" },
  { name: "Region", dataType: "string" },
];

describe("buildKpiStrip — computed comparison (M2)", () => {
  it("sets comparisonKind='yoy' + dateField when no delta column but a date dim exists", () => {
    const all = classifyFields(FIELDS_NO_DELTA_WITH_DATE);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Show total sales");
    expect(tiles[0]!.kpi!.comparisonKind).toBe("yoy");
    expect(tiles[0]!.kpi!.dateField).toBe("Order Date");
    // No pre-existing data columns exist for these — computed path must not
    // fabricate them.
    expect(tiles[0]!.kpi!.comparisonMeasure).toBeUndefined();
    expect(tiles[0]!.kpi!.deltaMeasure).toBeUndefined();
  });

  it("MoM keyword override still selects 'mom' through buildKpiStrip", () => {
    const all = classifyFields(FIELDS_NO_DELTA_WITH_DATE);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Sales month over month");
    expect(tiles[0]!.kpi!.comparisonKind).toBe("mom");
    expect(tiles[0]!.kpi!.dateField).toBe("Order Date");
  });

  it("does not set comparisonKind/dateField when no date dimension exists", () => {
    const all = classifyFields(FIELDS_NO_DELTA_NO_DATE);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Show total sales");
    expect(tiles[0]!.kpi!.comparisonKind).toBeUndefined();
    expect(tiles[0]!.kpi!.dateField).toBeUndefined();
  });

  it("M3: a computed YoY WINS over an auto-paired *_Difference column when a date dim exists", () => {
    // This is the exact live-probe finding: FIELDS_WITH_EXPLICIT_DELTA has
    // BOTH an auto-pairable "Sales Difference" column AND "Order Date".
    // Deferring to "Sales Difference" (the pre-M3 rule) would silently
    // reproduce the 100%-NULL-delta bug this backlog item exists to close.
    const all = classifyFields(FIELDS_WITH_EXPLICIT_DELTA);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Sales year over year");
    expect(tiles[0]!.kpi!.deltaMeasure).toBeUndefined();
    expect(tiles[0]!.kpi!.comparisonKind).toBe("yoy");
    expect(tiles[0]!.kpi!.dateField).toBe("Order Date");
    // comparisonMeasure (PP binding) is unaffected by the delta precedence —
    // it always binds independently when a PP/CP counterpart exists.
    expect(tiles[0]!.kpi!.comparisonMeasure).toBe("PP Sales");
  });

  it("M3: falls back to the auto-paired delta column when YoY is NOT computable (no date dim)", () => {
    const fieldsNoDate: FieldHint[] = FIELDS_WITH_EXPLICIT_DELTA.filter(
      (f) => f.name !== "Order Date",
    );
    const all = classifyFields(fieldsNoDate);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Sales year over year");
    expect(tiles[0]!.kpi!.deltaMeasure).toBe("Sales Difference");
    expect(tiles[0]!.kpi!.comparisonMeasure).toBe("PP Sales");
    expect(tiles[0]!.kpi!.comparisonKind).toBeUndefined();
    expect(tiles[0]!.kpi!.dateField).toBeUndefined();
  });

  it("M3: a MoM-worded question still defers to a usable auto-paired delta column", () => {
    // "mom" is not yet builder-rendered (see comparison.ts's scoping note),
    // so an existing, usable auto-paired delta stays preferable to
    // rendering nothing — only a computable YoY outranks the auto-paired
    // column.
    const all = classifyFields(FIELDS_WITH_EXPLICIT_DELTA);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Sales month over month");
    expect(tiles[0]!.kpi!.deltaMeasure).toBe("Sales Difference");
    expect(tiles[0]!.kpi!.comparisonKind).toBeUndefined();
    expect(tiles[0]!.kpi!.dateField).toBeUndefined();
  });

  it("defaults questionText to '' and still resolves YoY when a date dim exists", () => {
    const all = classifyFields(FIELDS_NO_DELTA_WITH_DATE);
    const tiles = buildKpiStrip(["Sales"], all, 4);
    expect(tiles[0]!.kpi!.comparisonKind).toBe("yoy");
  });

  it("is deterministic across repeated calls with identical input", () => {
    const all = classifyFields(FIELDS_NO_DELTA_WITH_DATE);
    const first = buildKpiStrip(["Sales", "Profit"], all, 4, "Sales year over year");
    const second = buildKpiStrip(["Sales", "Profit"], all, 4, "Sales year over year");
    expect(second).toEqual(first);
  });
});

describe("generatePlan — exec KPI band carries the computed comparison end to end", () => {
  it("an exec plan's KPI tile carries comparisonKind/dateField when the data has no delta column", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is revenue trending year over year?",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Order Date", dataType: "date" },
        { name: "Region", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    });
    const kpiTile = plan.sheets.find((s) => s.kind === "kpi_tile");
    expect(kpiTile).toBeDefined();
    expect(kpiTile!.kpi!.comparisonKind).toBe("yoy");
    expect(kpiTile!.kpi!.dateField).toBe("Order Date");
  });

  it("M3 live-probe regression: exec Superstore-like plan with BOTH a *_Difference column AND Order Date picks YoY, not the paired column", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing across categories and states?",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "PP Sales", dataType: "number" },
        { name: "Sales Difference", dataType: "number" },
        { name: "Order Date", dataType: "date" },
        { name: "Region", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Test",
    });
    const salesTile = plan.sheets.find(
      (s) => s.kind === "kpi_tile" && s.kpi?.primaryMeasure === "Sales",
    );
    expect(salesTile).toBeDefined();
    expect(salesTile!.kpi!.comparisonKind).toBe("yoy");
    expect(salesTile!.kpi!.dateField).toBe("Order Date");
    expect(salesTile!.kpi!.deltaMeasure).toBeUndefined();
    expect(salesTile!.kpi!.comparisonMeasure).toBe("PP Sales");
  });

  it("an exec plan's KPI tile has no computed comparison when no date dimension exists", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "What is total revenue?",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Region", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Test",
    });
    const kpiTile = plan.sheets.find((s) => s.kind === "kpi_tile");
    expect(kpiTile).toBeDefined();
    expect(kpiTile!.kpi!.comparisonKind).toBeUndefined();
    expect(kpiTile!.kpi!.dateField).toBeUndefined();
  });
});
