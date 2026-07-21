# ADR-0014 — external skill analysis: applied the deterministic subset, declined the rest

**Status:** Accepted
**Date:** 2026-07-20
**Feature:** Design Excellence follow-on — "safe deterministic bundle" (WCAG contrast, bar sort,
Pulse schema widening)

---

## Context

An external Tableau MCP skill suite publishes six Claude `SKILL.md` playbooks (`Pulse-Blueprint`, `Dashboard-Blueprint`,
`VizCritique-Pro`, `Calc-Engine`, `Governance-Scanner`, `Scribe`) — agent-facing prompt guidance
layered over the **official** `tableau-mcp` server's READ-only tools. They are prompts, not code:
each skill tells an LLM agent how to *reason about* an existing read-only Tableau MCP surface
(list/get calls), not a library this repo can import or a spec this repo's own server implements
against.

This repo's server is architecturally different in three load-bearing ways (see ADR-0005/0007,
`CODEBASE.md`'s A-01/A-02 invariants):

1. **Deterministic, not LLM-judged.** A-01: no LLM call inside the server. Every tool is a pure
   function of its structured input. VizCritique-Pro's core mechanism — an LLM visually scoring a
   rendered dashboard against a rubric — cannot become a server tool without violating this.
2. **Publish-oriented, not read/audit-oriented.** This server builds and publishes workbooks;
   the external skill suite's playbooks sit on top of the official server's *read* surface (list_content,
   get_view_image, get_datasource_metadata, etc.) that this repo does not carry.
3. **Stateless per call, no memory files, no scheduled agents** (ADR-0005/0007) — several skills
   assume a persistent agent session or memory file between runs; that has no analog here.

An analyst pass (recorded in the session scratchpad, not committed) triaged all six skills against
this repo's actual gaps and ranked eight candidate enhancements by effort/risk. This ADR records
the outcome: which candidates were applied now, which were deferred, and why the declined skills
don't fit.

## Decision

**Applied now — the "safe deterministic bundle" (three candidates, all pure/deterministic, all
tested):**

1. **WCAG contrast check in the brand layer** (`src/branding/contrast.ts`,
   `checkBrandContrast()`). Ports VizCritique-Pro's D5 rubric numbers (4.5:1 normal text, 3:1
   large text/UI) as pure relative-luminance + contrast-ratio math — no LLM judgment involved, the
   thresholds and the sRGB formula are both fixed, checkable constants. Wired into `validate_brand`
   as an ADDITIVE `contrastChecks` field; findings are warnings, never hard failures (a brand
   author may deliberately choose low contrast — matches this server's fail-soft philosophy).
2. **Default descending-by-measure sort for ranking bar charts** (`sidecar/twb_builder.py`,
   `_bar_ranking_sort_target()` / BI_DESIGN.md Sec 2.2a). Ports VizCritique-Pro's
   alphabetical-when-ranking anti-pattern finding as a narrow, deterministic default: a bar sheet
   with exactly one non-temporal dimension and one measure gets a `<computed-sort direction='DESC'>`
   element, mirroring the real shape mined from `WB-133`'s "LOD Calcs" worksheet. No LLM
   judgment — a fixed structural predicate on the sheet spec.
3. **Pulse enum/schema widening** (`src/rest/pulse.ts`) — see the Update section in ADR-0011.
   Purely additive tokens/fields from Pulse-Blueprint's own "confirmed API enum reference" table.
   Every new token marked `VERIFY-LIVE`; explicitly does NOT close the live-create-400 gap ADR-0011
   already documents.

**Deferred to the backlog (not applied this slice) — recorded in `design/corpus/GAPS.md` Sec 4:**
YoY/period-comparison KPI delta calc (#1), comparison-period heuristic (#2), metric-dictionary doc
recipe (#6), sign-based conditional delta color (#7), governance-scanner-lite (#8).

**Declined outright — does not fit this architecture:**

- **VizCritique-Pro's full LLM-judged dashboard scoring as a server tool.** Directly violates A-01
  (no LLM inside the server). The rubric NUMBERS (contrast ratios) are portable and were ported;
  the JUDGMENT mechanism (an LLM looking at a rendered image and scoring composition/hierarchy) is
  not, and would require this server to either call an LLM itself or accept LLM-scored input as a
  trusted parameter — both outside this project's determinism invariant. (This repo *has*
  separately used vision-agent review as an offline research/corpus-mining technique — see the
  V-phase commits — but that is explicitly NOT the same thing as shipping LLM judgment inside a
  production MCP tool call.)
- **Scribe's Auto-Doc and Governance-Scanner's full 7-domain audit.** Both require the official
  server's read/introspection scope (`get_view_image`, `list_content` with full metadata, workbook
  read-back) that this repo deliberately does not carry — this server is publish-oriented, not a
  read/audit tool. A governance-scanner-LITE variant using only what `list_content` already returns
  here is backlogged (#8), not declined outright — see GAPS.md Sec 4.
- **Calc-Engine's spatial/sets/zone-visibility/parameter-actions calc recipes.** No matching
  `twb_builder.py` construct exists for any of these (no zone-visibility toggle, no
  parameter-action wiring, no spatial calc emission path) — YAGNI: building the calc-formula
  recipe without the builder support to consume it would be dead code.
- **Dashboard-Blueprint's filter-card and device-layout tables.** Already independently declined in
  `design/corpus/GAPS.md` Sec 2 from real mined evidence (filter zones 17.0-24.6% of the corpus,
  device layouts 23.4% — both below the plan's own 40% "implement automatically" bar). This skill's
  guidance doesn't change that evidence-based conclusion.
- **Memory-file/scheduled-agent continuity patterns** (several skills assume a persistent agent
  session file between runs). Incompatible with this server's stateless-per-call architecture
  (ADR-0005/0007) — there is no server-side session to persist a memory file against.

## Alternatives considered

**Adopt the skills wholesale as agent-facing prompt guidance shipped alongside the server (not as
server code):** rejected for this slice — that would be a documentation/prompt-engineering
deliverable, not a server-code change, and blends two different kinds of "enhancement" (server
tool vs. agent instructions) that this ADR keeps separate. Nothing here prevents a future prompt
authoring pass from doing so.

**Implement the LLM-judged VizCritique score as an opt-in tool, gated behind an explicit flag:**
rejected — A-01 is a hard architectural invariant (CODEBASE.md), not a soft default; an opt-in
LLM-scoring tool still requires the server to either embed a model call or accept a black-box score
as trusted input, both of which this project's existing ADRs (0005, 0007) already rule out for
good reasons (determinism, testability, no hidden non-determinism in publish operations).

## Consequences

**Positive:**

- Three real, evidenced gaps closed with zero new runtime dependencies, zero LLM calls, and full
  test coverage (contrast math test vectors, bar-sort XSD + byte-identical guards, Pulse
  additive-only wire-body guard).
- The declined list is a durable, citable record — a future contributor re-reading these six
  skills doesn't have to re-derive why the LLM-judged and read-scope items don't fit; this ADR
  explains it once.

**Constraints this decision enforces:**

- Any future proposal to port more of VizCritique-Pro's rubric MUST stay in the "portable fixed
  numbers/predicates" lane (like contrast ratios and the sort rule) — porting its LLM-judgment
  mechanism itself requires revisiting A-01, not just this ADR.
- The Pulse widening's `VERIFY-LIVE` markers must not be silently dropped in a future edit without
  an actual live fixture confirming them — see ADR-0011's existing constraint.
