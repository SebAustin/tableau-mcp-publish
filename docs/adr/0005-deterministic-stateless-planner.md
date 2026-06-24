# ADR-0005 — Deterministic, stateless server with LLM intelligence in the calling agent

**Status:** Accepted  
**Date:** 2026-06-24  
**Feature:** Prompt-driven authoring (`design_dashboard` / `build_from_plan`)

---

## Context

The prompt-driven authoring feature must translate a natural-language business question and an
audience enum into a concrete `DashboardPlan` (sheet specs, mark types, layout, canvas size).
Two design dimensions needed a decision:

1. **Where does the chart-selection intelligence live?** Options: embed an LLM call inside the
   server, or use deterministic rule tables in the server and let the calling agent supply the
   business intelligence.

2. **How does the interview mode work?** The agent needs to collect clarifying answers from the
   user and then produce a plan. Options: hold conversation state in the server across calls, or
   keep each call stateless and have the agent supply the full context on every call.

The MCP server runs as a stdio subprocess. It has no streaming, no session memory, and no
LLM client. Adding one would require a second `ANTHROPIC_API_KEY` (or equivalent), introduce
latency inside a tool call, and make the planner output non-deterministic — breaking the ability
to assert plan shapes in CI without a live model.

The interview mode requires the agent to relay questions to the user and collect answers. This is
inherently a multi-turn user interaction that belongs to the agent's conversation loop, not to a
server endpoint.

---

## Decision

**The server is deterministic and stateless. The LLM intelligence lives in the calling agent.**

Specifically:

- `design_dashboard` is a pure function of its inputs. Given the same `(mode, audience,
  businessQuestion|directions|answers, fieldHints)` it returns byte-identical output. No LLM
  call, no `Date.now()`, no random seed.

- The planner pipeline (`fields.ts` → `marks.ts` → `audience.ts` → `plan.ts`) applies
  rule tables (keyword regexes, audience constraint tables, a 6-step clamp algorithm) drawn
  from `BI_DESIGN.md`. All rules are expressed as TypeScript pure functions.

- The interview mode is a **bounded, stateless two-call contract**:
  - Call 1: `design_dashboard(mode: "interview")` → returns `ClarifyingQuestions` (3–7 questions
    selected deterministically from a fixed bank based on which inputs are unknown).
  - Call 2: `design_dashboard(mode: "interview_followup", answers: {...})` → returns `DashboardPlan`.
  - The server stores nothing between these calls. The agent supplies the original inputs plus
    the collected answers on the second call. Question IDs are stable, content-derived strings
    so the agent can map answers without any server-held state.

- `build_from_plan` is the only side-effecting tool in the feature. It is separated from
  `design_dashboard` so the agent can show the plan to the user, accept edits, and build —
  without re-running the expensive publish on every refinement.

---

## Alternatives considered

### LLM call inside the server

The server could call an LLM API to translate business questions into sheet specs, allowing richer
and more flexible chart selection.

Rejected because:
- Requires a second API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) not present in the baseline.
- Makes the planner output non-deterministic; the MA-1..MC-2 acceptance criteria (CI-assertable
  plan shapes) would require a live model in CI.
- Adds latency and cost inside a synchronous MCP tool call.
- The calling agent already has an LLM; adding a second one is redundant.

### Server-held conversation state

The server could maintain a session ID and store conversation history between calls, allowing an
open-ended multi-turn interview inside the server.

Rejected because:
- MCP is a request/response protocol; there is no session-persistence mechanism.
- The stdio process model means there is no shared in-memory store across calls from different
  agents or restarts.
- Multi-turn user interaction belongs in the agent's conversation loop, where it can relay
  questions naturally and accept free-form answers.
- A stateless two-call contract (questions → plan) covers the use case without any server
  state mechanism.

---

## Consequences

**Positive:**

- Every planner output is unit-testable in CI without a live LLM or PAT. The MA-1..MC-2 and
  MB-1..MB-2 acceptance criteria all run headlessly.
- The server has no new secret dependencies. The existing `SERVER`/`SITE_NAME`/`PAT_NAME`/
  `PAT_VALUE` env vars are the complete auth surface.
- `build_from_plan` is the only tool that writes to Cloud in the feature, making the side-effect
  boundary explicit and auditable.
- The plan is a serializable, versioned JSON object (`schemaVersion: 1`) that the agent (and user)
  can inspect and edit before publishing.

**Constraints this decision enforces:**

- The server does no metadata introspection. Field names in `fieldHints` must be supplied by the
  caller (typically obtained from `@tableau/mcp-server`).
- Chart selection quality is bounded by the deterministic heuristics. Ambiguous or unusual
  business questions may produce plans with placeholder tokens; the agent is responsible for
  filling them in.
- The interview is hard-capped at two `design_dashboard` calls. Further refinement is done by the
  agent mutating the plan object and calling `build_from_plan`.

**Versioning:**

`schemaVersion` in `DashboardPlan` and `ClarifyingQuestions` is `1`. Both `design_dashboard`
(producer) and `build_from_plan` (consumer) enforce this at parse time. Increment `schemaVersion`
only when a rule change would break an existing unit test.
