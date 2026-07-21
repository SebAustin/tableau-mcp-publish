/**
 * Keyword → mark-type heuristic (BI_DESIGN §6, normative).
 *
 * Splits a business question or directions string on clause separators and
 * applies priority-ordered regexes to each clause to derive SheetSpec candidates.
 * Gap-fallback annotations (G-01..G-05) are attached to `rationale` where needed.
 *
 * Field inference (fields.ts) must run first; this module receives
 * `FieldClassification[]` rather than raw FieldHints.
 *
 * Phase-1 additions (Slice 4):
 * - scatter keyword → markType "scatter" (not the G-02 bar fallback)
 * - map keywords + geo field → markType "map_filled" with geo binding
 * - buildKpiStrip() helper: emits kpi_tile sheets from primary measures
 */

import type { FieldClassification, FieldRole, GeoRole } from "./fields.js";
import { findPeriodPair } from "./fields.js";
import type { MarkType, SheetKind } from "./schema.js";
import { selectComparisonPeriod } from "./comparison.js";

// ---------------------------------------------------------------------------
// Gap annotation strings (BI_DESIGN §2.3) — referenced by tests
// ---------------------------------------------------------------------------

export const GAP_G01 =
  "Color encoding not yet supported by the builder; apply color manually in Tableau.";
export const GAP_G02 =
  "Scatter plot requested; rendered as bar until builder adds Circle mark class.";
export const GAP_G03 = "Treemap not supported; rendered as bar.";
export const GAP_G04 = "Add reference lines manually in Tableau.";

/**
 * FEW-6 (BI_DESIGN §9, Few/visionary layer): the "N" in the Top-N filter
 * instruction below. Named so the G-05 rationale text and any future
 * enforcement (e.g. an actual builder-side Top-N filter) share a single
 * source of truth instead of a magic number repeated in prose.
 */
export const TOP_N_LIMIT = 10;

export const GAP_G05 =
  `High-cardinality dimension — apply Top ${TOP_N_LIMIT} filter in Tableau Desktop: right-click field > Filter > Top > By field.`;

// ---------------------------------------------------------------------------
// §6 — Priority-ordered keyword→markType table (Phase-1 update)
//
// Priority 5 (map) is now evaluated properly → map_filled when geo field present
// Priority 6 (scatter/drives) now routes to "scatter" instead of the G-02 bar fallback
// ---------------------------------------------------------------------------

type RichMarkType = MarkType; // "bar"|"line"|"text"|"map"|"scatter"|"map_filled"

interface MarkRule {
  priority: number;
  regex: RegExp;
  markType: RichMarkType;
  /** For the old scatter-as-bar path (G-02) — still used when no measure pair. */
  gapAnnotation?: string;
}

const MARK_RULES: MarkRule[] = [
  {
    priority: 1,
    regex: /\b(top\s+\d+|list|table|detail|breakdown|show me all|all records)\b/i,
    markType: "text",
  },
  {
    priority: 2,
    regex:
      /\b(trend|over time|by (month|quarter|year|date|day|week|period)|growth|historical|time series|trajectory)\b/i,
    markType: "line",
  },
  {
    priority: 3,
    regex:
      /\b(kpi|headline|total|grand total|overall|how many|count of|sum of|single number|big number)\b/i,
    markType: "text",
  },
  {
    priority: 4,
    regex:
      /\b(compare|comparison|versus|vs\.?|by (region|category|segment|channel|product|team|country|state|city|type|status|stage|tier|group)|across|per|distribution|breakdown by|rank)\b/i,
    markType: "bar",
  },
  {
    priority: 5,
    regex:
      /\b(map|geography|geographic|location|where|by country|by state|by city|by region \(on map\)|spatial)\b/i,
    markType: "map_filled", // Phase-1: upgraded from "map" when geo field present
  },
  {
    priority: 6,
    regex:
      /\b(correlation|scatter|relationship between|drives|impact of|x vs y|plotted against)\b/i,
    markType: "scatter", // Phase-1: upgraded from bar+G-02; audience guard in audience.ts
  },
  {
    priority: 7,
    regex: /\b(share|proportion|composition|part.?to.?whole|breakdown of .+%|percentage of)\b/i,
    markType: "bar",
  },
];

// ---------------------------------------------------------------------------
// Clause splitting
// ---------------------------------------------------------------------------

/** Split a question/directions string into clauses on AND/comma/semicolon. */
function splitClauses(text: string): string[] {
  return text
    .split(/\s+and\s+|,|;/i)
    .map((s) => s.trim())
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// RawSheet — full rich type including Phase-1 encoding blocks
// ---------------------------------------------------------------------------

export interface RawSheet {
  title: string;
  markType: MarkType;
  cols: string[];
  rows: string[];
  measures: string[];
  rationale?: string;
  /** Phase-1 additions */
  kind?: SheetKind;
  color?: { field: string; kind: "dimension" | "measure_names" | "measure" };
  kpi?: {
    primaryMeasure: string;
    comparisonMeasure?: string;
    deltaMeasure?: string;
    deltaIsPositiveGood?: boolean;
    sparklineField?: string;
    /**
     * Comparison-period kind for a COMPUTED delta (external skill backlog #1/#2,
     * GAPS.md Sec 4) — set only when no pre-existing `deltaMeasure` COLUMN
     * exists in the data and a usable date dimension does. "yoy" is
     * consumed by the sidecar builder as a real calculated-column
     * period-over-period delta; "mom" is carried on the wire but not yet
     * consumed (see `selectComparisonPeriod`'s docstring for why).
     */
    comparisonKind?: "yoy" | "mom";
    /** The date/temporal field driving a computed `comparisonKind` delta. */
    dateField?: string;
  };
  scatter?: { x: string; y: string; breakdown?: string };
  geo?: { geoField: string; geoRole: GeoRole; colorMeasure?: string };
}

// ---------------------------------------------------------------------------
// Shelf assignment
// ---------------------------------------------------------------------------

/**
 * Select shelf fields for a given markType from classified fields.
 *
 * Returns partial RawSheet fields (no title or rationale yet).
 */
function assignShelves(
  markType: MarkType,
  fields: FieldClassification[],
): Pick<RawSheet, "cols" | "rows" | "measures" | "scatter" | "geo" | "color"> {
  const usable = fields.filter((f) => !f.suppress);
  const temporal = usable.find((f) => f.role === "temporal" || f.tags.includes("temporal"));
  const geo = usable.find(
    (f) => f.role === "geographic" && (f.tags.includes("geo_named") || f.tags.includes("geo_coordinate")),
  );
  const measures = usable.filter((f) => f.role === "measure").map((f) => f.name);
  const nonTemporalDims = usable.filter(
    (f) =>
      (f.role === "dimension" || f.role === "geographic") &&
      !f.tags.includes("temporal"),
  );

  switch (markType) {
    case "line":
      return {
        cols: temporal ? [temporal.name] : [],
        rows: [],
        measures: measures.slice(0, 1),
      };

    case "map":
      return {
        cols: [],
        rows: geo ? [geo.name] : [],
        measures: measures.slice(0, 1),
      };

    case "map_filled": {
      // Use the geo field with geo binding; color by the first measure
      const geoField = geo;
      const colorMeasure = measures[0];
      return {
        cols: [],
        rows: geoField ? [geoField.name] : [],
        measures: colorMeasure ? [colorMeasure] : [],
        ...(geoField && geoField.geoRole !== undefined
          ? {
              geo: {
                geoField: geoField.name,
                geoRole: geoField.geoRole,
                colorMeasure,
              },
            }
          : {}),
        ...(colorMeasure
          ? { color: { field: colorMeasure, kind: "measure" as const } }
          : {}),
      };
    }

    case "scatter": {
      // Assign x=measures[0], y=measures[1]; breakdown=first dim if available
      const x = measures[0];
      const y = measures[1] ?? measures[0]; // fallback same measure if only one
      const breakdown = nonTemporalDims[0]?.name;
      return {
        cols: [],
        rows: [],
        measures: [x, y].filter((m): m is string => m !== undefined),
        ...(x && y
          ? {
              scatter: {
                x,
                y,
                ...(breakdown ? { breakdown } : {}),
              },
            }
          : {}),
      };
    }

    case "text": {
      // KPI: no dim, first measure
      const firstDim = nonTemporalDims[0];
      return {
        cols: [],
        rows: firstDim ? [firstDim.name] : [],
        measures: measures.slice(0, 1),
      };
    }

    case "bar":
    default: {
      const firstDim = nonTemporalDims[0];
      // Color by the second dimension if available (C-09 pattern)
      const colorDim = nonTemporalDims[1];
      return {
        cols: firstDim ? [firstDim.name] : [],
        rows: [],
        measures: measures.slice(0, 1),
        ...(colorDim
          ? { color: { field: colorDim.name, kind: "dimension" as const } }
          : {}),
      };
    }
  }
}

// ---------------------------------------------------------------------------
// No-dimension downgrade (BI_DESIGN §6 final clause)
// ---------------------------------------------------------------------------

/**
 * If markType is bar/line but no usable dimension exists, downgrade to text (KPI).
 * If the resulting text mark has no measures, drop it entirely (invariant §0.2).
 *
 * scatter and map_filled are NOT downgraded here — they go through the audience
 * guard in audience.ts instead.
 */
function applyNoDimensionDowngrade(
  sheet: RawSheet,
  fields: FieldClassification[],
): RawSheet | null {
  const usable = fields.filter((f) => !f.suppress);
  const dimensionRoles: FieldRole[] = ["dimension", "temporal", "geographic"];
  const hasDimension = usable.some(
    (f) => dimensionRoles.includes(f.role) && !f.suppress,
  );

  if ((sheet.markType === "bar" || sheet.markType === "line") && !hasDimension) {
    const downgraded: RawSheet = {
      ...sheet,
      markType: "text",
      cols: [],
      rows: [],
      color: undefined,
      rationale:
        (sheet.rationale ? sheet.rationale + " " : "") +
        "No usable dimension found; downgraded from " +
        sheet.markType +
        " to text (KPI).",
    };
    // Drop if no measure either (invariant §0.2)
    if (downgraded.measures.length === 0) return null;
    return downgraded;
  }
  // Drop measureless text marks (invariant §0.2)
  if (sheet.markType === "text" && sheet.measures.length === 0) return null;
  return sheet;
}

// ---------------------------------------------------------------------------
// G-05 high-cardinality annotation
// ---------------------------------------------------------------------------

function annotateHighCardinality(sheet: RawSheet, fields: FieldClassification[]): RawSheet {
  const allDimFields = [...sheet.cols, ...sheet.rows];
  const hasHighCard = allDimFields.some((name) => {
    const fc = fields.find((f) => f.name === name);
    return fc?.cardinalityHint === "high" || fc?.tags.includes("high_cardinality");
  });
  if (hasHighCard) {
    return {
      ...sheet,
      rationale: (sheet.rationale ? sheet.rationale + " " : "") + GAP_G05,
    };
  }
  return sheet;
}

// ---------------------------------------------------------------------------
// FEW-7 — small-multiples hint (BI_DESIGN §9, Few/visionary layer)
//
// When a bar sheet is colored by a second low-cardinality dimension (the C-09
// "2 dims + 1 measure" pattern) AND the business question / directions text
// signals an explicit comparison across dimensions, note the small-multiples
// alternative (one chart per category, instead of one color-coded bar) in the
// sheet's rationale. Documentational only: no new mark type is emitted and no
// existing shelf assignment changes.
// ---------------------------------------------------------------------------

const COMPARISON_ACROSS_RE = /\b(compare|comparison|versus|vs\.?|across|breakdown by|broken down by)\b/i;

export const FEW_07_SMALL_MULTIPLES_NOTE =
  "Two dimensions detected — consider small multiples (one chart per category) as a " +
  "legibility alternative to a single color-coded bar (Few/visionary layer, FEW-7).";

/**
 * Append the FEW-7 small-multiples rationale note to `sheet` when it is a bar
 * colored by a second dimension AND `questionText` signals a cross-dimension
 * comparison. No-op otherwise. Generic over any sheet-shaped object so it can
 * be applied to both `RawSheet` (heuristic path) and `SheetSpec` (exec KPI-band
 * path) without a type-widening cast at the call site.
 */
export function appendSmallMultiplesHintIfApplicable<
  T extends { markType: MarkType; color?: { kind: string }; rationale?: string },
>(sheet: T, questionText: string): T {
  const isBarWithSecondDimensionColor = sheet.markType === "bar" && sheet.color?.kind === "dimension";
  if (!isBarWithSecondDimensionColor) return sheet;
  if (!COMPARISON_ACROSS_RE.test(questionText)) return sheet;

  return {
    ...sheet,
    rationale: (sheet.rationale ? sheet.rationale + " " : "") + FEW_07_SMALL_MULTIPLES_NOTE,
  };
}

// ---------------------------------------------------------------------------
// Descriptive sheet title derivation
// ---------------------------------------------------------------------------

/**
 * Produce a human-readable sheet title from the bound shelf fields.
 *
 * Priority:
 *   1. measure + dimension → "Measure by Dimension" (e.g. "Revenue by Region")
 *   2. measure + temporal  → "Measure over Time"
 *   3. scatter → "X vs Y"
 *   4. map_filled → "Measure by GeoField"
 *   5. measure only        → the measure name (KPI title)
 *   6. dimension only      → the dimension name
 *   7. clause fallback     → first 40 chars of the clause, title-cased
 *   8. numeric fallback    → "${baseTitle} ${idx+1}"
 */
function deriveSheetTitle(
  clause: string,
  shelves: Pick<RawSheet, "cols" | "rows" | "measures" | "scatter" | "geo">,
  fields: FieldClassification[],
  baseTitle: string,
  clauseIdx: number,
): string {
  const toLabel = (name: string): string =>
    name
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
      .trim();

  // Scatter: "X vs Y"
  if (shelves.scatter) {
    return `${toLabel(shelves.scatter.x)} vs ${toLabel(shelves.scatter.y)}`;
  }

  // Map: "Measure by GeoField"
  if (shelves.geo) {
    const colorLabel = shelves.geo.colorMeasure ? toLabel(shelves.geo.colorMeasure) : "Map";
    return `${colorLabel} by ${toLabel(shelves.geo.geoField)}`;
  }

  const firstMeasure = shelves.measures[0];
  const allDims = [...shelves.cols, ...shelves.rows];
  const firstDim = allDims[0];

  if (firstMeasure && firstDim) {
    const fc = fields.find((f) => f.name === firstDim);
    const isTemporal = fc?.role === "temporal" || fc?.tags.includes("temporal");
    return isTemporal
      ? `${toLabel(firstMeasure)} over Time`
      : `${toLabel(firstMeasure)} by ${toLabel(firstDim)}`;
  }
  if (firstMeasure) {
    return toLabel(firstMeasure);
  }
  if (firstDim) {
    return toLabel(firstDim);
  }
  // Clause-text fallback: trim, strip punctuation, title-case
  const clauseLabel = clause
    .replace(/[^a-zA-Z0-9\s]/g, "")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .slice(0, 40)
    .trim();
  if (clauseLabel.length > 0) return clauseLabel;
  return `${baseTitle} ${clauseIdx + 1}`;
}

// ---------------------------------------------------------------------------
// Phase-1: KPI-strip emitter
// ---------------------------------------------------------------------------

/** Measures that are "cost-like" (lower is better) → deltaIsPositiveGood = false. */
const COST_LIKE_MEASURES = new Set<string>([
  "Discount",
  "Returns",
  "Days to Ship",
  "Days To Ship",
  "Days_to_Ship",
]);

/**
 * Return true if the measure name is cost-like (lower is better, so a negative
 * delta is good).
 */
export function isCostLikeMeasure(name: string): boolean {
  // Exact match first
  if (COST_LIKE_MEASURES.has(name)) return true;
  // Lowercase pattern match
  const lower = name.toLowerCase();
  return (
    lower.includes("discount") ||
    lower.includes("return") ||
    lower.includes("days to ship") ||
    lower.includes("days_to_ship") ||
    lower.includes("cost") ||
    lower.includes("churn")
  );
}

/**
 * Build a KPI-strip: one `kpi_tile` RawSheet per primary measure.
 *
 * Rules:
 * - Only primary measures (not period_compare) are included.
 * - A PP/CP counterpart, when present, is always bound as `comparisonMeasure`
 *   (kept as a plain raw-field encoding regardless of the delta precedence
 *   below — see `twb_builder._append_kpi_ban_calc_column`'s docstring for
 *   why comparisonMeasure is never calc-backed).
 * - Cost-like measures (Discount, Returns, Days to Ship) get deltaIsPositiveGood=false.
 * - The strip is capped at maxTiles.
 *
 * Delta precedence (M3 fix — GAPS.md Sec 4, live-probe finding; see
 * `comparison.ts`'s docstring for the full rationale):
 * 1. A future user-explicit delta/comparison measure (not yet wired — no
 *    such input exists on `buildKpiStrip` today; reserved as the highest
 *    tier so a future caller-supplied override is never silently beaten by
 *    the tiers below).
 * 2. A COMPUTED YoY delta, whenever `selectComparisonPeriod` resolves to
 *    `"yoy"` (i.e. a usable date dimension exists) — this OUTRANKS an
 *    auto-paired `*_Difference`/`*_Delta` column, because auto-pairing is a
 *    NAME heuristic that can silently be empty (proven by the Superstore
 *    dataset itself: "Sales" auto-pairs to "Sales Difference", which is
 *    100% NULL — the exact bug this backlog item exists to fix). A
 *    computed YoY is always well-defined once computable.
 * 3. An auto-paired `*_Difference`/`*_Delta` column from the data — used
 *    only when YoY is NOT computable (no usable date dimension), or when
 *    the question's keyword signal resolved to `"mom"` (not yet builder-
 *    supported — see `comparison.ts` — so a usable auto-paired delta stays
 *    preferable to rendering nothing).
 * 4. No delta at all.
 *
 * @param primaryMeasures  Names of the primary measures to tile (not CP/PP/Diff).
 * @param allClassifications  Full field list including suppressed period_compare fields.
 * @param maxTiles  Maximum number of KPI tiles to produce (audience-driven cap).
 * @param questionText  Business question / directions string, used to pick a
 *   COMPUTED comparison kind (`selectComparisonPeriod`). Defaults to `""` (no
 *   keyword signal — falls back to the date-dimension-presence default of
 *   `selectComparisonPeriod`).
 */
export function buildKpiStrip(
  primaryMeasures: string[],
  allClassifications: FieldClassification[],
  maxTiles = 4,
  questionText = "",
): RawSheet[] {
  const toLabel = (name: string): string =>
    name
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
      .trim();

  return primaryMeasures.slice(0, maxTiles).map((measure) => {
    const pair = findPeriodPair(measure, allClassifications);
    const hasComparison = pair.pp !== undefined || pair.cp !== undefined;
    const autoPairedDelta = pair.diff;

    // external skill backlog #1/#2 (GAPS.md Sec 4), M3 precedence fix: a
    // computable YoY always wins over an auto-paired delta column — see
    // this function's own docstring ("Delta precedence") and
    // comparison.ts's docstring for the full rationale.
    const computed = selectComparisonPeriod(questionText, allClassifications);

    let deltaMeasure: string | undefined;
    let comparisonKind: "yoy" | "mom" | undefined;
    let dateField: string | undefined;

    if (computed.kind === "yoy") {
      comparisonKind = "yoy";
      dateField = computed.dateField;
    } else if (autoPairedDelta) {
      deltaMeasure = autoPairedDelta;
    } else if (computed.kind === "mom") {
      comparisonKind = "mom";
      dateField = computed.dateField;
    }

    const kpi: RawSheet["kpi"] = {
      primaryMeasure: measure,
      ...(hasComparison ? { comparisonMeasure: pair.pp ?? pair.cp } : {}),
      ...(deltaMeasure ? { deltaMeasure } : {}),
      ...(comparisonKind ? { comparisonKind, dateField } : {}),
      deltaIsPositiveGood: !isCostLikeMeasure(measure),
    };

    const rationale = comparisonKind
      ? `KPI tile: ${measure} with computed ${comparisonKind.toUpperCase()} delta (${dateField}).`
      : deltaMeasure
        ? `KPI tile: ${measure} with period comparison (${pair.pp ?? pair.cp ?? "none"} / ${deltaMeasure}).`
        : `KPI tile: ${measure} (no period comparison columns found).`;

    return {
      title: toLabel(measure),
      markType: "text" as MarkType,
      kind: "kpi_tile" as SheetKind,
      cols: [],
      rows: [],
      measures: [measure],
      kpi,
      rationale,
    };
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Apply the BI_DESIGN §6 keyword heuristic to produce raw SheetSpec candidates.
 *
 * @param text           The businessQuestion or directions string.
 * @param fields         Classified field list (from classifyFields()).
 * @param baseTitle      Prefix for numeric-fallback auto-generated sheet titles.
 */
export function applyMarkHeuristic(
  text: string,
  fields: FieldClassification[],
  baseTitle = "Sheet",
): RawSheet[] {
  const clauses = splitClauses(text);
  const sheets: RawSheet[] = [];

  for (const [clauseIdx, clause] of clauses.entries()) {
    let markType: RichMarkType = "bar";
    // gapAnnotation retained for legacy G-02 when scatter not available
    // (currently not set because we now route scatter → "scatter")

    for (const rule of MARK_RULES) {
      if (rule.regex.test(clause)) {
        markType = rule.markType;
        break;
      }
    }

    const shelves = assignShelves(markType, fields);
    const rawSheet: RawSheet = {
      title: deriveSheetTitle(clause, shelves, fields, baseTitle, clauseIdx),
      markType,
      ...shelves,
    };

    // scatter/map_filled are not downgraded by the no-dimension rule
    if (markType === "scatter" || markType === "map_filled") {
      sheets.push(annotateHighCardinality(rawSheet, fields));
      continue;
    }

    // Apply no-dimension downgrade
    const maybeSheet = applyNoDimensionDowngrade(rawSheet, fields);
    if (!maybeSheet) continue;

    // Annotate high-cardinality dimensions (G-05)
    const annotated = annotateHighCardinality(maybeSheet, fields);
    // FEW-7: note the small-multiples alternative when applicable (§9)
    sheets.push(appendSmallMultiplesHintIfApplicable(annotated, text));
  }

  // If no sheets produced (e.g. empty text), produce one default bar
  if (sheets.length === 0) {
    const shelves = assignShelves("bar", fields);
    const defaultSheet: RawSheet = {
      title: deriveSheetTitle("", shelves, fields, baseTitle, 0),
      markType: "bar",
      ...shelves,
    };
    const maybeDefault = applyNoDimensionDowngrade(defaultSheet, fields);
    if (maybeDefault) {
      sheets.push(annotateHighCardinality(maybeDefault, fields));
    }
  }

  return sheets;
}
