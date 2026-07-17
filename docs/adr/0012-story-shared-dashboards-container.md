# ADR-0012 — Stories are `<dashboard type='storyboard'>` elements inside one shared `<dashboards>` container

**Status:** Accepted
**Date:** 2026-07-17
**Feature:** Tableau Stories (Phase E4)

---

## Context

A Tableau Story is, structurally, a special kind of dashboard (`<dashboard type='storyboard'>`)
that flips between "story points," each pointing at an existing worksheet or regular dashboard.
`build_from_plan` needed to add an optional story to the same `.twbx` it already builds a regular
dashboard into, driven by `plan.storyArc` (emitted deterministically by the planner when a persona
prefers `"story"` artifacts or the business question uses narrative language — see
`docs/architecture.md`'s planner section).

While implementing this, XSD validation surfaced a real structural bug: the existing builder code
wrote each dashboard-producing call (`_build_dashboard` for the regular dashboard, then a
would-be-separate call for the story) into its **own** `<dashboards>` wrapper element, producing
two sibling `<dashboards>` elements in the `.twb`. The official TWB XSD (vendored and gated in
`test_twb_schema_validation.py` per the ecosystem-review ADR) only allows **one** `<dashboards>`
element per workbook.

## Decision

**A workbook has exactly one `<dashboards>` container. Every regular dashboard is appended to it
first, then every story (`<dashboard type='storyboard'>`) is appended after — both kinds are
siblings inside the same element, differentiated only by the `type` attribute.**

- `_build_story()` returns a `<dashboard type='storyboard'>` `ET.Element`, structurally parallel to
  `_build_dashboard()`'s regular-dashboard element — same `<style/>`/`<size>`/`<zones>`/
  `<simple-id>` skeleton, different internal zone layout (title / flipboard-nav / flipboard, with a
  `<story-points>` list, vs. a regular dashboard's tiled worksheet zones).
- The title/flipboard-nav/flipboard zones use a bare `type='…'` attribute (not `type-v2`, which
  every other zone kind in this module uses) — verified against a real Tableau-authored story
  export and confirmed schema-valid via the XSD's `anyAttribute` wildcard, so no compromise was
  needed between fidelity and validity.
- Every `captured_sheet` a story point references is validated against the actual set of worksheet
  + regular-dashboard names already built into the workbook — enforced at three independent layers
  for defense in depth: the planner's `buildStoryArc()` only emits references to its own plan's
  sheet titles; `build_from_plan`'s `assertStoryArcCapturedSheetsExist()` re-checks before calling
  the sidecar; `_build_story()` raises `ValueError` (listing every valid name) as the final
  backstop against the actual built workbook.

## Alternatives considered

**A separate `.twbx`/workbook just for the story:** rejected — Tableau's own convention (and every
reference story workbook available for inspection) keeps a story alongside the dashboards it
narrates in one workbook; splitting them would double the publish calls and separate two
conceptually-linked artifacts an agent/user expects to find together.

**Keep the two-`<dashboards>`-wrapper structure and special-case the XSD gate to tolerate it:**
rejected outright once discovered — the official schema is authoritative and was already adopted
as a fidelity gate (ecosystem-review A1); special-casing the test to accept invalid output would
defeat the purpose of vendoring the schema at all. The one-container fix was strictly the correct
resolution, not a workaround.

**Validate `captured_sheet` references only once, at the outermost tool layer
(`build_from_plan`):** rejected as insufficient on its own — a caller invoking the sidecar directly
(bypassing the TS tool, as some tests and future integrations might) would lose that check
entirely. Three-layer validation (planner emission guarantee, tool-layer re-check, builder-layer
backstop) matches the project's existing "defense in depth" pattern used for audience-invariant
re-validation (ADR from the original M6 build).

## Consequences

**Positive:**

- Every generated `.twbx` — with or without a story — validates against the official TWB XSD
  (`test_twb_schema_validation.py`), and a real story workbook has been published and confirmed
  live on Tableau Cloud (`ACCEPTANCE.md`'s E4 section).
- The three-layer `captured_sheet` validation means a broken story reference is always caught
  before Tableau Cloud ever sees the file, with an error message listing every valid sheet name at
  whichever layer catches it first.
- Regular dashboards and stories share `_build_dashboard()`'s/`_build_story()`'s common zone-XML
  helpers, so future dashboard-layout changes (e.g. a new layout grammar) do not need a parallel
  story-specific implementation for the parts that are genuinely shared (canvas `<size>`,
  `<simple-id>` allocation, the base `<zones>` skeleton).

**Constraints this decision enforces:**

- Any future new "dashboard-shaped" artifact (if Tableau ever adds another `<dashboard type='…'>`
  variant this project wants to emit) must also append into the same single `<dashboards>`
  container — this is now a structural invariant, not an implementation detail, and violating it
  reintroduces the exact XSD failure this ADR fixed.
- `dashboard_index`-based `simple-id` UUID allocation must stay disjoint across regular dashboards
  and stories (both draw from one shared counter) to avoid a UUID collision inside one workbook.
