# ASSUMPTIONS.md

Decisions and deviations from the original build spec, with rationale. The spec's pinned versions
were validated against reality (June 2026); where they were stale or wrong, they were corrected.

## Confirmed with the user

- **Source control:** create + push `SebAustin/tableau-mcp-publish` via `gh`, **private first**,
  flip public on explicit word.
- **Live publish:** authorized — one real end-to-end publish to a Tableau Developer Program site,
  using a PAT the user supplies at the demo step. The PAT is never logged and `.env` is gitignored.

## Deviations from the spec (validated)

| Area | Spec | Decision | Why |
|---|---|---|---|
| MCP SDK | `@modelcontextprotocol/sdk@1.6.0`, low-level `new Server()`+`setRequestHandler()` | Pin `1.29.0`, use `McpServer.registerTool()` returning `content` + `structuredContent` | 1.6.0 is long stale; the official tableau-mcp uses `^1.12.1`. The modern `registerTool` API is the supported path and gives structured output for free. `inputSchema`/`outputSchema` are **ZodRawShape** (an object of zod schemas), not `z.object(...)`. |
| zod | `3.23.8` | `^3.25` (latest 3.x), still **v3** | SDK 1.29 resolves cleanly against current zod 3.x. zod **v4 breaks the SDK**, so we stay on v3. |
| REST API version | `3.24` | default `3.28`, configurable via `TABLEAU_API_VERSION` | 3.24 is Dec-2025 era; current Tableau Cloud is ~3.28–3.29. |
| sidecar Python | `requires-python = ">=3.12,<3.13"` | `">=3.12,<3.14"` | Local interpreter is 3.13.9; the `<3.13` cap would block `uv sync`. CI still pins 3.12.4. pandas/pantab/Hyper all support 3.13. |
| Hyper typing | manual pandas-dtype→Hyper-type map | rely on pantab auto-inference; assert types via round-trip test | pantab 5.x infers Hyper column types from pandas dtypes; the manual map is redundant and error-prone. |
| `.tdsx` packaging | "use tableau-document-api if it cleanly supports creation" | hand-build the zip (`.tds` XML + `Data/<name>.hyper`) | No maintained Document-API library creates `.tdsx`; hand-building a `connection class='hyper'` `.tds` + zip is the reliable path. |
| `.twb` marks | bar / line / text / map | MVP = **bar / line / text(table)**; **map = experimental/best-effort** | Hand-authored workbook XML is fragile; geographic encoding is the most brittle. Bar/line/text are reliably generatable. |
| lint tooling | `eslint src/` (no eslint pinned) | add ESLint 9 flat config + `typescript-eslint` to devDeps | The spec's `lint` script referenced eslint without declaring it; added so `npm run lint` actually runs. |
| Node | 22.7.5 | CI matrix **22.x / 24.x / 26.x**; local dev on Node 26 | `actions/setup-node` has no `22.7.5` in its linux-x64 manifest (CI fails to find it), so CI uses `22.x` (latest Node 22) to cover the Node 22 line the spec intended, plus 24.x and the local 26.x. |

## Connection drivers (sidecar)

`snowflake-connector-python` and `psycopg[binary]` are **optional extras** (`connectors`), not core
deps, so CI installs and tests the Hyper/.tdsx/.twb authoring path without needing database
drivers. CSV is always available. Documented in the sidecar README.

## Live-render caveat

Generated `.twb` files are validated as well-formed XML containing the required
datasource/worksheet/`datasource-dependencies` elements. Full "does it render marks in Tableau"
validation is only possible against a live site and is performed once at the gated demo step.

---

## Prompt-Driven Authoring Feature — Additional Assumptions (added 2026-06-24)

### A-01 — `design_dashboard` is rule-based, not LLM-backed

**Assumed:** The `design_dashboard` tool produces `DashboardPlan` and
`ClarifyingQuestions` objects using deterministic rules (question templates keyed by
audience, keyword-to-mark-type mappings, audience constraint tables).  No LLM API call
happens inside the MCP server process.

**Why:** MCP tools must be predictable, testable in CI without live API keys, and
consistent between calls with identical inputs.  The intelligence about business context
comes from the AI agent (Claude, Cursor) that calls the tool.

**How to override:** If a future version embeds an LLM call, add a
`DASHBOARD_LLM_PROVIDER` env var (`"none"` default), document the new dependency, gate
on the API key at startup, and update the success criteria to cover the LLM-off path.

### A-02 — Interview mode resolves in at most two `design_dashboard` calls

**Assumed:** One call returns questions; one call with answers returns a plan.  No
open-ended multi-turn loop inside the tool.

**Why:** MCP is a synchronous request/response protocol.  The agent owns conversation
state.  A bounded, stateless contract is testable and prevents the server from
accumulating session state.

**How to override:** Introduce a `sessionId` on the tool output, allow in-memory partial
state keyed by that ID, and add a session TTL or `cancel_interview` tool.

### A-03 — `openpyxl` is added as a non-optional sidecar dependency

**Assumed:** `openpyxl` is added to `sidecar/pyproject.toml` unconditionally.

**Why:** Excel is a first-class requested format; `pandas` already uses `openpyxl` as
its Excel engine.

**How to override:** Move to an optional extras group (`uv sync --extra excel`) if
binary size or CI time becomes a concern.

### A-04 — `pyarrow` is already available for Parquet

**Assumed:** `pyarrow` is a transitive dependency of `pantab` and does not need an
explicit addition.

**How to override:** Add `pyarrow` explicitly to `sidecar/pyproject.toml` if pantab
drops it as a transitive dep.

### A-05 — Dashboard layout uses hand-built XML

**Assumed:** The sidecar appends a `<dashboard>` element to the `.twb` XML produced by
`twb_builder.py`, following the same hand-built ElementTree pattern as ADR-002.

**How to override:** If a reliable Python Document API that creates dashboards becomes
available, prefer it.

### A-06 — Audience enum has exactly four values

**Assumed:** `exec | analyst | operational | mixed` at MVP.

**How to override:** Extend the zod enum and the constraint table in
`docs/feature-prompt-authoring/REQUIREMENTS.md` F-4.  Each new value needs documented
design effects and at least one unit test.

### A-07 — Dashboard layout defaults to `"tiled_vertical"`

**Assumed:** When `dashboardLayout` is omitted, a single-column vertical stack is used.

**How to override:** Change the default in the sidecar `DashboardWorkbookRequest` model
and the TS zod schema.

### A-08 — `TWB_VERSION` and `SOURCE_BUILD` are unchanged

**Assumed:** `TWB_VERSION = "18.1"` and `SOURCE_BUILD = "2024.1.0"` are reused.

**Why:** Proven to open on Tableau Cloud in the v0.1 live demo.

**How to override:** Bump only if a Tableau Cloud update rejects these values.

### A-09 — `create_datasource_from_table` is not modified; `csvPath` stays

**Assumed:** `create_datasource_from_file` is additive; `csvPath` and `records` on
`create_datasource_from_table` are not removed.

**How to override:** Deprecate `csvPath` in a future major version.

### A-10 — Field names in sheet specs are supplied by the agent, not introspected

**Assumed:** `design_dashboard` does not call the Tableau Metadata API to discover
available fields.  The agent knows field names from having just created the datasource
or from `@tableau/mcp-server`.

**How to override:** If field introspection is needed, it belongs in the official
`@tableau/mcp-server`; the agent passes results in.
