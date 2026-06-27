/**
 * Slice 4 planner tests — Superstore-grade plan generation.
 *
 * All tests are purely deterministic — no LLM calls, no network.
 * Covers:
 * - CP/PP/Difference period_compare classification and KPI binding
 * - geoRole detection for State, Country, City, Postal Code
 * - Helper column suppression
 * - buildKpiStrip with correct field pairing
 * - deltaIsPositiveGood direction for cost-like measures
 * - Exec plan shape: kpi_band_over_charts + map_filled + color bar
 * - No helper columns on any shelf
 * - Determinism: same input → identical plan
 * - Questions expanded to 3–10 (Phase-1 bank)
 * - scatter/map_filled allowed for analyst, blocked for exec → bar fallback
 */

import { describe, it, expect } from "vitest";
import { classifyField, classifyFields, findPeriodPair } from "../src/planner/fields.js";
import { buildKpiStrip, isCostLikeMeasure, applyMarkHeuristic } from "../src/planner/marks.js";
import { applyAudienceClamps } from "../src/planner/audience.js";
import { generatePlan, generateInterview } from "../src/planner/plan.js";
import type { FieldHint } from "../src/planner/fields.js";

// ---------------------------------------------------------------------------
// Superstore-like field list (~68 columns)
// ---------------------------------------------------------------------------

const SUPERSTORE_FIELDS: FieldHint[] = [
  // Real measures
  { name: "Sales", dataType: "number" },
  { name: "Profit", dataType: "number" },
  { name: "Profit Ratio", dataType: "number" },
  { name: "Quantity", dataType: "number" },
  { name: "Discount", dataType: "number" },
  { name: "Returns", dataType: "number" },
  { name: "Days to Ship", dataType: "number" },

  // Period-compare columns (CP/PP/Difference)
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
  { name: "CP Discount", dataType: "number" },
  { name: "PP Discount", dataType: "number" },
  { name: "Discount Difference", dataType: "number" },
  { name: "CP Days to Ship", dataType: "number" },
  { name: "PP Days to Ship", dataType: "number" },
  { name: "Days to Ship Difference", dataType: "number" },

  // Dimensions
  { name: "Category", dataType: "string" },
  { name: "Sub-Category", dataType: "string" },
  { name: "Segment", dataType: "string" },
  { name: "Region", dataType: "string" },
  { name: "State", dataType: "string" },
  { name: "Country", dataType: "string" },
  { name: "Ship Mode", dataType: "string" },
  { name: "Customer Name", dataType: "string" },
  { name: "Order Date", dataType: "date" },

  // Helper columns that must be suppressed
  { name: "Sales-Axis Label", dataType: "string" },
  { name: "Profit-Axis Label", dataType: "string" },
  { name: "Profit Ratio-Axis Prefix", dataType: "string" },
  { name: "X-Axis", dataType: "string" },
  { name: "Y-Axis", dataType: "string" },
  { name: "Map KPI Prefix", dataType: "string" },
  { name: "Map KPI Suffix", dataType: "string" },
  { name: "Date Equalizer Current Period", dataType: "string" },
  { name: "Date Filter CP", dataType: "string" },
  { name: "Date Filter PP", dataType: "string" },
  { name: "Date Comparison", dataType: "string" },
  { name: "CLICK TO HIGHLIGHT Region", dataType: "string" },
  { name: "Region = Region Parameter", dataType: "string" },
  { name: "Region Filter", dataType: "string" },
  { name: "Filtered State Region", dataType: "string" },
  { name: "Scatter Plot Breakdown", dataType: "string" },
  { name: "Days in Range", dataType: "number" },
  { name: "US Map Color", dataType: "number" },
];

// ---------------------------------------------------------------------------
// §1 — Period-compare classification
// ---------------------------------------------------------------------------

describe("fields — period_compare classification (Slice 4)", () => {
  it("'CP Sales' → role=measure, periodCompare={basis:'CP', baseMeasure:'Sales'}, suppress=true", () => {
    const fc = classifyField({ name: "CP Sales", dataType: "number" });
    expect(fc.role).toBe("measure");
    expect(fc.suppress).toBe(true);
    expect(fc.periodCompare).toEqual({ basis: "CP", baseMeasure: "Sales" });
    expect(fc.tags).toContain("period_compare");
  });

  it("'PP Profit Ratio' → basis PP, baseMeasure='Profit Ratio'", () => {
    const fc = classifyField({ name: "PP Profit Ratio", dataType: "number" });
    expect(fc.periodCompare?.basis).toBe("PP");
    expect(fc.periodCompare?.baseMeasure).toBe("Profit Ratio");
    expect(fc.suppress).toBe(true);
  });

  it("'Sales Difference' → basis DIFF, baseMeasure='Sales'", () => {
    const fc = classifyField({ name: "Sales Difference", dataType: "number" });
    expect(fc.periodCompare?.basis).toBe("DIFF");
    expect(fc.periodCompare?.baseMeasure).toBe("Sales");
    expect(fc.suppress).toBe(true);
  });

  it("'Days to Ship Difference' → basis DIFF, baseMeasure='Days to Ship'", () => {
    const fc = classifyField({ name: "Days to Ship Difference", dataType: "number" });
    expect(fc.periodCompare?.basis).toBe("DIFF");
    expect(fc.periodCompare?.baseMeasure).toBe("Days to Ship");
    expect(fc.suppress).toBe(true);
  });

  it("plain 'Sales' → no periodCompare, suppress=false", () => {
    const fc = classifyField({ name: "Sales", dataType: "number" });
    expect(fc.periodCompare).toBeUndefined();
    expect(fc.suppress).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §1.2 — geoRole annotation
// ---------------------------------------------------------------------------

describe("fields — geoRole annotation (Slice 4)", () => {
  it("'State' → role=geographic, geoRole='state'", () => {
    const fc = classifyField({ name: "State", dataType: "string" });
    expect(fc.role).toBe("geographic");
    expect(fc.geoRole).toBe("state");
  });

  it("'Country' → role=geographic, geoRole='country'", () => {
    const fc = classifyField({ name: "Country", dataType: "string" });
    expect(fc.role).toBe("geographic");
    expect(fc.geoRole).toBe("country");
  });

  it("'City' → role=geographic, geoRole='city'", () => {
    const fc = classifyField({ name: "City", dataType: "string" });
    expect(fc.role).toBe("geographic");
    expect(fc.geoRole).toBe("city");
  });

  it("'Postal Code' → role=geographic, geoRole='zipcode'", () => {
    const fc = classifyField({ name: "Postal Code", dataType: "string" });
    expect(fc.role).toBe("geographic");
    expect(fc.geoRole).toBe("zipcode");
  });
});

// ---------------------------------------------------------------------------
// §1 — Helper column suppression
// ---------------------------------------------------------------------------

describe("fields — helper column suppression (Slice 4)", () => {
  const helperNames = [
    "Sales-Axis Label",
    "Profit-Axis Label",
    "Profit Ratio-Axis Prefix",
    "X-Axis",
    "Y-Axis",
    "Map KPI Prefix",
    "Map KPI Suffix",
    "Date Equalizer Current Period",
    "Date Filter CP",
    "CLICK TO HIGHLIGHT Region",
    "Region = Region Parameter",
    "Region Filter",
    "Filtered State Region",
    "Scatter Plot Breakdown",
    "Days in Range",
    "US Map Color",
    "Date Comparison",
  ];

  for (const name of helperNames) {
    it(`'${name}' is suppressed`, () => {
      const fc = classifyField({ name, dataType: "string" });
      expect(fc.suppress).toBe(true);
    });
  }
});

// ---------------------------------------------------------------------------
// §2 — findPeriodPair
// ---------------------------------------------------------------------------

describe("findPeriodPair (Slice 4)", () => {
  it("finds PP Sales, Sales Difference for base 'Sales'", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const pair = findPeriodPair("Sales", all);
    expect(pair.pp).toBe("PP Sales");
    expect(pair.cp).toBe("CP Sales");
    expect(pair.diff).toBe("Sales Difference");
  });

  it("finds nothing for base 'Revenue' (not in Superstore list)", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const pair = findPeriodPair("Revenue", all);
    expect(pair.pp).toBeUndefined();
    expect(pair.cp).toBeUndefined();
    expect(pair.diff).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// buildKpiStrip
// ---------------------------------------------------------------------------

describe("buildKpiStrip (Slice 4)", () => {
  it("produces kpi_tile sheets with correct PP/Difference binding", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const primaryMeasures = ["Sales", "Profit", "Profit Ratio", "Quantity"];
    const tiles = buildKpiStrip(primaryMeasures, all, 4);

    expect(tiles.length).toBe(4);
    for (const tile of tiles) {
      expect(tile.kind).toBe("kpi_tile");
      expect(tile.markType).toBe("text");
      expect(tile.kpi).toBeDefined();
      expect(tile.kpi!.primaryMeasure).toBeTruthy();
      // All four have PP counterparts in Superstore
      expect(tile.kpi!.comparisonMeasure).toContain("PP ");
      expect(tile.kpi!.deltaMeasure).toContain("Difference");
    }

    // Sales tile
    const salesTile = tiles[0]!;
    expect(salesTile.kpi!.primaryMeasure).toBe("Sales");
    expect(salesTile.kpi!.comparisonMeasure).toBe("PP Sales");
    expect(salesTile.kpi!.deltaMeasure).toBe("Sales Difference");
    expect(salesTile.kpi!.deltaIsPositiveGood).toBe(true);
  });

  it("Discount tile → deltaIsPositiveGood=false (cost-like)", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const tiles = buildKpiStrip(["Discount"], all, 1);
    expect(tiles[0]!.kpi!.deltaIsPositiveGood).toBe(false);
  });

  it("Days to Ship tile → deltaIsPositiveGood=false (cost-like)", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const tiles = buildKpiStrip(["Days to Ship"], all, 1);
    expect(tiles[0]!.kpi!.deltaIsPositiveGood).toBe(false);
  });

  it("Returns tile → deltaIsPositiveGood=false (cost-like)", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const tiles = buildKpiStrip(["Returns"], all, 1);
    expect(tiles[0]!.kpi!.deltaIsPositiveGood).toBe(false);
  });

  it("Profit tile → deltaIsPositiveGood=true (good-when-up)", () => {
    const all = classifyFields(SUPERSTORE_FIELDS);
    const tiles = buildKpiStrip(["Profit"], all, 1);
    expect(tiles[0]!.kpi!.deltaIsPositiveGood).toBe(true);
  });

  it("strips without PP columns still produce tiles (graceful degradation)", () => {
    // Only primary measures, no CP/PP/Diff columns
    const simpleFields: FieldHint[] = [
      { name: "Revenue", dataType: "number" },
      { name: "Region", dataType: "string" },
    ];
    const all = classifyFields(simpleFields);
    const tiles = buildKpiStrip(["Revenue"], all, 1);
    expect(tiles.length).toBe(1);
    expect(tiles[0]!.kind).toBe("kpi_tile");
    expect(tiles[0]!.kpi!.primaryMeasure).toBe("Revenue");
    expect(tiles[0]!.kpi!.comparisonMeasure).toBeUndefined();
    expect(tiles[0]!.kpi!.deltaMeasure).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// isCostLikeMeasure
// ---------------------------------------------------------------------------

describe("isCostLikeMeasure (Slice 4)", () => {
  it("Discount → cost-like", () => expect(isCostLikeMeasure("Discount")).toBe(true));
  it("Returns → cost-like", () => expect(isCostLikeMeasure("Returns")).toBe(true));
  it("Days to Ship → cost-like", () => expect(isCostLikeMeasure("Days to Ship")).toBe(true));
  it("Sales → not cost-like", () => expect(isCostLikeMeasure("Sales")).toBe(false));
  it("Profit → not cost-like", () => expect(isCostLikeMeasure("Profit")).toBe(false));
  it("Quantity → not cost-like", () => expect(isCostLikeMeasure("Quantity")).toBe(false));
});

// ---------------------------------------------------------------------------
// Exec plan shape — Superstore-like input
// ---------------------------------------------------------------------------

describe("exec plan — Superstore-like input (Slice 4)", () => {
  const execPlan = () =>
    generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is sales performing across categories and states?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "superstore-ds",
      datasourceName: "Superstore",
      projectName: "Test",
    });

  it("plan has layoutGrammar.kind = 'kpi_band_over_charts'", () => {
    const plan = execPlan();
    expect(plan.layoutGrammar).toBeDefined();
    expect(plan.layoutGrammar!.kind).toBe("kpi_band_over_charts");
  });

  it("plan has kpiTileTitles and chartTitles in layoutGrammar", () => {
    const plan = execPlan();
    const lg = plan.layoutGrammar!;
    expect(Array.isArray(lg.kpiTileTitles)).toBe(true);
    expect(Array.isArray(lg.chartTitles)).toBe(true);
    expect(lg.kpiTileTitles!.length).toBeGreaterThan(0);
    expect(lg.chartTitles!.length).toBeGreaterThan(0);
  });

  it("plan has at least one kpi_tile sheet with valid kpi binding", () => {
    const plan = execPlan();
    const kpiTiles = plan.sheets.filter((s) => s.kind === "kpi_tile");
    expect(kpiTiles.length).toBeGreaterThan(0);
    for (const tile of kpiTiles) {
      expect(tile.kpi).toBeDefined();
      expect(tile.kpi!.primaryMeasure).toBeTruthy();
    }
  });

  it("KPI tile for Sales binds PP Sales and Sales Difference", () => {
    const plan = execPlan();
    const salesTile = plan.sheets.find(
      (s) => s.kind === "kpi_tile" && s.kpi?.primaryMeasure === "Sales",
    );
    expect(salesTile).toBeDefined();
    expect(salesTile!.kpi!.comparisonMeasure).toBe("PP Sales");
    expect(salesTile!.kpi!.deltaMeasure).toBe("Sales Difference");
    expect(salesTile!.kpi!.deltaIsPositiveGood).toBe(true);
  });

  it("plan has a map_filled sheet with geo.geoField='State'", () => {
    const plan = execPlan();
    const mapSheet = plan.sheets.find((s) => s.markType === "map_filled");
    expect(mapSheet).toBeDefined();
    expect(mapSheet!.geo).toBeDefined();
    expect(mapSheet!.geo!.geoField).toBe("State");
    expect(mapSheet!.geo!.geoRole).toBe("state");
  });

  it("plan has a color-encoded bar sheet", () => {
    const plan = execPlan();
    const colorBar = plan.sheets.find((s) => s.markType === "bar" && s.color !== undefined);
    expect(colorBar).toBeDefined();
  });

  it("plan has a dashboardTitle (non-empty string)", () => {
    const plan = execPlan();
    expect(plan.dashboardTitle).toBeTruthy();
    expect(typeof plan.dashboardTitle).toBe("string");
  });

  it("plan has a dashboardSubtitle", () => {
    const plan = execPlan();
    expect(plan.dashboardSubtitle).toBeTruthy();
  });

  it("no helper columns appear on any shelf", () => {
    const plan = execPlan();
    const helperPatterns = [
      /axis label/i,
      /axis prefix/i,
      /axis suffix/i,
      /^x-axis$/i,
      /^y-axis$/i,
      /map kpi/i,
      /date filter/i,
      /date equalizer/i,
      /click to highlight/i,
      /region filter/i,
      /region = region/i,
      /filtered state/i,
      /scatter plot breakdown/i,
      /days in range/i,
      /us map color/i,
    ];
    for (const sheet of plan.sheets) {
      const allFields = [...sheet.cols, ...sheet.rows, ...sheet.measures];
      for (const field of allFields) {
        for (const pattern of helperPatterns) {
          expect(
            pattern.test(field),
            `Helper column '${field}' found on shelf in sheet '${sheet.title}'`,
          ).toBe(false);
        }
      }
    }
  });

  it("no CP/PP/Difference columns appear as standalone shelf fields", () => {
    const plan = execPlan();
    for (const sheet of plan.sheets) {
      const allFields = [...sheet.cols, ...sheet.rows, ...sheet.measures];
      for (const field of allFields) {
        expect(field).not.toMatch(/^CP /);
        expect(field).not.toMatch(/^PP /);
        expect(field).not.toMatch(/ Difference$/);
      }
    }
  });

  it("exec audience: chart sheets ≤ maxSheets=3, kpi_tiles form the KPI band", () => {
    const plan = execPlan();
    // Phase-1: kpi_tile sheets form a visual band and are NOT capped by maxSheets.
    // Only the non-kpi_tile (chart) sheets are capped at 3.
    const chartSheets = plan.sheets.filter((s) => s.kind !== "kpi_tile");
    expect(chartSheets.length).toBeLessThanOrEqual(3);
    // At least some kpi_tile sheets must exist for the exec KPI band
    const kpiTiles = plan.sheets.filter((s) => s.kind === "kpi_tile");
    expect(kpiTiles.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Determinism — same input → identical plan
// ---------------------------------------------------------------------------

describe("determinism (Slice 4)", () => {
  it("exec Superstore plan is identical across two calls", () => {
    const input = {
      mode: "autonomous" as const,
      audience: "exec" as const,
      businessQuestion: "How is sales performing across categories and states?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "superstore-ds",
      datasourceName: "Superstore",
      projectName: "Test",
    };
    const plan1 = generatePlan(input);
    const plan2 = generatePlan(input);
    expect(JSON.stringify(plan1)).toBe(JSON.stringify(plan2));
  });

  it("analyst plan is identical across two calls", () => {
    const input = {
      mode: "autonomous" as const,
      audience: "analyst" as const,
      businessQuestion: "What drives profit by category and scatter of sales vs discount?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "superstore-ds",
      datasourceName: "Superstore",
      projectName: "Test",
    };
    const plan1 = generatePlan(input);
    const plan2 = generatePlan(input);
    expect(JSON.stringify(plan1)).toBe(JSON.stringify(plan2));
  });

  it("interview question selection is identical across two calls", () => {
    const opts = {
      audience: "exec" as const,
      businessQuestion: "How is revenue trending?",
      fieldHints: SUPERSTORE_FIELDS,
    };
    const q1 = generateInterview(opts);
    const q2 = generateInterview(opts);
    expect(JSON.stringify(q1)).toBe(JSON.stringify(q2));
  });
});

// ---------------------------------------------------------------------------
// Audience guards — scatter and map_filled routing
// ---------------------------------------------------------------------------

describe("audience guards — scatter / map_filled (Slice 4)", () => {
  it("scatter sheet survives for analyst audience", () => {
    // Use "correlation" keyword (priority 6) in a clause without "by X" (priority 4)
    // to avoid the priority 4 bar rule firing first.
    const plan = generatePlan({
      mode: "directed",
      audience: "analyst",
      directions: "correlation between sales and profit",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Profit", dataType: "number" },
        { name: "Category", dataType: "string" },
        { name: "State", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Test",
    });
    const hasScatter = plan.sheets.some((s) => s.markType === "scatter");
    expect(hasScatter).toBe(true);
  });

  it("scatter sheet is replaced with bar for exec audience", () => {
    const { sheets } = applyAudienceClamps(
      [
        {
          title: "Sales vs Profit",
          markType: "scatter",
          cols: [],
          rows: [],
          measures: ["Sales", "Profit"],
          scatter: { x: "Sales", y: "Profit" },
        },
      ],
      "exec",
    );
    const anyScatter = sheets.some((s) => s.markType === "scatter");
    expect(anyScatter).toBe(false);
  });

  it("map_filled sheet survives for analyst audience", () => {
    const { sheets } = applyAudienceClamps(
      [
        {
          title: "Sales by State",
          markType: "map_filled",
          cols: [],
          rows: ["State"],
          measures: ["Sales"],
          geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
        },
      ],
      "analyst",
    );
    const mapSheet = sheets.find((s) => s.markType === "map_filled");
    expect(mapSheet).toBeDefined();
  });

  it("map_filled sheet survives for exec audience (exec allows map_filled)", () => {
    const { sheets } = applyAudienceClamps(
      [
        {
          title: "Sales by State",
          markType: "map_filled",
          cols: [],
          rows: ["State"],
          measures: ["Sales"],
          geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
        },
      ],
      "exec",
    );
    // Exec allows map_filled — it should survive STEP 6
    const mapSheet = sheets.find((s) => s.markType === "map_filled");
    expect(mapSheet).toBeDefined();
  });

  it("map_filled sheet is replaced with bar for operational audience", () => {
    const { sheets } = applyAudienceClamps(
      [
        {
          title: "Sales by State",
          markType: "map_filled",
          cols: [],
          rows: ["State"],
          measures: ["Sales"],
          geo: { geoField: "State", geoRole: "state", colorMeasure: "Sales" },
        },
      ],
      "operational",
    );
    const anyMapFilled = sheets.some((s) => s.markType === "map_filled");
    expect(anyMapFilled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// kpi_tile sheets exempt from measure cap
// ---------------------------------------------------------------------------

describe("kpi_tile cap exemption (Slice 4)", () => {
  it("kpi_tile sheets are NOT truncated by audience measure cap", () => {
    const kpiSheet = {
      title: "Sales",
      markType: "text" as const,
      kind: "kpi_tile" as const,
      cols: [],
      rows: [],
      measures: ["Sales"],
      kpi: {
        primaryMeasure: "Sales",
        comparisonMeasure: "PP Sales",
        deltaMeasure: "Sales Difference",
        deltaIsPositiveGood: true,
      },
    };
    // Exec maxMeasures=1, but kpi_tile is exempt
    const { sheets } = applyAudienceClamps([kpiSheet], "exec");
    const tile = sheets.find((s) => s.kind === "kpi_tile");
    expect(tile).toBeDefined();
    // kpi block should be intact
    expect(tile!.kpi!.comparisonMeasure).toBe("PP Sales");
    expect(tile!.kpi!.deltaMeasure).toBe("Sales Difference");
  });
});

// ---------------------------------------------------------------------------
// Interview — question count 3–10
// ---------------------------------------------------------------------------

describe("interview questions — 3–10 (Slice 4)", () => {
  it("max 10 questions when nothing is known", () => {
    const result = generateInterview({});
    expect(result.questions.length).toBeGreaterThanOrEqual(3);
    expect(result.questions.length).toBeLessThanOrEqual(10);
  });

  it("q_comparison is in bank", () => {
    const result = generateInterview({});
    const ids = result.questions.map((q) => q.id);
    expect(ids).toContain("q_comparison");
  });

  it("q_geo_level included when geo field present", () => {
    const result = generateInterview({
      fieldHints: [
        { name: "State", dataType: "string" },
        { name: "Sales", dataType: "number" },
      ],
    });
    const ids = result.questions.map((q) => q.id);
    expect(ids).toContain("q_geo_level");
  });

  it("q_branding included when context has no branding info", () => {
    const result = generateInterview({ audience: "exec", businessQuestion: "Revenue by region" });
    const ids = result.questions.map((q) => q.id);
    expect(ids).toContain("q_branding");
  });

  it("q_comparison NOT included when context mentions prior period", () => {
    const result = generateInterview({
      audience: "exec",
      businessQuestion: "Revenue trend",
      context: "compare vs prior year YoY",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "Order Date", dataType: "date" },
        { name: "Region", dataType: "string" },
      ],
    });
    const ids = result.questions.map((q) => q.id);
    expect(ids).not.toContain("q_comparison");
  });
});

// ---------------------------------------------------------------------------
// map_filled routing via applyMarkHeuristic
// ---------------------------------------------------------------------------

describe("map_filled routing via mark heuristic (Slice 4)", () => {
  it("'geographic map' keyword + State geo field → map_filled mark with geo binding", () => {
    // Note: the clause must trigger priority 5 (map) before priority 4 (by X).
    // "geographic map of sales" avoids "by state" which would trigger priority 4 first.
    const fields = classifyFields([
      { name: "State", dataType: "string" },
      { name: "Sales", dataType: "number" },
      { name: "Region", dataType: "string" },
    ]);
    const sheets = applyMarkHeuristic("show geographic map of sales", fields);
    const mapSheet = sheets.find((s) => s.markType === "map_filled");
    expect(mapSheet).toBeDefined();
    expect(mapSheet!.geo).toBeDefined();
    expect(mapSheet!.geo!.geoField).toBe("State");
    expect(mapSheet!.geo!.geoRole).toBe("state");
  });

  it("buildExecKpiBandPlan includes a map_filled when State is present", () => {
    // The exec planner directly places a map_filled if a state geo field exists.
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "Sales performance overview",
      fieldHints: [
        { name: "Sales", dataType: "number" },
        { name: "PP Sales", dataType: "number" },
        { name: "Sales Difference", dataType: "number" },
        { name: "Category", dataType: "string" },
        { name: "State", dataType: "string" },
        { name: "Order Date", dataType: "date" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Test",
    });
    const mapSheet = plan.sheets.find((s) => s.markType === "map_filled");
    expect(mapSheet).toBeDefined();
    expect(mapSheet!.geo!.geoField).toBe("State");
    expect(mapSheet!.geo!.geoRole).toBe("state");
  });
});
