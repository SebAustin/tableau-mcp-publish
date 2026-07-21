# ADR-0013 — A committed, deterministic design corpus instead of an embeddings/vector-store RAG

**Status:** Accepted
**Date:** 2026-07-18
**Feature:** Design-excellence corpus + theme layer (Phase E6, slice D0)

---

## Context

User feedback on the live demo: the generated dashboards/stories work but are "not user friendly
and not beautiful." The user supplied 4 exemplary Tableau Public Superstore dashboards
(`WB-117`/`WB-117`, `WB-118`/`WB-118`,
a fourth (undownloadable) exemplar, `WB-114`/`WB-114`) and **explicitly
authorized downloading them from Tableau Public**, analyzing them, and building a "RAG of Tableau
design knowledge" so output becomes eye-candy, user-friendly, and enterprise-branded.

The verified gap: `twb_builder.py` emits **zero** styling today — bare `<style/>` at the
worksheet and dashboard build sites, no `<zone-style>`, no spacers, no chrome control
(gridlines/zeroline/axis-ticks/mark-labels), no styled datalabels, no custom palettes, no actions.
`brand.yaml`'s `typography.ban` and `palette.good`/`palette.bad` are transported through the wire
models but never consumed by the builder. The references get their entire look from a small,
mirrorable XML vocabulary — `<zone-style>` box model, `type-v2='empty'` spacers, multi-run text
zones, workbook/worksheet `<style-rule>` chrome removal, styled datalabels, custom
sequential/diverging palettes, and `<action>`/`<edit-parameter-action>` elements — cataloged from
10 real on-disk workbooks (3 downloaded exemplars + 7 pre-existing reference workbooks; see
`design/references/README.md`).

Two questions needed answers before any styling code could be written:

1. **What does "a RAG of Tableau design knowledge" mean in a stateless MCP server?** A
   traditional RAG (embeddings + vector store + runtime similarity search) is a new class of
   infrastructure and nondeterminism for a project whose planner is explicitly documented as
   "deterministic given explicit inputs" (ADR-0005) and whose tools are stateless request/response
   (ADR-0006, ADR-0007).
2. **How do we guarantee the corpus never invents Tableau XML the way a hallucinating LLM might?**
   Every other slice of this project's design work (dashboard layout, KPI tiles, story arcs) has
   followed a strict "mirror real, XSD-verified workbook XML — never invent a construct" discipline.
   A design corpus that fabricated colors, paddings, or action shapes would break that discipline
   silently, at the one point (styling) where "looks plausible but isn't real Tableau XML" is
   hardest to catch by inspection.

## Decision

**"RAG" is implemented as a committed, human-reviewable design corpus with deterministic
tag-based retrieval — no embeddings, no vector store, no runtime dependency, fully unit-testable.**

```
design/
  references/          # downloaded .twbx provenance (raw files gitignored, README.md kept)
  corpus/
    SCHEMA.md           # corpus schema + provenance rules
    themes/             # 4 hand-curated presets: executive_dark, executive_light, analyst_clean, operational_plain
    recipes/            # mined raw constructs: zone_styles, chrome_rules, actions, palettes, text_zones
```

- **Miner** (`sidecar/design_miner.py`, dev-only CLI, zipfile + lxml + PyYAML): XPath-extracts the
  vocabulary from real `.twb`/`.twbx` workbooks. Every recipe entry carries `source`, `sha256`,
  and `xpath` — the miner copies attribute values verbatim and never invents, renames, or
  normalizes beyond a whitespace strip. Uses the same XXE-safe `lxml.etree.XMLParser` configuration
  already proven in `tests/test_twb_schema_validation.py` (`resolve_entities=False`,
  `no_network=True`, `load_dtd=False`), plus a zip-slip guard on `.twbx` archive members (fail
  closed: any unsafe member name rejects the whole archive).
- **Themes** are hand-curated *from* the mined recipes, never invented: `tests/designCorpus.test.ts`
  mechanically asserts every hex color literal in every theme file appears somewhere in
  `recipes/*.yaml` ("theme-cites-recipe") — the same "never invent XML" discipline used for the
  builder itself, lifted to the corpus layer. Every theme's `provenance` list documents which of a
  fixed 10-workbook allowlist each construct came from.
- **Retrieval** will be a pure `selectTheme(audience, personaName?, artifact)` function (a later
  slice — D5) over each theme's `tags` — persona match > audience match > default `analyst_clean`,
  alphabetical tie-break — deterministic in the same style as `applyAudienceClamps` (ADR-0005).
  This D0 slice only guarantees the corpus has enough tag coverage (every `audience` value resolves
  to ≥1 theme) for that function to be implementable without any fallback gaps.
- Theme resolution will happen in `designDashboard.ts` (the existing `brand.yaml` I/O point, per
  ADR-0006); the **resolved** theme block will be embedded in the plan so `build_from_plan` needs
  no corpus filesystem I/O and propose→build cannot drift — matching how `brand.yaml` resolution
  already works.

## Alternatives considered

**Embeddings + vector store (the literal reading of "RAG"):** rejected. This project's planner and
tool layer are deliberately stateless and deterministic (ADR-0005, ADR-0007); a vector store
introduces a new runtime dependency (an embedding model call, a vector DB or in-process index),
nondeterministic nearest-neighbor retrieval (the same query could surface a different "closest"
style reference across runs depending on index state or floating-point tie-breaking), and a class
of bugs (silent retrieval drift as the corpus grows) this project has no existing tooling to catch.
A tag-based lookup over a small, fixed set of hand-curated themes is exhaustively testable
(`tests/designCorpus.test.ts` enumerates every audience and asserts coverage) in a way approximate
nearest-neighbor search is not.

**Skip the corpus, hand-write theme values directly in `twb_builder.py`:** rejected — this is
exactly the failure mode the plan's "mirror real, XSD-verified XML" discipline exists to prevent.
Hand-typing "a plausible navy" or "a plausible padding" without a real, sourced Tableau workbook
behind it is indistinguishable, on review, from an invented value; the corpus's provenance
requirement (`source`/`sha256`/`xpath` on every entry, `theme-cites-recipe` test) makes "this value
is real" a machine-checkable property instead of a code-review judgment call.

**Mine constructs at runtime from the live `.twbx` files, on every `design_dashboard` call:**
rejected — would require shipping the (large, several-MB) reference `.twbx` files with the package,
add parse latency to every tool call, and reintroduce a runtime dependency on `lxml` in the
TypeScript-hosted MCP server's request path. Mining once, offline, into a small committed YAML
corpus keeps the runtime path dependency-free and fast, matching every other config-at-rest pattern
in this project (`brand.yaml`, the vendored XSD).

## Consequences

**Positive:**

- The corpus is fully offline-testable: `sidecar/tests/test_design_miner.py` round-trips a
  hand-made fixture through all 5 extractors with correct provenance, and
  `tests/designCorpus.test.ts` zod-validates every committed recipe/theme file and mechanically
  checks the provenance allowlist and theme-cites-recipe invariant — no live Tableau site, no API
  key, no network call is needed to verify the corpus is internally consistent.
- `design_miner.py` is re-runnable: widening the input set (more reference workbooks) or
  re-curating a theme is a local, offline, git-diffable operation with no infrastructure to
  provision.
- The corpus documents its own gaps rather than hiding them: `cornerRadius` (a legal plain
  `<format attr="corner-radius">` per the TWB XSD) is deliberately absent from every D0 theme
  because no real mined zone-style in the 10-workbook corpus uses it — every theme file states this
  explicitly rather than a later reader having to guess whether it was an oversight.

**Constraints this decision enforces:**

- Every future theme edit or addition must cite real recipe provenance — `theme-cites-recipe` makes
  "I eyeballed a nice hex value" fail CI, the same way `test_zone_style_is_last_child_of_zone`-style
  XSD tests make an invented XML shape fail CI elsewhere in this project.
- The 10-workbook allowlist is a closed set for this slice (`WORKBOOK_ALLOWLIST` in
  `tests/designCorpus.test.ts`); adding a new source workbook to the corpus means updating both the
  allowlist and `design/references/README.md`'s provenance table in the same change, keeping the
  "which real file did this come from" question always answerable.
- Retrieval must stay a pure function over `tags` (D5, out of scope here) — no persisted index, no
  approximate/probabilistic matching — so `selectTheme` remains as unit-testable and
  audit-friendly as `applyAudienceClamps` and the rest of the planner.
