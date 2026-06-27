/**
 * Field-role inference engine (BI_DESIGN §1).
 *
 * Classifies each FieldHint into a primary role plus secondary tags. This is a
 * pure function of (name, dataType) and runs as the first stage of the planning
 * pipeline — before chart selection.
 *
 * Pattern matching uses a token-based approach: snake_case field names are split
 * on underscores and each token is tested independently, which avoids the
 * well-known JavaScript \b-does-not-fire-on-underscore pitfall.
 *
 * Phase-1 additions (Slice 4):
 * - `period_compare` secondary tag for CP/PP/Difference columns
 * - `geoRole` annotation for geo fields (state/country/city/zipcode)
 * - Helper column suppression for Superstore-style computed fields
 */

export type FieldRole = "measure" | "dimension" | "identifier" | "temporal" | "geographic";
export type CardinalityHint = "low" | "high" | "unknown";

/** Geo role assigned to geographic fields for map_filled binding. */
export type GeoRole = "state" | "country" | "city" | "zipcode";

/**
 * Period-comparison tag for CP/PP/Difference columns.
 * These are never placed as standalone chart measures — they are used for KPI binding only.
 */
export type PeriodCompareBasis = "CP" | "PP" | "DIFF";

export interface PeriodCompareTag {
  basis: PeriodCompareBasis;
  /** Name of the base measure (e.g. "Sales" for "PP Sales"). */
  baseMeasure: string;
}

/** Raw hint supplied by the calling agent (may omit role and dataType). */
export interface FieldHint {
  name: string;
  role?: "dimension" | "measure";
  dataType?: "string" | "number" | "date" | "boolean";
}

/** Fully-classified field — output of classifyFields(). */
export interface FieldClassification {
  name: string;
  role: FieldRole;
  tags: string[];
  cardinalityHint: CardinalityHint;
  /** When true the field must not appear on any shelf (Rows/Cols/Color/measures). */
  suppress: boolean;
  /**
   * Geo role for geographic fields. Present when role === "geographic" and the
   * field maps to a Tableau geo role (state, country, city, zipcode).
   */
  geoRole?: GeoRole;
  /**
   * Period-compare metadata. Present when the field is a CP/PP/Difference variant.
   * Such fields must NOT be placed as standalone chart measures; they bind only
   * in KPI kpi{comparisonMeasure, deltaMeasure}.
   */
  periodCompare?: PeriodCompareTag;
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

/** Split a snake_case or space-separated name into lower-case tokens. */
function tokenize(name: string): string[] {
  return name
    .toLowerCase()
    .split(/[_\s]+/)
    .filter(Boolean);
}

/**
 * Return true if any token in `name` matches `pattern`, or if the full lower-case
 * name matches `pattern`.  Use anchored patterns (^...$) on tokens for precision.
 */
function tokensMatch(pattern: RegExp, name: string): boolean {
  const lower = name.toLowerCase();
  if (pattern.test(lower)) return true;
  const tokens = tokenize(name);
  return tokens.some((t) => pattern.test(t));
}

// ---------------------------------------------------------------------------
// Phase-1: Helper-column suppression patterns
//
// These columns are Superstore-style computed/parameter helper fields that must
// never appear on any shelf. They are suppressed before chart selection runs.
// ---------------------------------------------------------------------------

const HELPER_SUPPRESS_PATTERNS: RegExp[] = [
  // Axis label / prefix / suffix helpers (e.g. "Sales-Axis Label", "Profit Prefix")
  /-axis\s+label/i,
  /-axis\s+prefix/i,
  /-axis\s+suffix/i,
  /\baxis\s+label\b/i,
  /\baxis\s+prefix\b/i,
  /\baxis\s+suffix\b/i,
  // X-Axis / Y-Axis literal helpers
  /^x-axis$/i,
  /^y-axis$/i,
  /^x axis$/i,
  /^y axis$/i,
  // Map KPI helpers
  /\bmap\s+kpi/i,
  // Date Equalizer / Date Filter / Date Comparison
  /\bdate\s+equalizer/i,
  /\bdate\s+filter/i,
  /\bdate\s+comparison\b/i,
  // CLICK TO HIGHLIGHT / Region Filter / Region Parameter
  /\bclick\s+to\s+highlight/i,
  /\bregion\s+filter\b/i,
  /\bregion\s*=\s*region\s+parameter\b/i,
  // Filtered State
  /\bfiltered\s+state\b/i,
  // Scatter Plot Breakdown
  /\bscatter\s+plot\s+breakdown\b/i,
  // Days in Range
  /\bdays\s+in\s+range\b/i,
  // US Map Color
  /\bus\s+map\s+color\b/i,
];

/**
 * Return true if this field name matches a known helper-column pattern and
 * should be suppressed from all shelves.
 */
function isHelperColumn(name: string): boolean {
  return HELPER_SUPPRESS_PATTERNS.some((re) => re.test(name));
}

// ---------------------------------------------------------------------------
// Phase-1: Period-compare detection (CP / PP / Difference columns)
//
// Patterns:
//   "CP Sales"     → basis="CP",   baseMeasure="Sales"
//   "PP Sales"     → basis="PP",   baseMeasure="Sales"
//   "Sales Difference" → basis="DIFF", baseMeasure="Sales"
// ---------------------------------------------------------------------------

const CP_PREFIX_RE = /^CP\s+(.+)$/;
const PP_PREFIX_RE = /^PP\s+(.+)$/;
const DIFF_SUFFIX_RE = /^(.+)\s+Difference$/i;

function detectPeriodCompare(name: string): PeriodCompareTag | undefined {
  let m: RegExpMatchArray | null;

  m = name.match(CP_PREFIX_RE);
  if (m) return { basis: "CP", baseMeasure: m[1]!.trim() };

  m = name.match(PP_PREFIX_RE);
  if (m) return { basis: "PP", baseMeasure: m[1]!.trim() };

  m = name.match(DIFF_SUFFIX_RE);
  if (m) return { basis: "DIFF", baseMeasure: m[1]!.trim() };

  return undefined;
}

// ---------------------------------------------------------------------------
// Phase-1: Geo role detection
// ---------------------------------------------------------------------------

// Matched against the name with all separators removed (e.g. "Postal Code" →
// "postalcode"), so multi-word geo names resolve regardless of space/underscore/hyphen.
const GEO_ROLE_MAP: ReadonlyArray<{ regex: RegExp; role: GeoRole }> = [
  { regex: /^(state|province)$/i, role: "state" },
  { regex: /^(country|nation)$/i, role: "country" },
  { regex: /^(city|cityname|metro)$/i, role: "city" },
  { regex: /^(zip|zipcode|postal|postalcode|postcode)$/i, role: "zipcode" },
];

function detectGeoRole(name: string): GeoRole | undefined {
  const norm = name.toLowerCase().replace(/[_\s-]+/g, "");
  for (const { regex, role } of GEO_ROLE_MAP) {
    if (regex.test(norm)) return role;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// §1.2 — 8 priority-ordered name-pattern rules
//
// Patterns that use token-level anchoring are marked [token]; they are tested
// against each split token (e.g. "customer_id" → ["customer","id"]).
// Patterns that need the full name are marked [full]; they are tested against
// the whole lower-case string.
// ---------------------------------------------------------------------------

interface NamePattern {
  /** Tested against individual tokens (split on "_") if `tokenMatch` is true. */
  tokenMatch: boolean;
  regex: RegExp;
  role: FieldRole;
  tags: string[];
  suppress: boolean;
}

const NAME_PATTERNS: NamePattern[] = [
  // Priority 1 — identifiers [token]
  // Tokens: id, uuid, guid, key, pk, fk, code, sku, serial, token, hash
  {
    tokenMatch: true,
    regex: /^(id|uuid|guid|key|pk|fk|code|sku|serial|token|hash)$/i,
    role: "identifier",
    tags: ["high_cardinality", "suppress"],
    suppress: true,
  },
  // Priority 2 — temporal [token]
  {
    tokenMatch: true,
    regex: /^(date|day|month|quarter|year|week|timestamp|time|period|fiscal|fy|cy)$/i,
    role: "temporal",
    tags: ["temporal"],
    suppress: false,
  },
  // Priority 3 — geo coordinate [token]
  {
    tokenMatch: true,
    regex: /^(lat|latitude|lon|longitude|lng)$/i,
    role: "geographic",
    tags: ["geo_coordinate"],
    suppress: false,
  },
  // Priority 4 — geo named [token]
  {
    tokenMatch: true,
    regex: /^(country|nation|state|province|region|city|metro|zip|postal|postcode|geoid|territory)$/i,
    role: "geographic",
    tags: ["geo_named"],
    suppress: false,
  },
  // Priority 5 — low-cardinality dimension [token]
  {
    tokenMatch: true,
    regex:
      /^(name|label|title|description|desc|category|cat|type|status|stage|segment|group|tier|bucket|channel|source|medium|campaign)$/i,
    role: "dimension",
    tags: ["low_cardinality"],
    suppress: false,
  },
  // Priority 6 — boolean flags [token]
  // Includes prefix tokens (is, has, can, should) and boolean-valued tokens
  {
    tokenMatch: true,
    regex: /^(flag|is|has|can|should|active|enabled|deleted|archived|approved|published)$/i,
    role: "dimension",
    tags: ["boolean_flag"],
    suppress: false,
  },
  // Priority 7 — measures [token]
  {
    tokenMatch: true,
    regex:
      /^(revenue|sales|amount|total|sum|gross|net|profit|margin|cost|price|spend|budget|arr|mrr|gmv|ltv|aov|cac|volume|qty|quantity|count|units|orders|transactions|conversions|sessions|pageviews|clicks|impressions|rate|ratio|pct|percent|score|index|weight|value)$/i,
    role: "measure",
    tags: ["numeric"],
    suppress: false,
  },
  // Priority 8 — ordinal dimension [token]
  {
    tokenMatch: true,
    regex: /^(rank|ranking|position|priority|order|sequence)$/i,
    role: "dimension",
    tags: ["ordinal"],
    suppress: false,
  },
];

// ---------------------------------------------------------------------------
// §1.1 — dtype primary classification
// ---------------------------------------------------------------------------

function classifyByDtype(
  dataType: FieldHint["dataType"],
): Pick<FieldClassification, "role" | "tags"> {
  switch (dataType) {
    case "number":
      return { role: "measure", tags: [] };
    case "date":
      return { role: "temporal", tags: ["temporal"] };
    case "boolean":
      return { role: "dimension", tags: ["boolean_flag"] };
    case "string":
    default:
      return { role: "dimension", tags: [] };
  }
}

// ---------------------------------------------------------------------------
// §1.3 — cardinality heuristic
// ---------------------------------------------------------------------------

function inferCardinality(name: string, tags: string[]): CardinalityHint {
  if (tags.includes("suppress") || tokensMatch(/^(name|email|url|path|description|desc)$/i, name)) {
    return "high";
  }
  if (
    tags.includes("boolean_flag") ||
    tokensMatch(/^(status|stage|type|tier|channel|segment|region|country|state)$/i, name)
  ) {
    return "low";
  }
  return "unknown";
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Classify a single FieldHint following BI_DESIGN §1 steps A–D, plus
 * Phase-1 additions: helper-column suppression, period_compare detection,
 * and geoRole annotation.
 *
 * Step A: dtype primary classification
 * Step B: name-pattern override (first match wins; overrides Step A)
 * Step C: cardinality heuristic
 * Step D: tie-breakers (numeric id → identifier; string measure-name → measure annotated)
 * Phase-1 E: helper-column suppression (isHelperColumn → suppress=true)
 * Phase-1 F: period_compare detection (CP/PP/Difference → periodCompare tag)
 * Phase-1 G: geoRole annotation for geographic fields
 */
export function classifyField(hint: FieldHint): FieldClassification {
  // Phase-1 E — helper-column suppression (runs before any other rule)
  if (isHelperColumn(hint.name)) {
    return {
      name: hint.name,
      role: "identifier",
      tags: ["high_cardinality", "suppress", "helper_column"],
      cardinalityHint: "high",
      suppress: true,
    };
  }

  // Phase-1 F — period_compare detection
  // CP/PP columns → they become measures but tagged as period_compare; suppressed as
  // standalone shelves (they bind via kpi.comparisonMeasure/deltaMeasure only).
  const periodCompare = detectPeriodCompare(hint.name);
  if (periodCompare !== undefined) {
    return {
      name: hint.name,
      role: "measure",
      tags: ["numeric", "period_compare"],
      cardinalityHint: "unknown",
      suppress: true, // never placed on a standalone shelf
      periodCompare,
    };
  }

  // Step A — dtype
  const dtypeResult = classifyByDtype(hint.dataType);
  let role: FieldRole = dtypeResult.role;
  let tags: string[] = [...dtypeResult.tags];
  let suppress = false;

  // Step B — name-pattern overrides (first match wins)
  for (const pattern of NAME_PATTERNS) {
    const matched = pattern.tokenMatch
      ? tokensMatch(pattern.regex, hint.name)
      : pattern.regex.test(hint.name.toLowerCase());

    if (matched) {
      // Step D tie-breaker: numeric dtype + identifier name → identifier wins
      if (pattern.role === "identifier" && hint.dataType === "number") {
        role = "identifier";
        tags = [...pattern.tags];
        suppress = pattern.suppress;
        break;
      }

      // Step D tie-breaker: string dtype + measure name → measure, annotate
      if (pattern.role === "measure" && hint.dataType === "string") {
        role = "measure";
        tags = [...pattern.tags, "string_typed_measure"];
        suppress = false;
        break;
      }

      role = pattern.role;
      tags = [...pattern.tags];
      suppress = pattern.suppress;
      break;
    }
  }

  // Phase-1 G — geo override: a name that resolves to a known Tableau geo role
  // (State, Postal Code, …) is geographic even if a token like "code" matched the
  // identifier rule first (e.g. "Postal Code" → geographic/zipcode, not identifier).
  let geoRole: GeoRole | undefined = detectGeoRole(hint.name);
  if (geoRole !== undefined) {
    role = "geographic";
    tags = ["geo_named"];
    suppress = false;
  } else if (role === "geographic") {
    geoRole = detectGeoRole(hint.name);
  }

  // Step C — cardinality heuristic
  const cardinalityHint = inferCardinality(hint.name, tags);

  return {
    name: hint.name,
    role,
    tags,
    cardinalityHint,
    suppress,
    ...(geoRole !== undefined ? { geoRole } : {}),
  };
}

/**
 * Classify an array of FieldHints (BI_DESIGN §1).
 *
 * Returns classifications for all fields.  Callers should filter by
 * `suppress === false` before building shelves.
 */
export function classifyFields(hints: FieldHint[]): FieldClassification[] {
  return hints.map(classifyField);
}

/** Return only the non-suppressed (usable) fields from a classification list. */
export function usableFields(classifications: FieldClassification[]): FieldClassification[] {
  return classifications.filter((f) => !f.suppress);
}

/**
 * Return the CP, PP, and Difference field names for a given base measure name,
 * searching the full classification list (including suppressed period_compare fields).
 *
 * Returns undefined for each slot that is not present in the field list.
 */
export function findPeriodPair(
  baseMeasure: string,
  all: FieldClassification[],
): { cp?: string; pp?: string; diff?: string } {
  const result: { cp?: string; pp?: string; diff?: string } = {};
  for (const fc of all) {
    if (!fc.periodCompare) continue;
    if (fc.periodCompare.baseMeasure !== baseMeasure) continue;
    if (fc.periodCompare.basis === "CP") result.cp = fc.name;
    else if (fc.periodCompare.basis === "PP") result.pp = fc.name;
    else if (fc.periodCompare.basis === "DIFF") result.diff = fc.name;
  }
  return result;
}
