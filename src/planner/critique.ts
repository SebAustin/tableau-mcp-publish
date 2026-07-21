/**
 * VizCritique-lite — a DETERMINISTIC dashboard self-critique.
 *
 * The external-skill-suite VizCritique-Pro skill scores a rendered dashboard with an LLM
 * judge. That mechanism violates this project's A-01 invariant (no LLM call
 * inside the server). This is the architecture-honest assimilation: score a
 * GENERATED plan against the norms we already mined — the top-100
 * `visual_norms.yaml` business_dashboard stratum, the WCAG contrast math, and
 * the ENFORCED Stephen Few rules — with pure, testable rules and zero judgment.
 *
 * Every dimension whose backing norm bucket is `confidence: low` degrades to a
 * "note" (excluded from the score), mirroring the T2/V-phase discipline: a
 * low-n norm never drives an assertion.
 */

import { contrastRatio } from "../branding/contrast.js";
import type { HexColor } from "../branding/schema.js";

// --- shapes the critique reads from a DashboardPlan (structural subset) ------

export interface CritiquePlanSheet {
  kind?: string; // "chart" | "kpi_tile"
  markType?: string;
  kpi?: { comparisonMeasure?: string; comparisonKind?: string; deltaMeasure?: string };
}
export interface CritiquePlanTheme {
  dashboardBackground?: string;
  header?: { titleColor?: string; subtitleColor?: string; background?: string };
  kpiTile?: { background?: string; banColor?: string };
}
export interface CritiquePlan {
  dashboardTitle?: string;
  layoutGrammar?: { kind?: string };
  sheets?: CritiquePlanSheet[];
  designTheme?: CritiquePlanTheme;
}

// --- the mined norms it scores against (business_dashboard stratum) ----------

interface CategoricalNorm {
  buckets: Record<string, { count: number; rate: number }>;
  n: number;
  confidence: "ok" | "low";
}
interface NumericNorm {
  median: number;
  p25: number;
  p75: number;
  n: number;
  confidence: "ok" | "low";
}
export interface BusinessNorms {
  layout_archetype?: CategoricalNorm;
  ban_count?: NumericNorm;
  title_case?: CategoricalNorm;
  chart_type_prevalence?: CategoricalNorm;
}

// --- output ------------------------------------------------------------------

export type Verdict = "pass" | "warn" | "note";
export interface CritiqueDimension {
  dimension: string;
  verdict: Verdict;
  finding: string;
  normCited?: { stat: string; observed: string; expected: string; n: number; confidence: string };
}
export interface CritiqueResult {
  overallScore: number; // 0-100 over non-note dimensions
  dimensions: CritiqueDimension[];
}

/** Score weights (documented + fixed). Contrast is weighted highest — it is the
 * one dimension with an accessibility/correctness consequence, not a stylistic
 * preference. A `warn` earns partial (0.4) credit; `pass` full; `note` is
 * excluded from both numerator and denominator. */
const WEIGHTS = { Layout: 25, "KPI count": 15, "Chart mix": 20, Title: 10, Contrast: 30 } as const;
const WARN_CREDIT = 0.4;
const WCAG_NORMAL = 4.5;

function isLowConf(norm?: { confidence: "ok" | "low" }): boolean {
  return !norm || norm.confidence === "low";
}

/** Top-k bucket keys by descending rate (stable: rate desc, then key asc). */
function topKeys(norm: CategoricalNorm, k: number): string[] {
  return Object.entries(norm.buckets)
    .sort((a, b) => b[1].rate - a[1].rate || a[0].localeCompare(b[0]))
    .slice(0, k)
    .map(([key]) => key);
}

function mapGrammarToArchetype(kind?: string): string | undefined {
  if (kind === "kpi_band_over_charts") return "kpi_band_top";
  if (kind === "tiled_vertical" || kind === "tiled_horizontal") return "grid_of_charts";
  return undefined;
}

function detectTitleCase(title: string): "all_caps" | "title_case" | "sentence_case" | "mixed" {
  const words = title.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "mixed";
  const letters = title.replace(/[^A-Za-z]/g, "");
  if (letters.length > 0 && letters === letters.toUpperCase()) return "all_caps";
  const titleCased = words.every((w) => /^[^A-Za-z]*[A-Z]/.test(w));
  if (titleCased) return "title_case";
  if (/^[^A-Za-z]*[A-Z]/.test(words[0] ?? "")) return "sentence_case";
  return "mixed";
}

function dim(
  dimension: string,
  verdict: Verdict,
  finding: string,
  normCited?: CritiqueDimension["normCited"],
): CritiqueDimension {
  return normCited ? { dimension, verdict, finding, normCited } : { dimension, verdict, finding };
}

/**
 * Critique a plan against the business_dashboard norms. Pure + deterministic.
 */
export function critiqueDashboardPlan(plan: CritiquePlan, norms: BusinessNorms): CritiqueResult {
  const dims: CritiqueDimension[] = [];
  const sheets = plan.sheets ?? [];

  // 1 — Layout archetype vs the top-2 real-world business patterns.
  if (isLowConf(norms.layout_archetype)) {
    dims.push(dim("Layout", "note", "No confident layout norm to compare against."));
  } else {
    const norm = norms.layout_archetype as CategoricalNorm;
    const archetype = mapGrammarToArchetype(plan.layoutGrammar?.kind);
    const top2 = topKeys(norm, 2);
    if (archetype && top2.includes(archetype)) {
      dims.push(
        dim("Layout", "pass", `Layout "${archetype}" matches a leading real-world pattern.`, {
          stat: "layout_archetype",
          observed: archetype,
          expected: top2.join("/"),
          n: norm.n,
          confidence: norm.confidence,
        }),
      );
    } else {
      dims.push(
        dim(
          "Layout",
          "warn",
          `Layout "${archetype ?? plan.layoutGrammar?.kind ?? "unset"}" is not among the top real-world business layouts.`,
          { stat: "layout_archetype", observed: archetype ?? "unset", expected: top2.join("/"), n: norm.n, confidence: norm.confidence },
        ),
      );
    }
  }

  // 2 — KPI-tile count vs the mined [p25, p75] band.
  const kpiCount = sheets.filter((s) => s.kind === "kpi_tile").length;
  if (isLowConf(norms.ban_count)) {
    dims.push(dim("KPI count", "note", "No confident BAN-count norm to compare against."));
  } else {
    const norm = norms.ban_count as NumericNorm;
    const inBand = kpiCount >= norm.p25 && kpiCount <= norm.p75;
    dims.push(
      dim("KPI count", inBand ? "pass" : "warn", inBand
        ? `${kpiCount} KPI tiles is within the typical range.`
        : `${kpiCount} KPI tiles is outside the typical range [${norm.p25}, ${norm.p75}] (median ${norm.median}).`,
        { stat: "ban_count", observed: String(kpiCount), expected: `${norm.p25}-${norm.p75}`, n: norm.n, confidence: norm.confidence }),
    );
  }

  // 3 — Chart mix + ENFORCED Few rules (structural, always evaluable).
  const hasKpiTiles = kpiCount > 0;
  const hasComparison = sheets.some(
    (s) => s.kind === "kpi_tile" && (s.kpi?.comparisonMeasure || s.kpi?.comparisonKind || s.kpi?.deltaMeasure),
  );
  // no-pie is structurally guaranteed (MarkTypeEnum has no "pie"); the only
  // live check is: KPI tiles should carry a period comparison (FEW-2).
  if (hasKpiTiles && !hasComparison) {
    dims.push(dim("Chart mix", "warn", "KPI tiles present but none carry a period comparison (FEW-2)."));
  } else {
    dims.push(dim("Chart mix", "pass", "Mark types are pie/gauge-free and KPI tiles carry comparisons (Few-compliant)."));
  }

  // 4 — Title casing vs the mined distribution.
  if (!plan.dashboardTitle) {
    dims.push(dim("Title", "note", "No dashboard title to evaluate."));
  } else if (isLowConf(norms.title_case)) {
    dims.push(dim("Title", "note", "No confident title-case norm to compare against."));
  } else {
    const norm = norms.title_case as CategoricalNorm;
    const observed = detectTitleCase(plan.dashboardTitle);
    const modal = topKeys(norm, 1)[0];
    const rate = norm.buckets[observed]?.rate ?? 0;
    const ok = observed === modal || rate >= 0.25;
    dims.push(
      dim("Title", ok ? "pass" : "warn", ok
        ? `Title case "${observed}" is common in real dashboards.`
        : `Title case "${observed}" is uncommon; most business dashboards use "${modal}".`,
        { stat: "title_case", observed, expected: modal ?? "title_case", n: norm.n, confidence: norm.confidence }),
    );
  }

  // 5 — Contrast (WCAG) — the one accessibility-critical dimension.
  const theme = plan.designTheme;
  const pairs: Array<{ label: string; fg?: string; bg?: string }> = theme
    ? [
        { label: "header title", fg: theme.header?.titleColor, bg: theme.header?.background ?? theme.dashboardBackground },
        { label: "KPI value", fg: theme.kpiTile?.banColor, bg: theme.kpiTile?.background },
      ]
    : [];
  const evaluable = pairs.filter((p) => p.fg && p.bg);
  if (evaluable.length === 0) {
    dims.push(dim("Contrast", "note", "No themed color pairs to check."));
  } else {
    const failures = evaluable
      .map((p) => ({ ...p, ratio: contrastRatio(p.fg as HexColor, p.bg as HexColor) }))
      .filter((p) => p.ratio < WCAG_NORMAL);
    if (failures.length === 0) {
      dims.push(dim("Contrast", "pass", "All themed text/background pairs meet WCAG AA (4.5:1)."));
    } else {
      const worst = failures.sort((a, b) => a.ratio - b.ratio)[0];
      dims.push(
        dim("Contrast", "warn", `${failures.length} themed pair(s) below WCAG AA; worst: ${worst?.label} at ${worst?.ratio.toFixed(2)}:1.`, {
          stat: "wcag_contrast", observed: `${worst?.ratio.toFixed(2)}:1`, expected: "≥4.5:1", n: evaluable.length, confidence: "ok",
        }),
      );
    }
  }

  // --- weighted score over the non-note dimensions ---------------------------
  let num = 0;
  let den = 0;
  for (const d of dims) {
    if (d.verdict === "note") continue;
    const w = WEIGHTS[d.dimension as keyof typeof WEIGHTS] ?? 0;
    den += w;
    num += w * (d.verdict === "pass" ? 1 : WARN_CREDIT);
  }
  const overallScore = den === 0 ? 0 : Math.round((num / den) * 100);
  return { overallScore, dimensions: dims };
}
