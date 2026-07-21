# ADR-0011 — Pulse tools shipped code-complete against a best-effort payload, honestly flagged pending a live fixture

**Status:** Accepted (code-complete; live proof blocked)
**Date:** 2026-07-17
**Feature:** Tableau Pulse metric definitions + metrics (Phase E3)

---

## Context

Tableau Pulse has a documented REST surface, but Tableau's own official reference implementation
(`pulse-api-utilities`) only ever **clones an existing definition it fetches**, it never
constructs a `create-definition` request body from scratch. The `basic_specification` internals
(and several enum families — granularity, range, comparison, sentiment, number format) are
therefore specified only by example in the one sample body the reference repo ships, not by a
formal schema. A first attempt to build `POST /api/-/pulse/definitions` from that one example
returned a bare `400` against a real Cloud site with no field-level detail in the error.

The project's stated invariant (validate at the boundary, fail loud, never guess silently) made an
option of simply shipping nothing until the shape was locked untenable — the E3 milestone otherwise
had no path to make progress without user-provided Pulse-UI access to create a de-risking fixture.

## Decision

**Ship all four Pulse tools now, built to mirror Tableau's official example body field-for-field,
with every token beyond the few the reference material explicitly confirms marked `VERIFY-LIVE` in
both code comments and the tool descriptions surfaced to the agent — and state the live-blocked
status honestly in `ACCEPTANCE.md` and `docs/tool_reference.md` rather than implying the tools are
fully proven.**

- `buildCreateDefinitionBody()` (`src/rest/pulse.ts`) replicates the exact nesting Tableau's
  reference repo calls out as easy to get wrong: `extension_options` / `representation_options` /
  `insights_options` / `comparisons` / `datasource_goals` / `related_links` / `certification` are
  top-level siblings of `specification`, not nested inside it.
  `create_pulse_definition` additionally runs a **VDS-backed pre-flight check**
  (`validatePulsePreflight()`) that verifies the measure and time-dimension fields actually exist
  on the datasource and that the time dimension's VDS `dataType` looks date-like — this catches an
  entire class of caller mistakes (typo'd field name, non-date time dimension) independently of
  whether the Pulse-specific payload shape itself is fully correct.
- Every enum (`PulseGranularitySchema`, `PulseRangeSchema`, `PulseComparisonSchema`, etc.) keeps
  only the one confirmed default from the reference sample and documents every other member as
  unconfirmed, so a caller who sticks to defaults is on the most-validated path.
- The de-risking plan is explicit and recorded, not left implicit: create one definition in the
  Pulse UI, `GET` it back, and use the real response to correct `buildCreateDefinitionBody()` /
  lock a fixture — tracked as the concrete next step in `ACCEPTANCE.md`'s E3 section.

## Alternatives considered

**Defer all Pulse tools until a live fixture is available:** rejected — would block the entire E3
milestone indefinitely on a resource (Pulse UI access to manually create one definition) outside
this repo's automated control, with no incremental value shipped in the meantime. Shipping
code-complete, pre-flight-validated tools that are honestly flagged VERIFY-LIVE lets an operator
who *does* have Pulse UI access attempt live creation today, while making the current limitation
impossible to miss.

**Reverse-engineer the payload shape from Tableau's web client network traffic instead of the
official reference repo:** rejected as the primary source — the official `pulse-api-utilities`
repo is Tableau's own maintained reference and the more durable source to track for future
changes; network-traffic reverse-engineering was not pursued as it was unnecessary given the
official repo's field names, and would be a weaker citation for future maintainers.

**Suppress the VERIFY-LIVE caveats to present the tools as fully proven, matching every other
publish tool's confidence level:** rejected — directly conflicts with this project's standing
"honest caveats over false confidence" discipline (mirrored in `schedule_refresh`'s always-included
Bridge note and `create_live_datasource`'s key-pair rejection); a Pulse tool that silently fails in
production because of an unconfirmed enum token is worse than one whose docs say so upfront.

## Update (2026-07-20) — external skill enhancement #5: additive schema widening, still not a fix

An external agent-skill review (`docs/adr/0014-external-skill-analysis.md`) cross-checked this
module against the external skill suite's Pulse-Blueprint skill, which ships its own "confirmed" API enum
reference. Four purely additive widenings landed as a result, every new token still marked
`VERIFY-LIVE`:

- `PulseGranularitySchema` gained `GRANULARITY_BY_FISCAL_QUARTER` / `GRANULARITY_BY_FISCAL_YEAR`;
  `PulseComparisonSchema` gained `TIME_COMPARISON_FISCAL_YEAR_AGO_PERIOD`.
- A new `PulseCurrencyCodeSchema` (`USD`/`EUR`/`GBP`/`JPY`/`UNSPECIFIED`, bare-suffix — same
  in/out convention as `PulseAggregationSchema`) backs an optional `currencyCode` input field,
  emitted as `representation_options.currency_code` only when supplied.
- A new `PulseInsightTypeSchema` (the 8-value `INSIGHT_TYPE_*` family: `CURRENT_TREND` /
  `NEW_TREND` / `TOP_DRIVERS` / `TOP_DETRACTORS` / `BOTTOM_CONTRIBUTORS` / `RISKY_MONOPOLY` /
  `UNUSUAL_CHANGE` / `RECORD_LEVEL_OUTLIERS`) backs an optional `insightSettings` array, emitted as
  `insights_options.settings` entries only when supplied.
- Optional `rowLevelIdField` / `rowLevelNameField` / `rowLevelEntityNames` input fields, emitted as
  `specification.row_level_id_field` / `row_level_name_field` / `row_level_entity_names` only when
  supplied.

**This is schema-readiness only — it does NOT close the live-blocked gap above.** Pulse-Blueprint
itself is explicit that Tableau's official MCP only exposes READ-only Pulse endpoints and that
"Pulse metric creation requires the Tableau UI" — it never constructs a `basic_specification` body
from scratch, so it corroborates nothing about whether the underlying REST `POST` accepts these
fields. Every new field/token is additive (omitted by default, reproducing the pre-widening wire
body byte-for-byte — see `tests/pulse.test.ts`'s "additive-only guard") and still requires the same
create → `GET` → correct-the-client de-risking step described above before any of it can be trusted
in production.

## Consequences

**Positive:**

- An operator with Pulse UI access has a concrete, documented path to close the gap (create → GET →
  correct the client) rather than starting from zero.
- The VDS pre-flight check provides real value today regardless of the Pulse-specific payload
  shape's final correctness — bad field references are caught before ever reaching Pulse.
- `rest/pulse.ts`'s retry policy is deliberately more conservative than the rest of the codebase
  (GET-only — see ADR-0008) specifically *because* of this shape uncertainty, so a retried DELETE
  can't compound an already-unconfirmed failure mode.

**Constraints this decision enforces:**

- `docs/tool_reference.md` and `README.md` must both state the live-blocked status of Pulse
  creation explicitly wherever the tools are described — silently dropping this caveat in future
  doc edits would misrepresent tool maturity to an agent/user.
- Any code change to `buildCreateDefinitionBody()`/`buildCreateMetricBody()` after the fixture is
  locked should update this ADR's status from "code-complete; live proof blocked" to "Accepted" and
  remove the now-resolved VERIFY-LIVE markers it corrects.
