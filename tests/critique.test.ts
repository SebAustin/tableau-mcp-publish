import { describe, it, expect } from "vitest";
import { critiqueDashboardPlan, type BusinessNorms, type CritiquePlan } from "../src/planner/critique.js";
import { loadBusinessNorms } from "../src/tools/critiqueDashboard.js";

/** A synthetic norm set with confident buckets, for controlled dimension tests. */
const NORMS: BusinessNorms = {
  layout_archetype: {
    buckets: { kpi_band_top: { count: 18, rate: 0.58 }, grid_of_charts: { count: 6, rate: 0.19 }, single_viz: { count: 1, rate: 0.03 } },
    n: 31,
    confidence: "ok",
  },
  ban_count: { median: 4, p25: 3, p75: 6, n: 32, confidence: "ok" },
  title_case: {
    buckets: { title_case: { count: 26, rate: 0.72 }, all_caps: { count: 7, rate: 0.19 } },
    n: 36,
    confidence: "ok",
  },
};

const goodPlan: CritiquePlan = {
  dashboardTitle: "Sales And Profit Performance",
  layoutGrammar: { kind: "kpi_band_over_charts" },
  sheets: [
    { kind: "kpi_tile", markType: "text", kpi: { comparisonKind: "yoy" } },
    { kind: "kpi_tile", markType: "text", kpi: { comparisonKind: "yoy" } },
    { kind: "kpi_tile", markType: "text", kpi: { comparisonKind: "yoy" } },
    { kind: "kpi_tile", markType: "text", kpi: { comparisonKind: "yoy" } },
    { kind: "chart", markType: "bar" },
  ],
  designTheme: {
    dashboardBackground: "#2f2e41",
    header: { titleColor: "#ffffff", background: "#2f2e41" },
    kpiTile: { banColor: "#ffffff", background: "#2f2e41" },
  },
};

describe("critiqueDashboardPlan", () => {
  it("scores a norm-aligned plan high with all passes", () => {
    const r = critiqueDashboardPlan(goodPlan, NORMS);
    expect(r.overallScore).toBeGreaterThanOrEqual(90);
    expect(r.dimensions.find((d) => d.dimension === "Layout")?.verdict).toBe("pass");
    expect(r.dimensions.find((d) => d.dimension === "KPI count")?.verdict).toBe("pass");
    expect(r.dimensions.find((d) => d.dimension === "Contrast")?.verdict).toBe("pass");
  });

  it("scores a bad plan low (off-pattern layout, too many KPIs, low contrast)", () => {
    const bad: CritiquePlan = {
      dashboardTitle: "ALL CAPS TITLE",
      layoutGrammar: { kind: "tiled_vertical" }, // grid_of_charts — still top-2, so warn comes elsewhere
      sheets: Array.from({ length: 9 }, () => ({ kind: "kpi_tile", markType: "text" })), // no comparison, count>p75
      designTheme: {
        dashboardBackground: "#ffffff",
        header: { titleColor: "#eeeeee", background: "#ffffff" }, // ~1.1:1
        kpiTile: { banColor: "#f5f5f5", background: "#ffffff" },
      },
    };
    const r = critiqueDashboardPlan(bad, NORMS);
    expect(r.overallScore).toBeLessThan(60);
    expect(r.dimensions.find((d) => d.dimension === "KPI count")?.verdict).toBe("warn");
    expect(r.dimensions.find((d) => d.dimension === "Chart mix")?.verdict).toBe("warn");
    expect(r.dimensions.find((d) => d.dimension === "Contrast")?.verdict).toBe("warn");
    expect(r.dimensions.find((d) => d.dimension === "Title")?.verdict).toBe("warn");
  });

  it("flags an off-pattern layout as warn", () => {
    const r = critiqueDashboardPlan({ ...goodPlan, layoutGrammar: { kind: "single_viz_freeform" } }, NORMS);
    expect(r.dimensions.find((d) => d.dimension === "Layout")?.verdict).toBe("warn");
  });

  it("degrades a dimension to 'note' when its norm bucket is low-confidence", () => {
    const lowConf: BusinessNorms = { ...NORMS, ban_count: { median: 4, p25: 3, p75: 6, n: 3, confidence: "low" } };
    const r = critiqueDashboardPlan(goodPlan, lowConf);
    expect(r.dimensions.find((d) => d.dimension === "KPI count")?.verdict).toBe("note");
  });

  it("emits 'note' (not a crash) when norms are entirely absent", () => {
    const r = critiqueDashboardPlan(goodPlan, {});
    expect(r.dimensions.find((d) => d.dimension === "Layout")?.verdict).toBe("note");
    // contrast still evaluable (no norm needed) → pass
    expect(r.dimensions.find((d) => d.dimension === "Contrast")?.verdict).toBe("pass");
  });

  it("warns when KPI tiles carry no period comparison (FEW-2)", () => {
    const noComp = { ...goodPlan, sheets: [{ kind: "kpi_tile", markType: "text" }, { kind: "kpi_tile", markType: "text" }, { kind: "kpi_tile", markType: "text" }] };
    const r = critiqueDashboardPlan(noComp, NORMS);
    expect(r.dimensions.find((d) => d.dimension === "Chart mix")?.verdict).toBe("warn");
  });

  it("is deterministic", () => {
    expect(critiqueDashboardPlan(goodPlan, NORMS)).toEqual(critiqueDashboardPlan(goodPlan, NORMS));
  });

  it("cites the mined stat with n and confidence", () => {
    const r = critiqueDashboardPlan(goodPlan, NORMS);
    const layout = r.dimensions.find((d) => d.dimension === "Layout");
    expect(layout?.normCited).toMatchObject({ stat: "layout_archetype", n: 31, confidence: "ok" });
  });
});

describe("loadBusinessNorms — reads the REAL committed corpus", () => {
  it("loads the business_dashboard stratum with confident layout + ban_count norms", () => {
    const norms = loadBusinessNorms();
    expect(norms.layout_archetype?.confidence).toBe("ok");
    expect(norms.layout_archetype?.buckets.kpi_band_top?.rate).toBeGreaterThan(0.4);
    expect(norms.ban_count?.median).toBeGreaterThan(0);
  });

  it("critiques the exec-Superstore-shaped plan sensibly against the real norms", () => {
    const norms = loadBusinessNorms();
    const r = critiqueDashboardPlan(goodPlan, norms);
    expect(r.overallScore).toBeGreaterThanOrEqual(80);
  });
});
