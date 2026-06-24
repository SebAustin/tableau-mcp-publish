/**
 * Interview question bank (BI_DESIGN §7, normative).
 *
 * `design_dashboard(mode: "interview")` selects 3–7 questions from this
 * ordered bank deterministically based on which inputs are still unknown.
 */

import type { Audience } from "./schema.js";
import type { FieldHint } from "./fields.js";

export interface Question {
  id: string;
  question: string;
  hint?: string;
}

/** Full ordered question bank per BI_DESIGN §7. */
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
 * Select 3–7 interview questions from the bank based on what is unknown.
 *
 * Selection is deterministic given the same input.
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

  // Ensure the three unconditional minimums are present if we'd have < 3
  if (selected.size < 3) {
    for (const id of UNCONDITIONAL_IDS) {
      selected.add(id);
      if (selected.size >= 3) break;
    }
  }

  // Return in the canonical order of QUESTION_BANK, capped at 7
  return QUESTION_BANK.filter((q) => selected.has(q.id)).slice(0, 7);
}
