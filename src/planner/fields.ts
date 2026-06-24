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
 */

export type FieldRole = "measure" | "dimension" | "identifier" | "temporal" | "geographic";
export type CardinalityHint = "low" | "high" | "unknown";

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
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

/** Split a snake_case name into lower-case tokens. */
function tokenize(name: string): string[] {
  return name.toLowerCase().split("_").filter(Boolean);
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
 * Classify a single FieldHint following BI_DESIGN §1 steps A–D.
 *
 * Step A: dtype primary classification
 * Step B: name-pattern override (first match wins; overrides Step A)
 * Step C: cardinality heuristic
 * Step D: tie-breakers (numeric id → identifier; string measure-name → measure annotated)
 */
export function classifyField(hint: FieldHint): FieldClassification {
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

  // Step C — cardinality heuristic
  const cardinalityHint = inferCardinality(hint.name, tags);

  return {
    name: hint.name,
    role,
    tags,
    cardinalityHint,
    suppress,
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
