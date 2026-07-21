/**
 * Tests for `selectComparisonPeriod` (an external Tableau MCP skill suite backlog #2, GAPS.md
 * Sec 4) — the comparison-period selection heuristic that pairs with the
 * computed YoY delta calc (backlog #1).
 */

import { describe, it, expect } from "vitest";
import { classifyFields } from "../src/planner/fields.js";
import type { FieldHint } from "../src/planner/fields.js";
import { selectComparisonPeriod } from "../src/planner/comparison.js";

const FIELDS_WITH_DATE: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Order Date", dataType: "date" },
  { name: "Region", dataType: "string" },
];

const FIELDS_WITHOUT_DATE: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Region", dataType: "string" },
];

describe("selectComparisonPeriod", () => {
  it("returns kind='none' when no usable date dimension exists", () => {
    const all = classifyFields(FIELDS_WITHOUT_DATE);
    const selection = selectComparisonPeriod("Sales by region", all);
    expect(selection.kind).toBe("none");
    expect(selection.dateField).toBeUndefined();
  });

  it("defaults to YoY when a date dimension exists and no keyword is present", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    const selection = selectComparisonPeriod("Show total sales", all);
    expect(selection.kind).toBe("yoy");
    expect(selection.dateField).toBe("Order Date");
  });

  it("picks YoY explicitly for 'year over year' / 'YoY' / 'annual' keywords", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    expect(selectComparisonPeriod("Sales year over year", all).kind).toBe("yoy");
    expect(selectComparisonPeriod("YoY sales trend", all).kind).toBe("yoy");
    expect(selectComparisonPeriod("Annual sales performance", all).kind).toBe("yoy");
  });

  it("picks MoM for 'month over month' / 'MoM' keywords", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    const monthOverMonth = selectComparisonPeriod("Sales month over month", all);
    expect(monthOverMonth.kind).toBe("mom");
    expect(monthOverMonth.dateField).toBe("Order Date");

    const mom = selectComparisonPeriod("MoM growth in sales", all);
    expect(mom.kind).toBe("mom");
  });

  it("does not false-positive MoM on unrelated words containing 'mom'", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    // "momentum" contains "mom" as a substring but not as a whole word.
    expect(selectComparisonPeriod("Sales momentum this quarter", all).kind).toBe("yoy");
  });

  it("is deterministic: same input always returns an equal selection", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    const first = selectComparisonPeriod("Sales year over year", all);
    const second = selectComparisonPeriod("Sales year over year", all);
    expect(second).toEqual(first);

    const noneFirst = selectComparisonPeriod("Sales by region", classifyFields(FIELDS_WITHOUT_DATE));
    const noneSecond = selectComparisonPeriod("Sales by region", classifyFields(FIELDS_WITHOUT_DATE));
    expect(noneSecond).toEqual(noneFirst);
  });

  it("handles empty question text gracefully (falls back to YoY default)", () => {
    const all = classifyFields(FIELDS_WITH_DATE);
    expect(selectComparisonPeriod("", all).kind).toBe("yoy");
  });
});
