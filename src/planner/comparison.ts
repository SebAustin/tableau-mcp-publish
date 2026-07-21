/**
 * Comparison-period selection heuristic (an external Tableau MCP skill suite backlog #2, GAPS.md
 * Sec 4 — pairs with #1, the computed YoY delta calc).
 *
 * A pure function mapping a business question's natural-language cues
 * ("year over year", "month over month") plus the presence of a usable date
 * dimension to the comparison PERIOD a KPI tile's delta should use. This is
 * the planner-side half of closing ACCEPTANCE.md's documented NULL-delta
 * residual: a KPI tile can now request a COMPUTED comparison (built by the
 * sidecar as a real calculated column — see `twb_builder._append_computed_yoy_delta_calc`)
 * instead of requiring the caller to supply a pre-existing delta COLUMN in
 * the source data.
 *
 * Precedence vs. an auto-paired *_Difference/*_Delta column (M3 fix — live
 * probe finding)
 * -----------------------------------------------------------------------
 * `fields.ts`'s `findPeriodPair` auto-pairs a primary measure with a
 * same-named CP/PP/Difference column purely by NAME (e.g. "Sales" ->
 * "Sales Difference"). That is a heuristic, not a guarantee: the exact
 * Superstore dataset that motivated this backlog item auto-pairs "Sales" ->
 * "Sales Difference", and "Sales Difference" is itself 100% NULL — the
 * SAME broken-column problem this backlog item exists to fix. Deferring to
 * that auto-paired column (as an earlier version of `buildKpiStrip` did)
 * silently reproduces the exact NULL-delta bug being closed.
 *
 * A computed YoY, by contrast, is well-defined whenever a usable date
 * dimension exists — it needs no cooperation from an unreliable
 * name-matched column. So `buildKpiStrip` now gives a computable YoY
 * PRECEDENCE over an auto-paired delta column (not the reverse): auto-
 * pairing is only trusted as a fallback once YoY is not computable (no
 * usable date dimension). See `buildKpiStrip`'s own docstring for the full
 * four-tier precedence order.
 *
 * Scoping note (kept intentionally narrow, mirroring GAPS.md's own declined-
 * candidate discipline: no calc recipe without a proven, cited builder
 * shape to consume it):
 * - "yoy" is the only kind the BUILDER currently turns into a computed calc
 *   column (the data-relative `{ MAX(YEAR([date])) }` LOD recipe — see the
 *   builder's own docstring for why `YEAR(TODAY())` is wrong on historical
 *   data). "mom" is SELECTED here (so the heuristic and its wire contract
 *   are complete and tested) but the builder does not yet emit a computed
 *   MoM delta — a MoM row-level recipe was not part of this slice's cited
 *   evidence and would be guessed, not proven; the KPI tile falls back to
 *   its existing primary-BAN-only behavior (no worse than before) until a
 *   future slice adds it.
 * - "vs target/goal" is NOT implemented: there is no target-measure
 *   detection anywhere in `fields.ts`, and no builder construct consumes a
 *   "target" comparison kind — adding one here would be dead weight (the
 *   same reasoning GAPS.md Sec 4's declined-candidates list already applies
 *   to other calc recipes with no matching builder support).
 */

import type { FieldClassification } from "./fields.js";
import { usableFields } from "./fields.js";

/** Comparison-period kind a KPI tile's delta can be computed against. */
export type ComparisonKind = "yoy" | "mom" | "none";

export interface ComparisonSelection {
  kind: ComparisonKind;
  /** The date/temporal field driving the comparison. Absent when kind === "none". */
  dateField?: string;
}

const YOY_KEYWORDS = /\b(year over year|yoy|annual(?:ly)?)\b/i;
const MOM_KEYWORDS = /\b(month over month|mom)\b/i;

/**
 * Pick the comparison period for a KPI tile's computed delta.
 *
 * Deterministic and side-effect free: the same `(questionText,
 * allClassifications)` pair always returns the same selection.
 *
 * Decision order:
 * 1. No usable temporal field anywhere in the field list -> `{ kind: "none" }`
 *    (no computed delta is possible; the KPI tile stays primary-BAN-only,
 *    unchanged behavior).
 * 2. A usable temporal field exists -> pick the kind from question-text
 *    keywords (MoM cues -> "mom"; everything else, including explicit YoY
 *    cues and the no-keyword default, -> "yoy" — a date dimension being
 *    present is itself the signal that a year-over-year comparison is
 *    meaningful).
 *
 * @param questionText        The business question / directions string (may be empty).
 * @param allClassifications  Full classified field list (see `classifyFields`).
 */
export function selectComparisonPeriod(
  questionText: string,
  allClassifications: FieldClassification[],
): ComparisonSelection {
  const dateField = usableFields(allClassifications).find(
    (f) => f.role === "temporal" || f.tags.includes("temporal"),
  );
  if (!dateField) {
    return { kind: "none" };
  }
  if (MOM_KEYWORDS.test(questionText)) {
    return { kind: "mom", dateField: dateField.name };
  }
  if (YOY_KEYWORDS.test(questionText)) {
    return { kind: "yoy", dateField: dateField.name };
  }
  // Default: a date dimension exists and no more specific comparison was
  // requested — year-over-year is the most broadly meaningful default.
  return { kind: "yoy", dateField: dateField.name };
}
