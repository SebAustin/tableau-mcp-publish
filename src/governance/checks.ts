/**
 * Governance-lite checks (external-skill-suite Governance-Scanner, deterministic subset).
 *
 * These are the three governance checks the server's EXISTING read surface can
 * support from a `ContentItem` list alone (`{ id, name, type, projectName?,
 * updatedAt? }`). Owner-concentration, view-count/adoption, and certification
 * checks from the full Governance-Scanner skill are deliberately NOT here: they
 * need read scope (workbook/view metadata introspection) this publish-oriented
 * server does not carry — see docs/adr/0014-external-skill-analysis.md and
 * design/corpus/GAPS.md §4.
 *
 * The scanner is a PURE function: the clock is injected (`nowIso`) so a stale
 * check is deterministic and unit-testable — no `Date.now()` inside.
 */

/** One content item as the REST client's `listContent()` returns it. */
export interface ContentRef {
  id: string;
  name: string;
  type: "datasource" | "workbook";
  projectName?: string;
  updatedAt?: string;
}

export type GovernanceCheck = "stale" | "default_project" | "naming";
export type GovernanceSeverity = "info" | "warn";

export interface GovernanceFinding {
  item: { id: string; name: string; type: "datasource" | "workbook" };
  check: GovernanceCheck;
  severity: GovernanceSeverity;
  detail: string;
}

export interface GovernanceScanResult {
  findings: GovernanceFinding[];
  summary: {
    scanned: number;
    /** Items skipped by the stale check because they carry no `updatedAt`. */
    staleUndetermined: number;
    byCheck: Record<GovernanceCheck, number>;
  };
}

export interface GovernanceScanOptions {
  /** ISO-8601 "now" — injected so the pure fn stays deterministic. */
  nowIso: string;
  /** Items older than this many days flag `stale`. */
  staleDays: number;
  /**
   * A name matching this regex flags `naming`. Default (below) flags
   * leading/trailing whitespace or a doubled internal space — cheap,
   * unambiguous hygiene signals that don't presume a house style.
   */
  namingPattern?: RegExp;
}

export const DEFAULT_STALE_DAYS = 180;
/** Leading/trailing whitespace, or two-or-more consecutive spaces. */
export const DEFAULT_NAMING_PATTERN = /^\s|\s$|\s{2,}/;

const MS_PER_DAY = 86_400_000;

function ageDays(updatedAtIso: string, nowIso: string): number | null {
  const then = Date.parse(updatedAtIso);
  const now = Date.parse(nowIso);
  if (Number.isNaN(then) || Number.isNaN(now)) return null;
  return (now - then) / MS_PER_DAY;
}

/**
 * Deterministically scan a content list for the three supported governance
 * issues. Findings are emitted in input order, `stale`→`default_project`→
 * `naming` within a single item, so output is stable for a given input.
 */
export function scanContent(
  items: readonly ContentRef[],
  opts: GovernanceScanOptions,
): GovernanceScanResult {
  const namingPattern = opts.namingPattern ?? DEFAULT_NAMING_PATTERN;
  const findings: GovernanceFinding[] = [];
  const byCheck: Record<GovernanceCheck, number> = {
    stale: 0,
    default_project: 0,
    naming: 0,
  };
  let staleUndetermined = 0;

  for (const it of items) {
    const ref = { id: it.id, name: it.name, type: it.type };

    // 1. Stale — only decidable when updatedAt is present + parseable.
    if (it.updatedAt) {
      const age = ageDays(it.updatedAt, opts.nowIso);
      if (age === null) {
        staleUndetermined += 1;
      } else if (age > opts.staleDays) {
        findings.push({
          item: ref,
          check: "stale",
          severity: "warn",
          detail: `Not updated in ${Math.floor(age)} days (threshold ${opts.staleDays}).`,
        });
        byCheck.stale += 1;
      }
    } else {
      staleUndetermined += 1;
    }

    // 2. Default project.
    if (it.projectName?.toLowerCase() === "default") {
      findings.push({
        item: ref,
        check: "default_project",
        severity: "warn",
        detail: "Published to the Default project; move it to a governed project.",
      });
      byCheck.default_project += 1;
    }

    // 3. Naming convention.
    if (namingPattern.test(it.name)) {
      findings.push({
        item: ref,
        check: "naming",
        severity: "info",
        detail: `Name "${it.name}" has whitespace/formatting that reads as inconsistent.`,
      });
      byCheck.naming += 1;
    }
  }

  return { findings, summary: { scanned: items.length, staleUndetermined, byCheck } };
}
