# ADR-0007 — `design_dashboard` returns a confirmable `DashboardProposal`, not a raw plan

**Status:** Accepted
**Date:** 2026-07-16
**Feature:** Propose→confirm loop (Slice 5, on top of ADR-0005's stateless-planner decision)

---

## Context

`design_dashboard` originally returned a `DashboardPlan` — a structural JSON object (sheet specs,
mark types, layout) meant for `build_from_plan` to consume. That shape is precise but not
human-readable: an agent relaying it to a user would either dump raw JSON or hand-write a summary
that could drift from what the plan actually contains. There was no built-in "let the user review
and revise before anything publishes" step in the tool contract itself — an agent had to invent
one, inconsistently, on top of a bare plan.

## Decision

**`design_dashboard` returns a `DashboardProposal` (`kind: "proposal"`) for every mode except
`interview`, derived from the `DashboardPlan` by a pure, deterministic projection
(`buildProposal()` in `src/planner/proposal.ts`). The proposal embeds the plan verbatim in its
`plan` field. `build_from_plan` still only accepts a `DashboardPlan` — the agent must extract
`proposal.plan` before calling it.**

The proposal carries:
- `summary` — a one-paragraph natural-language description, including persona/brand provenance and
  tone (`concise` trims to the first sentence + layout line; `detailed` keeps the full text).
- `kpiStrip` — one entry per KPI tile, with primary/comparison/delta measures and up/down-is-good
  direction.
- `views` — one entry per chart sheet, with a human-readable encoding summary (e.g. "bar: Sales by
  Category, colored by Segment").
- `layoutSummary` — a sentence describing the dashboard's zone layout.
- `storyOutline` (optional) — ordered captions when the plan carries a `storyArc`.
- `openQuestions` (optional) — honest callouts: unresolved placeholder tokens, a persona's
  `preferredArtifact` this build path doesn't produce, etc.

The agent-facing contract is a two-step loop: present the proposal → on "confirm", call
`build_from_plan(proposal.plan)` verbatim; on "change X", re-call `design_dashboard` in `directed`
mode with updated `directions` for a fresh proposal. No state is held server-side between calls —
this is layered on top of, not a replacement for, ADR-0005's stateless-planner decision.

## Alternatives considered

**Return the raw `DashboardPlan` and let each calling agent format its own summary:** rejected —
produces inconsistent, potentially inaccurate summaries across agents/prompts, and duplicates
formatting logic that belongs in one deterministic, unit-tested place.

**Make `design_dashboard` itself hold a pending-proposal state and accept a `confirm: true` flag to
publish:** rejected — reintroduces server-side session state, which ADR-0005 explicitly rejected
for the interview mode and which an MCP stdio process has no reliable mechanism for (no shared
store across restarts or concurrent agent sessions).

**Have `build_from_plan` accept either a `DashboardPlan` or a full `DashboardProposal` (and unwrap
it internally):** rejected for this phase — keeping `build_from_plan`'s input contract to exactly
`DashboardPlan` keeps its own validation (`schemaVersion`/`kind: "plan"` guard, placeholder-token
rejection, audience re-validation) unambiguous; the agent extracting `proposal.plan` is a one-line
step already implied by "pass it verbatim."

## Consequences

**Positive:**

- Every proposal is reproducible and unit-testable: `buildProposal(plan)` is pure, so
  `tests/planner-slice5.test.ts` asserts exact summary/kpiStrip/views/layoutSummary shapes without
  a live model or a live site.
- The confirm step is explicit and auditable: `build_from_plan` is still the only side-effecting
  tool in the family, and it is always a separate call from `design_dashboard`.
- `openQuestions` gives the agent a structured place to surface caveats (placeholder fields, an
  unsupported `preferredArtifact`) instead of silently proceeding or silently dropping the
  preference.

**Constraints this decision enforces:**

- Any new plan-level field an agent should see in a human-readable form must get a corresponding
  projection in `buildProposal()` — the proposal is not a passthrough of the plan.
- `DashboardProposalSchema` requires at least one `views` entry; an all-KPI-tile plan synthesizes a
  minimal view from its first sheet so the schema still validates (a degenerate case documented in
  `proposal.ts`, not expected in well-formed exec plans).
