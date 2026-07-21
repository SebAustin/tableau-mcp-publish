# ADR-0015 — Deterministic self-critique (VizCritique-lite), and the N-phase assimilation split

Date: 2026-07-21 · Status: Accepted · Supersedes/extends: ADR-0014 (external skill analysis)

## Context

ADR-0014 analyzed six an external Tableau MCP skill suite Claude skills and applied a deterministic subset (contrast math,
descending sort, Pulse enum widening); the remainder was backlogged in `design/corpus/GAPS.md §4`.
The user then green-lit building that remainder. This ADR records how each item was assimilated —
and, for the flagship, why it was rebuilt rather than ported.

## Decision

**`critique_dashboard` is a deterministic self-critique, not a port of VizCritique-Pro.** The source
skill scores a *rendered* dashboard with an LLM judge. That mechanism violates this project's **A-01**
invariant (no LLM call inside the MCP server process — the planner is rule-based, retrieval is a
committed corpus). So we captured the skill's *value* — a structured, multi-dimension critique — with
the architecture we already have: a pure function (`src/planner/critique.ts`) that scores a
`DashboardPlan` against the committed top-100 `business_dashboard` norms, the WCAG contrast math, and
the ENFORCED Few rules. Every dimension whose backing norm bucket is `confidence: low` degrades to a
`note` and is excluded from the score — the same low-`n` discipline used everywhere else in the corpus.

Two more backlog items shipped as deterministic tools: **`generate_metric_dictionary`** (Scribe's
Metric-Builder over existing VDS metadata) and **`scan_governance`** (Governance-Scanner's three
read-scope-free checks). One builder change shipped: **sign-based delta color** via Tableau's
bracketed number-format section colors (`[Green]▲;[Red]▼`), additive and `VERIFY-LIVE` (no reference
workbook uses it — gated by a live probe, fallback to arrow-only).

Tool count 27 → **30**.

## What stayed declined (unchanged from ADR-0014)

- VizCritique-Pro's **LLM-judged scoring engine** as a server tool (A-01). We took its numbers, not its judgment.
- Scribe Auto-Doc + Governance's full 7-domain audit — need read introspection scope (`get_view_image`,
  owner / view-count / certification) this publish-oriented server deliberately does not carry.
- Calc-Engine spatial/sets/zone-visibility/parameter-actions — no builder construct consumes them (YAGNI).
- Dashboard-Blueprint filter/device tables — already declined from mined evidence (below the 40% bar).
- Memory-file / scheduled-agent continuity — incompatible with the stateless-per-call architecture (ADR-0005/0007).

## Consequences

- The server can now critique its own output offline, deterministically, with every finding citing a
  mined stat (`n`, `confidence`) — a testable quality gate that needs no live site and no LLM.
- `critique_dashboard` couples to the committed `visual_norms.yaml` shape; a `designVisual` regeneration
  test already guards that shape, and the critique tests read the real committed file so drift fails CI.
- Sign-based delta color is the one unproven construct; it is flag-gated and live-probed, never a default.
