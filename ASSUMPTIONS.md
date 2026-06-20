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
