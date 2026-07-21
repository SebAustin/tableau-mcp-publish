import { describe, it, expect } from "vitest";
import { deriveMetricDictionary, type DatasourceField } from "../src/planner/metricDict.js";

describe("deriveMetricDictionary", () => {
  it("classifies a numeric currency measure", () => {
    const f: DatasourceField = { name: "Sales", dataType: "REAL", defaultAggregation: "SUM" };
    const { metrics } = deriveMetricDictionary([f]);
    expect(metrics[0]).toMatchObject({
      name: "Sales",
      role: "measure",
      suggestedAggregation: "SUM",
      format: "currency",
    });
    expect(metrics[0]?.definitionTemplate).toContain("measure");
    expect(metrics[0]?.definitionTemplate).toContain("currency");
  });

  it("classifies a percent measure by name hint", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Profit Margin", dataType: "REAL" }]);
    expect(metrics[0]?.format).toBe("percent");
    expect(metrics[0]?.role).toBe("measure");
  });

  it("classifies a plain numeric measure as number", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Quantity", dataType: "INTEGER" }]);
    expect(metrics[0]).toMatchObject({ role: "measure", format: "number" });
  });

  it("classifies a string dimension as text", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Region", dataType: "STRING" }]);
    expect(metrics[0]).toMatchObject({ role: "dimension", format: "text" });
    expect(metrics[0]?.suggestedAggregation).toBeUndefined();
  });

  it("classifies a date field as role=date", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Order Date", dataType: "DATE" }]);
    expect(metrics[0]?.role).toBe("date");
    expect(metrics[0]?.format).toBe("text");
  });

  it("falls back to SUM for a measure with no defaultAggregation", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Revenue", dataType: "REAL" }]);
    expect(metrics[0]?.suggestedAggregation).toBe("SUM");
  });

  it("prefers an explicit defaultAggregation over the SUM fallback", () => {
    const { metrics } = deriveMetricDictionary([
      { name: "Price", dataType: "REAL", defaultAggregation: "AVG" },
    ]);
    expect(metrics[0]?.suggestedAggregation).toBe("AVG");
  });

  it("uses caption in the definition template when present", () => {
    const { metrics } = deriveMetricDictionary([
      { name: "sum_sales", caption: "Total Sales", dataType: "REAL" },
    ]);
    expect(metrics[0]?.definitionTemplate.startsWith("Total Sales")).toBe(true);
  });

  it("returns a valid empty envelope for no fields", () => {
    expect(deriveMetricDictionary([])).toEqual({ metrics: [], count: 0 });
  });

  it("preserves input order and is deterministic", () => {
    const fields: DatasourceField[] = [
      { name: "Region", dataType: "STRING" },
      { name: "Sales", dataType: "REAL" },
      { name: "Order Date", dataType: "DATE" },
    ];
    const a = deriveMetricDictionary(fields);
    const b = deriveMetricDictionary(fields);
    expect(a).toEqual(b);
    expect(a.metrics.map((m) => m.name)).toEqual(["Region", "Sales", "Order Date"]);
    expect(a.count).toBe(3);
  });

  it("handles an unknown dataType without crashing", () => {
    const { metrics } = deriveMetricDictionary([{ name: "Mystery", dataType: "SPATIAL" }]);
    expect(metrics[0]?.name).toBe("Mystery");
    expect(["measure", "dimension", "date"]).toContain(metrics[0]?.role);
  });
});
