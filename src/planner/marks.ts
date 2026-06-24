/**
 * Keyword → mark-type heuristic (BI_DESIGN §6, normative).
 *
 * Splits a business question or directions string on clause separators and
 * applies priority-ordered regexes to each clause to derive SheetSpec candidates.
 * Gap-fallback annotations (G-01..G-05) are attached to `rationale` where needed.
 *
 * Field inference (fields.ts) must run first; this module receives
 * `FieldClassification[]` rather than raw FieldHints.
 */

import type { FieldClassification, FieldRole } from "./fields.js";
import type { MarkType } from "./schema.js";

// ---------------------------------------------------------------------------
// Gap annotation strings (BI_DESIGN §2.3) — referenced by tests
// ---------------------------------------------------------------------------

export const GAP_G01 =
  "Color encoding not yet supported by the builder; apply color manually in Tableau.";
export const GAP_G02 =
  "Scatter plot requested; rendered as bar until builder adds Circle mark class.";
export const GAP_G03 = "Treemap not supported; rendered as bar.";
export const GAP_G04 = "Add reference lines manually in Tableau.";
export const GAP_G05 =
  "High-cardinality dimension — apply Top 10 filter in Tableau Desktop: right-click field > Filter > Top > By field.";

// ---------------------------------------------------------------------------
// §6 — Priority-ordered keyword→markType table
// ---------------------------------------------------------------------------

interface MarkRule {
  priority: number;
  regex: RegExp;
  markType: MarkType;
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
    markType: "map",
  },
  {
    priority: 6,
    regex:
      /\b(correlation|scatter|relationship between|drives|impact of|x vs y|plotted against)\b/i,
    markType: "bar",
    gapAnnotation: GAP_G02,
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
// Shelf assignment
// ---------------------------------------------------------------------------

export interface RawSheet {
  title: string;
  markType: MarkType;
  cols: string[];
  rows: string[];
  measures: string[];
  rationale?: string;
}

/**
 * Select shelf fields for a given markType from classified fields.
 *
 * Returns partial RawSheet fields (no title or rationale yet).
 */
function assignShelves(
  markType: MarkType,
  fields: FieldClassification[],
): Pick<RawSheet, "cols" | "rows" | "measures"> {
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
      return {
        cols: firstDim ? [firstDim.name] : [],
        rows: [],
        measures: measures.slice(0, 1),
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
// Public API
// ---------------------------------------------------------------------------

/**
 * Apply the BI_DESIGN §6 keyword heuristic to produce raw SheetSpec candidates.
 *
 * @param text           The businessQuestion or directions string.
 * @param fields         Classified field list (from classifyFields()).
 * @param baseTitle      Prefix for auto-generated sheet titles.
 */
export function applyMarkHeuristic(
  text: string,
  fields: FieldClassification[],
  baseTitle = "Sheet",
): RawSheet[] {
  const clauses = splitClauses(text);
  const sheets: RawSheet[] = [];

  for (const [clauseIdx, clause] of clauses.entries()) {
    let markType: MarkType = "bar";
    let gapAnnotation: string | undefined;

    for (const rule of MARK_RULES) {
      if (rule.regex.test(clause)) {
        markType = rule.markType;
        gapAnnotation = rule.gapAnnotation;
        break;
      }
    }

    const shelves = assignShelves(markType, fields);
    const rawSheet: RawSheet = {
      title: `${baseTitle} ${clauseIdx + 1}`,
      markType,
      ...shelves,
      rationale: gapAnnotation,
    };

    // Apply no-dimension downgrade
    const maybeSheet = applyNoDimensionDowngrade(rawSheet, fields);
    if (!maybeSheet) continue;

    // Annotate high-cardinality dimensions (G-05)
    const annotated = annotateHighCardinality(maybeSheet, fields);
    sheets.push(annotated);
  }

  // If no sheets produced (e.g. empty text), produce one default bar
  if (sheets.length === 0) {
    const shelves = assignShelves("bar", fields);
    const defaultSheet: RawSheet = {
      title: `${baseTitle} 1`,
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
