/**
 * Slice T2 (PLAN.md's Top-100 Corpus plan) — dashboard title auto-shorten.
 *
 * Beauty-gate round 2 verdict: "title too big". The mined evidence
 * (design/corpus/stats/dashboard_norms.yaml, T1 corpus mining, 110
 * workbooks) shows the pre-existing themed-header title FONTSIZE (20pt) was
 * already at/under the mined 900-1400-stratum median (22pt) — fontsize was
 * never the defect. The real driver was LENGTH: a long, question-derived
 * dashboardTitle (e.g. "How Are Sales And Profit Performing Across
 * Categories And States?") overflows the themed header zone. This file
 * covers the fix: `shortenDashboardTitle` + its wiring into `generatePlan`.
 *
 * Covers:
 * - `shortenDashboardTitle()`: measure-noun extraction -> "{Measure(s)}
 *   Performance"; fallback to word-boundary truncation when no plan measure
 *   is mentioned in the question.
 * - `generatePlan()` end-to-end: a long business question yields a
 *   shortened `dashboardTitle` + the FULL question as `dashboardSubtitle`; a
 *   short question is left untouched (byte-identical to pre-T2 behavior).
 * - The exact demo-question example from the plan's evidence section
 *   ("How Are Sales And Profit Performing Across Categories And States?").
 * - CI gate: re-parses the COMMITTED design/corpus/stats/dashboard_norms.yaml
 *   at test time. There is currently NO mined title-length bucket in that
 *   file (only title_fontsize / title_height_ratio were mined in T1) — this
 *   test asserts that premise explicitly and, if a future corpus refresh
 *   DOES add one, prefers its p75 over the hardcoded 40-char judgment call,
 *   so norm drift is caught here rather than silently diverging from
 *   `TITLE_AUTO_SHORTEN_THRESHOLD`.
 *
 * All tests are deterministic; the CI-gate test reads the committed
 * (version-controlled) stats YAML from disk, same discipline as
 * tests/planner-storyArc.test.ts's story_norms.yaml gate.
 */

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { describe, it, expect } from "vitest";
import {
  generatePlan,
  shortenDashboardTitle,
  TITLE_AUTO_SHORTEN_THRESHOLD,
} from "../src/planner/plan.js";
import type { FieldHint } from "../src/planner/fields.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const DASHBOARD_NORMS_PATH = join(REPO_ROOT, "design", "corpus", "stats", "dashboard_norms.yaml");

// ---------------------------------------------------------------------------
// §1 — shortenDashboardTitle (pure unit tests)
// ---------------------------------------------------------------------------

describe("shortenDashboardTitle — measure-noun extraction", () => {
  it('the plan\'s own worked example: "How Are Sales And Profit Performing Across Categories And States?" -> "Sales & Profit Performance"', () => {
    const result = shortenDashboardTitle(
      "How Are Sales And Profit Performing Across Categories And States?",
      "How Are Sales And Profit Performing Across Categories And St",
      ["Sales", "Profit"],
    );
    expect(result).toBe("Sales & Profit Performance");
  });

  it("the demo-dashboard question (scripts/demo-superstore.ts) shortens the same way", () => {
    const result = shortenDashboardTitle(
      "How is Superstore performing on sales and profit by region, category, and state?",
      "How Is Superstore Performing On Sales And Profit By Region,",
      ["Sales", "Profit", "Discount", "Quantity"],
    );
    expect(result).toBe("Sales & Profit Performance");
  });

  it("a single mentioned measure produces a single-measure headline", () => {
    const result = shortenDashboardTitle(
      "Show revenue by customer and revenue by region across every segment we track",
      "Show Revenue By Customer And Revenue By Region Across Every ",
      ["Revenue"],
    );
    expect(result).toBe("Revenue Performance");
  });

  it("caps at 2 measure names even when more are mentioned", () => {
    const result = shortenDashboardTitle(
      "How do sales, profit, and discount all move together across regions and categories?",
      "How Do Sales, Profit, And Discount All Move Together Across ",
      ["Sales", "Profit", "Discount"],
    );
    expect(result).toBe("Sales & Profit Performance");
  });

  it("is case-insensitive and matches word boundaries (not substrings)", () => {
    const result = shortenDashboardTitle(
      "how are SALES trending across every region and category we operate in this fiscal year",
      "How Are Sales Trending Across Every Region And Category We ",
      ["Sales"],
    );
    expect(result).toBe("Sales Performance");
  });

  it("falls back to word-boundary truncation when no plan measure is mentioned", () => {
    const fallback = "How Is Our Overall Customer Satisfaction Trending This Quarter";
    const result = shortenDashboardTitle(
      "How is our overall customer satisfaction trending this quarter?",
      fallback,
      ["Sales", "Profit"], // neither mentioned in the question
    );
    expect(result.length).toBeLessThanOrEqual(TITLE_AUTO_SHORTEN_THRESHOLD);
    expect(fallback.startsWith(result)).toBe(true);
  });

  it("falls back to truncation when there are no plan measures at all (placeholder plan)", () => {
    const fallback = "How Is Our Overall Customer Satisfaction Trending This Quarter";
    const result = shortenDashboardTitle(
      "How is our overall customer satisfaction trending this quarter?",
      fallback,
      [],
    );
    expect(result.length).toBeLessThanOrEqual(TITLE_AUTO_SHORTEN_THRESHOLD);
  });

  it("truncation never splits mid-word", () => {
    const fallback = "AAAAAAAAAA BBBBBBBBBB CCCCCCCCCC DDDDDDDDDD EEEEEEEEEE";
    const result = shortenDashboardTitle("no measure mentioned here", fallback, ["Sales"]);
    expect(fallback).toContain(result); // result is a prefix ending on a word boundary
    expect(result.endsWith(" ")).toBe(false);
  });

  it("is pure/deterministic: identical inputs produce identical output", () => {
    const a = shortenDashboardTitle("How are sales and profit trending across regions?", "X", [
      "Sales",
      "Profit",
    ]);
    const b = shortenDashboardTitle("How are sales and profit trending across regions?", "X", [
      "Sales",
      "Profit",
    ]);
    expect(a).toBe(b);
  });
});

// ---------------------------------------------------------------------------
// §2 — generatePlan() end-to-end wiring
// ---------------------------------------------------------------------------

const SUPERSTORE_FIELDS: FieldHint[] = [
  { name: "Sales", dataType: "number" },
  { name: "Profit", dataType: "number" },
  { name: "Category", dataType: "string" },
  { name: "State", dataType: "string" },
  { name: "Order Date", dataType: "date" },
];

describe("generatePlan — auto-shorten wiring", () => {
  it("a long question yields a shortened dashboardTitle and the full question as dashboardSubtitle", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How Are Sales And Profit Performing Across Categories And States?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    });
    expect(plan.dashboardTitle).toBe("Sales & Profit Performance");
    expect(plan.dashboardTitle!.length).toBeLessThanOrEqual(TITLE_AUTO_SHORTEN_THRESHOLD);
    expect(plan.dashboardSubtitle).toBe(
      "How Are Sales And Profit Performing Across Categories And States?",
    );
  });

  it("the exact demo-dashboard question (scripts/demo-superstore.ts) shortens end-to-end", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion:
        "How is Superstore performing on sales and profit by region, category, and state?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    });
    expect(plan.dashboardTitle).toBe("Sales & Profit Performance");
    expect(plan.dashboardSubtitle).toBe(
      "How is Superstore performing on sales and profit by region, category, and state?",
    );
  });

  it("a short question is left untouched — no shortening, generic audience subtitle stays", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion: "How is revenue trending?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    });
    expect(plan.dashboardTitle).toBe("How Is Revenue Trending");
    expect(plan.dashboardSubtitle).toBe("Executive Summary · Period-over-Period");
  });

  it("storyName mirrors the (possibly shortened) dashboardTitle when a story is warranted", () => {
    const plan = generatePlan({
      mode: "autonomous",
      audience: "exec",
      businessQuestion:
        "Tell the story of how sales and profit are performing across categories and states",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    });
    expect(plan.storyName).toBe(plan.dashboardTitle);
    expect(plan.dashboardTitle!.length).toBeLessThanOrEqual(TITLE_AUTO_SHORTEN_THRESHOLD);
  });

  it("determinism: identical long-question input produces a byte-identical plan across calls", () => {
    const input = {
      mode: "autonomous" as const,
      audience: "exec" as const,
      businessQuestion: "How Are Sales And Profit Performing Across Categories And States?",
      fieldHints: SUPERSTORE_FIELDS,
      datasourceLuid: "DS",
      datasourceName: "Superstore",
      projectName: "Finance",
    };
    const plan1 = generatePlan(input);
    const plan2 = generatePlan(input);
    expect(JSON.stringify(plan1)).toBe(JSON.stringify(plan2));
  });
});

// ---------------------------------------------------------------------------
// §3 — CI gate: TITLE_AUTO_SHORTEN_THRESHOLD tracks the committed mined
// stats, so a future corpus refresh that adds a title-length bucket is
// caught here instead of silently diverging.
// ---------------------------------------------------------------------------

describe("TITLE_AUTO_SHORTEN_THRESHOLD — mined-stats CI gate", () => {
  const stats = parseYaml(readFileSync(DASHBOARD_NORMS_PATH, "utf-8")) as {
    strata?: Record<string, Record<string, unknown>>;
  };
  const stratum = stats.strata?.["900-1400"] ?? {};
  const titleLengthStat = stratum["title_length"] as
    | { confidence?: string; p75?: number }
    | undefined;

  it("dashboard_norms.yaml currently has NO title-length bucket (documented judgment premise)", () => {
    // This is the premise ASSUMPTIONS.md's 40-char judgment call rests on.
    // If a future corpus refresh (re-running sidecar/design_stats.py with a
    // title-length extractor) adds this bucket, the NEXT assertion in this
    // describe block takes over and this constant must be reconciled with
    // the mined p75 instead of staying a hardcoded guess.
    expect(titleLengthStat).toBeUndefined();
  });

  it("shortened titles stay within the effective threshold (mined p75 if present, else the documented 40)", () => {
    const effectiveThreshold =
      titleLengthStat?.confidence === "ok" && typeof titleLengthStat.p75 === "number"
        ? titleLengthStat.p75
        : TITLE_AUTO_SHORTEN_THRESHOLD;
    const shortened = shortenDashboardTitle(
      "How Are Sales And Profit Performing Across Categories And States?",
      "How Are Sales And Profit Performing Across Categories And St",
      ["Sales", "Profit"],
    );
    expect(shortened.length).toBeLessThanOrEqual(effectiveThreshold);
  });
});
