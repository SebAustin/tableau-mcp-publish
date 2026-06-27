/**
 * Interview question bank (BI_DESIGN §7, normative).
 *
 * `design_dashboard(mode: "interview")` selects 3–10 questions from this
 * ordered bank deterministically based on which inputs are still unknown.
 *
 * Phase-1 additions (Slice 4): expanded bank 7 → 10 questions.
 * - q_comparison: prior period vs prior year vs target
 * - q_geo_level: geographic granularity (country/region/state/city)
 * - q_branding: dashboard title, subtitle, brand color
 * - q_chart_pref: chart type preferences (optional)
 */

import type { Audience } from "./schema.js";
import type { FieldHint } from "./fields.js";

export interface Question {
  id: string;
  question: string;
  hint?: string;
}

/** Full ordered question bank (Phase-1: 10 questions). */
export const QUESTION_BANK: Question[] = [
  {
    id: "q_audience",
    question: "Who is the primary audience for this dashboard?",
    hint: "For example: executive leadership, data analyst team, operations/frontline staff, or a mixed group.",
  },
  {
    id: "q_goal",
    question: "What is the primary business question this dashboard should answer?",
    hint: "For example: 'How is revenue trending?' or 'Which regions are underperforming?'",
  },
  {
    id: "q_key_metric",
    question: "What is the one metric that matters most to the viewer?",
    hint: "For example: revenue, churn rate, order volume, customer count.",
  },
  {
    id: "q_time_frame",
    question:
      "Does this dashboard need to show data over time, or is a point-in-time snapshot sufficient?",
    hint: "Time-based views require a date or period field.",
  },
  {
    id: "q_dimensions",
    question: "What categories or segments should the data be broken down by?",
    hint: "For example: by region, by product, by customer segment, by status.",
  },
  {
    id: "q_filters",
    question: "Are there any filters or scope limitations that should be applied by default?",
    hint: "For example: current fiscal year only, specific region, active customers only.",
  },
  {
    id: "q_action",
    question: "What action or decision does the viewer take after looking at this dashboard?",
    hint: "For example: escalate an order, contact a customer, reallocate budget.",
  },
  // Phase-1 additions
  {
    id: "q_comparison",
    question: "What type of comparison is most useful — prior period, prior year, or vs. a target?",
    hint: "For example: 'current month vs last month', 'YTD vs prior YTD', 'actuals vs budget'. This determines the KPI delta columns used.",
  },
  {
    id: "q_geo_level",
    question: "What geographic level should the data be shown at — country, region, state, or city?",
    hint: "This controls whether the map shows countries, US states, or cities. Leave blank if no map is needed.",
  },
  {
    id: "q_branding",
    question:
      "What title and subtitle should appear on the dashboard, and is there a brand color to apply?",
    hint: "For example: title 'Executive Sales Review Q4 2024', subtitle 'Regional & Category Performance', brand color '#003087'.",
  },
];

/** IDs of unconditional minimum questions (always included if count < 3). */
const UNCONDITIONAL_IDS = new Set(["q_goal", "q_key_metric", "q_filters"]);

export interface InterviewInput {
  audience?: Audience;
  businessQuestion?: string;
  context?: string;
  fieldHints?: FieldHint[];
}

/**
 * Select 3–10 interview questions from the bank based on what is unknown.
 *
 * Selection is deterministic given the same input.
 *
 * Phase-1 inclusion rules for new questions:
 * - q_comparison: included when context does not already mention comparison/period/prior
 * - q_geo_level: included when fieldHints contain a geographic field or context mentions map/geo
 * - q_branding: included when context does not mention a title or brand
 * - q_chart_pref: always optional — NOT included by default (keeps selection deterministic)
 */
export function selectQuestions(input: InterviewInput): Question[] {
  const { audience, businessQuestion, context, fieldHints } = input;

  const selected = new Set<string>();

  // q_audience — included when audience is absent
  if (!audience) selected.add("q_audience");

  // q_goal — included when businessQuestion and directions both absent
  if (!businessQuestion) selected.add("q_goal");

  // q_key_metric — included when fieldHints absent or empty
  if (!fieldHints || fieldHints.length === 0) selected.add("q_key_metric");

  // q_time_frame — included when no temporal field in fieldHints
  const hasTemporalField =
    fieldHints?.some(
      (h) =>
        h.dataType === "date" ||
        /\b(date|day|month|quarter|year|week|timestamp|time|period|fiscal|fy|cy)\b/i.test(h.name),
    ) ?? false;
  if (!hasTemporalField) selected.add("q_time_frame");

  // q_dimensions — included when fieldHints absent or contains only measures
  const hasDimension =
    fieldHints?.some(
      (h) =>
        h.dataType === "string" ||
        h.dataType === "boolean" ||
        h.dataType === "date" ||
        h.role === "dimension",
    ) ?? false;
  if (!hasDimension) selected.add("q_dimensions");

  // q_filters — included when context doesn't mention filter/scope
  const contextMentionsFilter =
    context ? /filter|scope|limit|only|restrict/i.test(context) : false;
  if (!contextMentionsFilter) selected.add("q_filters");

  // q_action — included when audience is operational or context mentions action/decision
  const contextMentionsAction =
    context ? /action|decision|decide|escalate|contact|reallocate/i.test(context) : false;
  if (audience === "operational" || contextMentionsAction) selected.add("q_action");

  // Phase-1: q_comparison — included when context doesn't already mention period/comparison
  const contextMentionsComparison =
    context
      ? /prior\s+(period|year|month)|previous\s+(period|year|month)|vs\.?\s+target|versus\s+target|period\s+over\s+period|year\s+over\s+year|mom|yoy/i.test(context)
      : false;
  if (!contextMentionsComparison) selected.add("q_comparison");

  // Phase-1: q_geo_level — included when a geo field is present or context mentions map/geo
  const hasGeoField =
    fieldHints?.some(
      (h) =>
        /\b(state|country|city|region|zip|postal|province|territory|geoid)\b/i.test(h.name),
    ) ?? false;
  const contextMentionsMap =
    context ? /\b(map|geography|geographic|location|where|state|country|city)\b/i.test(context) : false;
  if (hasGeoField || contextMentionsMap) selected.add("q_geo_level");

  // Phase-1: q_branding — included when context doesn't mention a title or brand color
  const contextMentionsBranding =
    context ? /\b(title|subtitle|brand|color|colour|logo)\b/i.test(context) : false;
  if (!contextMentionsBranding) selected.add("q_branding");

  // Ensure the three unconditional minimums are present if we'd have < 3
  if (selected.size < 3) {
    for (const id of UNCONDITIONAL_IDS) {
      selected.add(id);
      if (selected.size >= 3) break;
    }
  }

  // Return in the canonical order of QUESTION_BANK, capped at 10
  return QUESTION_BANK.filter((q) => selected.has(q.id)).slice(0, 10);
}
