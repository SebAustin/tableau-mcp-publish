/**
 * buildProposal — Slice 5: propose→confirm contract.
 *
 * Derives a human-readable, confirmable DashboardProposal from a DashboardPlan.
 * The proposal is the value returned by `design_dashboard` for autonomous,
 * directed, and interview_followup modes.  It carries:
 *   - A one-paragraph natural-language summary.
 *   - A kpiStrip: one entry per kpi_tile sheet in the plan.
 *   - A views array: one entry per non-kpi_tile sheet in the plan.
 *   - A layoutSummary: human description of the layout grammar.
 *   - Optional openQuestions: surfaced when placeholder tokens or defaulted
 *     audience assumptions are present.
 *   - plan: the input DashboardPlan VERBATIM — passed to build_from_plan on
 *     user confirmation.
 *
 * This module is PURE / DETERMINISTIC — no I/O, no randomness.
 */

import {
  DashboardProposalSchema,
  type DashboardPlan,
  type DashboardProposal,
  type KpiStripItem,
  type ProposedView,
} from "./schema.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert a snake/kebab field name to a readable label. */
function toLabel(name: string): string {
  return name
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/** Determine the KPI direction from the kpi block on a kpi_tile sheet. */
function kpiDirection(
  deltaIsPositiveGood: boolean | undefined,
  hasDelta: boolean,
): "up_good" | "down_good" | "neutral" {
  if (!hasDelta) return "neutral";
  return deltaIsPositiveGood === false ? "down_good" : "up_good";
}

// ---------------------------------------------------------------------------
// kpiStrip builder
// ---------------------------------------------------------------------------

/**
 * Build KpiStripItem entries from the kpi_tile sheets in a plan.
 *
 * Each kpi_tile sheet contributes one entry. If a sheet is kpi_tile but has
 * no kpi block (degraded / legacy), we still emit a minimal entry using the
 * sheet title and the first measure name.
 */
function buildKpiStripItems(plan: DashboardPlan): KpiStripItem[] {
  const items: KpiStripItem[] = [];

  for (const sheet of plan.sheets) {
    if (sheet.kind !== "kpi_tile") continue;

    const kpi = sheet.kpi;
    if (kpi) {
      // M3 precedence fix (GAPS.md Sec 4): a KPI tile's delta now renders
      // either from an explicit/auto-paired kpi.deltaMeasure OR from a
      // computed YoY (kpi.comparisonKind === "yoy" — "mom" is not yet
      // builder-rendered, see comparison.ts). direction must reflect
      // whichever one will actually render, not deltaMeasure alone.
      const hasRenderedDelta = kpi.deltaMeasure !== undefined || kpi.comparisonKind === "yoy";
      items.push({
        label: sheet.title,
        primaryMeasure: kpi.primaryMeasure,
        ...(kpi.comparisonMeasure !== undefined
          ? { comparisonMeasure: kpi.comparisonMeasure }
          : {}),
        ...(kpi.deltaMeasure !== undefined ? { deltaMeasure: kpi.deltaMeasure } : {}),
        direction: kpiDirection(kpi.deltaIsPositiveGood, hasRenderedDelta),
      });
    } else {
      // Graceful degradation: no kpi block — use first measure or title
      const primaryMeasure = sheet.measures[0] ?? sheet.title;
      items.push({
        label: sheet.title,
        primaryMeasure,
        direction: "neutral",
      });
    }
  }

  return items;
}

// ---------------------------------------------------------------------------
// Encoding summary builder
// ---------------------------------------------------------------------------

/**
 * Produce a human-readable encoding summary for a non-kpi_tile sheet.
 *
 * Examples:
 * - bar of "Sales by Category, colored by Segment"
 * - filled map of "Sales by State"
 * - scatter of "Sales vs Profit"
 * - line of "Sales over Order Date"
 */
function buildEncodingSummary(
  plan: DashboardPlan,
  sheetIndex: number,
): { summary: string; fields: string[] } {
  const sheet = plan.sheets[sheetIndex]!;
  const fields = new Set<string>();

  // Collect all field references
  for (const f of sheet.cols) fields.add(f);
  for (const f of sheet.rows) fields.add(f);
  for (const f of sheet.measures) fields.add(f);
  if (sheet.color?.field) fields.add(sheet.color.field);
  if (sheet.scatter?.x) fields.add(sheet.scatter.x);
  if (sheet.scatter?.y) fields.add(sheet.scatter.y);
  if (sheet.scatter?.breakdown) fields.add(sheet.scatter.breakdown);
  if (sheet.geo?.geoField) fields.add(sheet.geo.geoField);

  // Remove CP/PP/Difference columns from the visible fields list
  // (they are KPI-only and not meaningful to surface here)
  const visibleFields = [...fields].filter(
    (f) => !/^CP |^PP | Difference$/.test(f),
  );

  let summary: string;

  if (sheet.markType === "scatter" && sheet.scatter) {
    const { x, y, breakdown } = sheet.scatter;
    summary = `scatter: ${toLabel(x)} vs ${toLabel(y)}`;
    if (breakdown) summary += `, colored by ${toLabel(breakdown)}`;
  } else if (sheet.markType === "map_filled" && sheet.geo) {
    const primary = sheet.measures[0];
    const geo = sheet.geo.geoField;
    summary = `filled map: ${primary ? toLabel(primary) + " by " : ""}${toLabel(geo)}`;
    if (sheet.color?.field && sheet.color.field !== primary) {
      summary += `, colored by ${toLabel(sheet.color.field)}`;
    }
  } else {
    // bar / line / text / map
    const dim = sheet.cols[0] ?? sheet.rows[0];
    const measure = sheet.measures[0];
    const markLabel =
      sheet.markType === "bar"
        ? "bar"
        : sheet.markType === "line"
          ? "line"
          : sheet.markType === "text"
            ? "table"
            : sheet.markType;

    if (measure && dim) {
      summary = `${markLabel}: ${toLabel(measure)} by ${toLabel(dim)}`;
    } else if (measure) {
      summary = `${markLabel}: ${toLabel(measure)}`;
    } else if (dim) {
      summary = `${markLabel}: ${toLabel(dim)}`;
    } else {
      summary = markLabel;
    }

    if (sheet.color?.field) {
      summary += `, colored by ${toLabel(sheet.color.field)}`;
    }
  }

  return { summary, fields: visibleFields.length > 0 ? visibleFields : [...fields] };
}

// ---------------------------------------------------------------------------
// views builder
// ---------------------------------------------------------------------------

/**
 * Build ProposedView entries from the non-kpi_tile sheets in a plan.
 */
function buildViews(plan: DashboardPlan): ProposedView[] {
  const views: ProposedView[] = [];

  for (let i = 0; i < plan.sheets.length; i++) {
    const sheet = plan.sheets[i]!;
    if (sheet.kind === "kpi_tile") continue;

    const { summary, fields } = buildEncodingSummary(plan, i);

    views.push({
      title: sheet.title,
      chartType: sheet.markType,
      encodingSummary: summary,
      fields: fields.length > 0 ? fields : [sheet.title],
    });
  }

  // Safety: DashboardProposalSchema requires min(1) view.
  // If the plan only has kpi_tile sheets, synthesize a minimal view from the
  // first sheet so the schema still validates. This is a degenerate case that
  // should not occur with well-formed exec plans.
  if (views.length === 0 && plan.sheets.length > 0) {
    const first = plan.sheets[0]!;
    views.push({
      title: first.title,
      chartType: first.markType,
      encodingSummary: first.title,
      fields: first.measures.length > 0 ? first.measures : [first.title],
    });
  }

  return views;
}

// ---------------------------------------------------------------------------
// layoutSummary builder
// ---------------------------------------------------------------------------

/**
 * Produce a one-sentence layout description for the user.
 *
 * Examples:
 * - "KPI band of 4 tiles (Sales, Profit, Profit Ratio, Quantity) above a 2-chart row (Sales by Category, Sales by State)."
 * - "2 charts in a vertical tile stack."
 */
function buildLayoutSummary(plan: DashboardPlan): string {
  const lg = plan.layoutGrammar;

  if (lg?.kind === "kpi_band_over_charts") {
    const kpiCount = lg.kpiTileTitles?.length ?? 0;
    const chartCount = lg.chartTitles?.length ?? 0;
    const kpiNames = lg.kpiTileTitles?.join(", ") ?? "";
    const chartNames = lg.chartTitles?.join(", ") ?? "";
    const kpiPart =
      kpiCount > 0
        ? `KPI band of ${kpiCount} tile${kpiCount === 1 ? "" : "s"} (${kpiNames})`
        : "KPI band";
    const chartPart =
      chartCount > 0
        ? `${chartCount}-chart row (${chartNames})`
        : "chart section";
    return `${kpiPart} above a ${chartPart}.`;
  }

  // Fallback for tiled layouts
  const chartSheets = plan.sheets.filter((s) => s.kind !== "kpi_tile");
  const kpiSheets = plan.sheets.filter((s) => s.kind === "kpi_tile");
  const direction = plan.dashboardLayout === "tiled_horizontal" ? "horizontal" : "vertical";

  if (kpiSheets.length > 0) {
    return (
      `${kpiSheets.length} KPI tile${kpiSheets.length === 1 ? "" : "s"} and ` +
      `${chartSheets.length} chart${chartSheets.length === 1 ? "" : "s"} in a ${direction} tile stack.`
    );
  }
  return `${chartSheets.length} chart${chartSheets.length === 1 ? "" : "s"} in a ${direction} tile stack.`;
}

// ---------------------------------------------------------------------------
// Summary builder
// ---------------------------------------------------------------------------

/**
 * Build the one-paragraph natural-language summary for the proposal.
 *
 * Template: "An <audience> dashboard answering '<question>' with …"
 */
function buildSummary(plan: DashboardPlan): string {
  const kpiTiles = plan.sheets.filter((s) => s.kind === "kpi_tile");
  const nonKpiSheets = plan.sheets.filter((s) => s.kind !== "kpi_tile");

  const audienceLabel = plan.audience.charAt(0).toUpperCase() + plan.audience.slice(1);
  const dsName = plan.datasourceName;

  // Collect primary measure names from KPI tiles (deduplicated)
  const kpiMeasureNames = kpiTiles
    .map((s) => s.kpi?.primaryMeasure ?? s.title)
    .filter((name) => !/^CP |^PP | Difference$/.test(name));

  // Chart description list
  const chartDescriptions = nonKpiSheets.map((s) => {
    if (s.markType === "map_filled") {
      const geo = s.geo?.geoField ?? "geography";
      const measure = s.measures[0] ?? "";
      return `a filled ${geo} map${measure ? ` colored by ${toLabel(measure)}` : ""}`;
    }
    if (s.markType === "scatter" && s.scatter) {
      return `a scatter plot of ${toLabel(s.scatter.x)} vs ${toLabel(s.scatter.y)}`;
    }
    if (s.markType === "bar") {
      const measure = s.measures[0] ?? "";
      const dim = s.cols[0] ?? s.rows[0] ?? "";
      const colorPart = s.color ? `, colored by ${toLabel(s.color.field)}` : "";
      return `a bar chart of ${toLabel(measure)} by ${toLabel(dim)}${colorPart}`;
    }
    if (s.markType === "line") {
      const measure = s.measures[0] ?? "";
      const dim = s.cols[0] ?? s.rows[0] ?? "";
      return `a trend line of ${toLabel(measure)} over ${toLabel(dim)}`;
    }
    return `a ${s.markType} chart ("${s.title}")`;
  });

  // Assemble the summary sentence
  let summary = `${audienceLabel} dashboard on "${dsName}"`;

  if (kpiMeasureNames.length > 0) {
    const kpiList = kpiMeasureNames.join(", ");
    summary += ` featuring a KPI band of ${kpiTiles.length} metric${kpiTiles.length === 1 ? "" : "s"} (${kpiList})`;
  }

  if (chartDescriptions.length > 0) {
    const over = kpiMeasureNames.length > 0 ? " over " : " with ";
    summary += over + chartDescriptions.join(" and ");
  }

  summary += ".";

  // Phase E1 (Slice A): surface persona/brand provenance when design_dashboard
  // resolved a named persona from brand.yaml.
  if (plan.personaName) {
    if (plan.personaTone === "concise") {
      // Phase E1 (Slice C): concise tone folds the persona mention into the
      // same sentence (instead of appending a second one) so the later
      // first-sentence trim in `applyToneToSummary` still preserves
      // provenance rather than discarding it.
      summary = summary.replace(/\.$/, ` (persona: "${plan.personaName}").`);
    } else {
      summary += ` Tailored for the "${plan.personaName}" persona`;
      summary += plan.brandName ? ` (${plan.brandName} brand).` : ".";
    }
  }

  return summary;
}

// ---------------------------------------------------------------------------
// Open-questions detector
// ---------------------------------------------------------------------------

const PLACEHOLDER_RE = /^<(measure|dimension)>$/;

/**
 * Surface any assumptions worth flagging to the user before they confirm.
 * Returns undefined when there is nothing notable to call out.
 */
function detectOpenQuestions(plan: DashboardPlan): string[] | undefined {
  const questions: string[] = [];

  // Placeholder tokens still present
  for (const sheet of plan.sheets) {
    const allFields = [...sheet.cols, ...sheet.rows, ...sheet.measures];
    for (const field of allFields) {
      if (PLACEHOLDER_RE.test(field)) {
        questions.push(
          `Sheet "${sheet.title}" still contains placeholder token "${field}". ` +
            "Provide fieldHints or edit the plan before confirming.",
        );
        break; // one warning per sheet is enough
      }
    }
  }

  // No field hints were used (plan has placeholder sheets) — surfaced via
  // the rationale text, but we can also surface it here for clarity.
  const hasPlaceholderNote =
    plan.rationale.includes("placeholder") || plan.rationale.includes("No field hints");
  if (hasPlaceholderNote && questions.length === 0) {
    questions.push(
      "No fieldHints were supplied; sheet definitions are generic placeholders. " +
        "Provide fieldHints for a richer proposal.",
    );
  }

  return questions.length > 0 ? questions : undefined;
}

// ---------------------------------------------------------------------------
// Phase E1 (Slice C) — persona `preferredArtifact` / `tone` consumption
// (BI_DESIGN §9)
// ---------------------------------------------------------------------------

/**
 * When the resolved persona prefers an artifact type this server cannot
 * build yet ("pulse"), surface an honest openQuestion naming the phase where
 * that capability lands, rather than silently ignoring the preference or
 * pretending to honor it. "dashboard" (or no preference) needs no note — this
 * proposal already is a dashboard.
 *
 * Phase E4: "story" is no longer a stub — `generatePlan()` emits a
 * deterministic `storyArc` for it and `buildProposal` surfaces the arc via
 * `storyOutline` below, so no "not yet available" note is warranted for that
 * preference anymore.
 */
function detectPersonaArtifactQuestion(plan: DashboardPlan): string | undefined {
  const artifact = plan.personaPreferredArtifact;
  if (artifact === undefined || artifact === "dashboard" || artifact === "story") {
    return undefined;
  }

  const personaLabel = plan.personaName ? `The "${plan.personaName}" persona` : "This persona";
  return (
    `${personaLabel} prefers a "${artifact}" artifact; ${artifact} generation lands in Phase E3 ` +
    "and is not yet available — this proposal is a dashboard."
  );
}

// ---------------------------------------------------------------------------
// Phase E4 — story outline
// ---------------------------------------------------------------------------

/** Ordered captions from `plan.storyArc`, or undefined when there is none. */
function buildStoryOutline(plan: DashboardPlan): string[] | undefined {
  if (!plan.storyArc || plan.storyArc.length === 0) return undefined;
  return plan.storyArc.map((p) => p.caption);
}

/**
 * `tone: "concise"` trims the proposal summary down to its first sentence
 * plus the layout line, dropping the (often long) per-chart description list
 * — appropriate for a persona who wants headline numbers only, not prose.
 * Any other tone (or no tone) leaves `summary` unchanged.
 */
function applyToneToSummary(
  summary: string,
  layoutSummary: string,
  tone: DashboardPlan["personaTone"],
): string {
  if (tone !== "concise") return summary;
  const firstSentenceMatch = summary.match(/^[^.]*\.?/);
  const firstSentence = firstSentenceMatch?.[0]?.trim() || summary;
  return `${firstSentence} ${layoutSummary}`.trim();
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Build a DashboardProposal from a DashboardPlan.
 *
 * Pure and deterministic: same plan → same proposal.
 * The embedded `plan` field is the input plan VERBATIM — the agent must pass
 * it unchanged to `build_from_plan` on user confirmation.
 *
 * @param plan - A fully-formed DashboardPlan produced by generatePlan().
 * @returns A DashboardProposal validated by DashboardProposalSchema.
 */
export function buildProposal(plan: DashboardPlan): DashboardProposal {
  const kpiStrip = buildKpiStripItems(plan);
  const views = buildViews(plan);
  const layoutSummary = buildLayoutSummary(plan);
  let summary = applyToneToSummary(buildSummary(plan), layoutSummary, plan.personaTone);

  // Phase E4: surface the story arc (if any) both as a structured field
  // (storyOutline, for programmatic use) and as a sentence in the
  // human-readable summary (so it isn't buried in the plan JSON).
  const storyOutline = buildStoryOutline(plan);
  if (storyOutline) {
    summary +=
      ` This proposal includes a ${storyOutline.length}-point story: ` +
      `${storyOutline.join(" -> ")}.`;
  }

  // Design Excellence, Slice D5: name the resolved design theme, if any.
  // Appended AFTER applyToneToSummary (same placement as the storyOutline
  // sentence above) — a concise-tone summary is trimmed to its first
  // sentence + layoutSummary, so a theme mention folded INTO buildSummary()
  // would be silently dropped for concise personas; appending it here keeps
  // it visible regardless of tone.
  if (plan.designTheme) {
    summary += ` Styled with the "${plan.designTheme.name}" design theme.`;
  }

  // Design Excellence, Slice D7: mention cross-filtering when enabled (see
  // designDashboard.ts's auto-enable rule). Same placement/rationale as the
  // designTheme mention above — appended after tone trimming so it survives
  // concise summaries too.
  if (plan.interactions?.crossFilter) {
    summary += " Cross-filtering enabled.";
  }

  const artifactNote = detectPersonaArtifactQuestion(plan);
  const combinedOpenQuestions = [
    ...(detectOpenQuestions(plan) ?? []),
    ...(artifactNote ? [artifactNote] : []),
  ];
  const openQuestions = combinedOpenQuestions.length > 0 ? combinedOpenQuestions : undefined;

  const raw = {
    schemaVersion: plan.schemaVersion,
    kind: "proposal" as const,
    workbookName: plan.workbookName,
    audience: plan.audience,
    datasourceName: plan.datasourceName,
    projectName: plan.projectName,
    summary,
    ...(plan.dashboardTitle !== undefined ? { dashboardTitle: plan.dashboardTitle } : {}),
    ...(plan.dashboardSubtitle !== undefined ? { dashboardSubtitle: plan.dashboardSubtitle } : {}),
    kpiStrip,
    views,
    layoutSummary,
    ...(openQuestions !== undefined ? { openQuestions } : {}),
    ...(storyOutline !== undefined ? { storyOutline } : {}),
    plan,
  };

  // Validate through schema — throws on any structural violation so callers
  // get an explicit error rather than a silent invalid payload.
  return DashboardProposalSchema.parse(raw);
}
