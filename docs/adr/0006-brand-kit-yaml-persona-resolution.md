# ADR-0006 — `brand.yaml` as a defaulted YAML file, personas resolved only at the tool layer

**Status:** Accepted
**Date:** 2026-06-29
**Feature:** Branding system + design-excellence layer (Phase E1)

---

## Context

`design_dashboard` needed a way to apply organization-specific color palettes, typography, number
formats, and audience presets ("the CEO wants 3 sheets and concise wording") without hardcoding
them into the planner or requiring a code change per customer/team. Two questions needed answers:

1. **Where does brand configuration live, and in what format?** Options: a TypeScript config
   module, JSON, or YAML; embedded defaults vs. a required file.
2. **Where does persona resolution happen?** Options: inside the pure planner, or at the MCP tool
   layer before the planner is invoked.

## Decision

**`brand.yaml` at the repo root, validated by a zod schema where every field is optional and
defaulted, loaded and resolved only by `src/tools/designDashboard.ts` (and, when branding a build,
`src/tools/buildFromPlan.ts`) — never by the planner or the sidecar directly.**

Specifically:

- `BrandFileSchema.parse({})` (an empty object) already produces a fully-populated, valid
  `BrandFile` — "file absent" and "file present but sparse" are handled identically, both falling
  back to defaults that mirror the color choices already audited in the reference `.twbx` files.
- `personas` is a free-form `Record<string, PersonaOverrides>` map, so adding a persona is a YAML
  edit, not a code change. Each persona's `base` must resolve to one of the planner's existing
  `AudienceEnum` values, keeping the branding layer downstream of (never mutating) the planner's
  contract.
- `loadBrand()`/`resolvePersona()` are the **only** I/O in the branding layer. `design_dashboard`
  reads the file once, resolves the persona (audience + `maxSheets`/`chartDeny`/`kpiEmphasis`/
  `preferredArtifact`/`tone` overrides), and passes plain, already-resolved values into
  `generatePlan()`. The planner itself never touches the filesystem.

## Alternatives considered

**Embed brand config in TypeScript (a `.ts` module):** rejected — a non-developer editing colors
or adding a persona would need to touch source code and understand the build step, defeating the
goal of a config surface a brand/design team member can edit directly.

**JSON instead of YAML:** rejected — YAML supports comments, which `brand.yaml` uses extensively to
document every field inline (the file is meant to be self-documenting for a non-engineer editor).
Adds one new runtime dependency (`yaml@2.9.0`).

**Resolve personas inside the planner:** rejected — would require the planner (previously pure,
filesystem-free, and independently unit-tested per ADR-0005) to perform file I/O, breaking its
"deterministic given explicit inputs" testability and coupling it to a specific config file format.

## Consequences

**Positive:**

- `validate_brand` can report `{ valid, warnings, personas, summary }` without ever throwing,
  because a malformed file is a validation-layer concern (caught by `loadBrand`), not a planner
  concern.
- The planner's own tests (`tests/planner*.test.ts`) remain filesystem-free and fast; brand/persona
  wiring gets its own focused test files (`tests/branding.test.ts`, `tests/builderBrand.test.ts`).
- Five personas ship as defaults; adding a sixth is a YAML edit with no deploy.

**Constraints this decision enforces:**

- Every new persona override field must be threaded explicitly through `designDashboard.ts` into
  `PlanInput` (redeclared enums, not imported, to avoid the planner depending on `branding/`) —
  there is no implicit pass-through.
- `build_from_plan` re-reads `brand.yaml` independently (it does not receive the resolved brand
  object from `design_dashboard`, since the two tool calls are stateless and may be minutes apart)
  — a `brand.yaml` edit between `design_dashboard` and `build_from_plan` calls changes the applied
  brand, which is the intended "single source of brand truth" behavior, not a bug.
