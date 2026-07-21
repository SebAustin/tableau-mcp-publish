/**
 * Tests for M2 — computed comparison-period wiring on `buildKpiStrip` and
 * the exec KPI-band plan (an external Tableau MCP skill suite backlog #1/#2, GAPS.md Sec 4).
 *
 * Closes ACCEPTANCE.md's documented NULL-delta residual: a KPI tile whose
 * data carries no pre-existing delta COLUMN (the common case — e.g. the
 * Superstore CSV) now gets a COMPUTED comparison (`comparisonKind` +
 * `dateField`) instead of a permanently-NULL delta, whenever a usable date
 * dimension exists.
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

  it("an explicit pre-existing delta column always wins over the computed path", () => {
    const all = classifyFields(FIELDS_WITH_EXPLICIT_DELTA);
    const tiles = buildKpiStrip(["Sales"], all, 4, "Sales year over year");
    expect(tiles[0]!.kpi!.deltaMeasure).toBe("Sales Difference");
    expect(tiles[0]!.kpi!.comparisonMeasure).toBe("PP Sales");
    // The computed fields must stay unset — the data-driven delta already
    // satisfies the KPI tile.
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
