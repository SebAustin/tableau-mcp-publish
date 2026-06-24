# PLAN.md — tableau-mcp-publish

---

## Feature: Prompt-Driven Authoring — Requirements Brief

### Problem & goal

Today an agent must supply fully-formed worksheet specs to build a workbook.  This
feature lifts the abstraction so a user can describe a business question, specify a data
source (file path or SQL query), choose an audience, and optionally be guided by
structured BI-analyst questions — and receive a published Tableau **dashboard** (not
just loose worksheets) on Cloud.

### Functional requirements (summary)

- **Datasource from file (F-1):** accept `.csv`, `.json`/`.jsonl`, `.xlsx`/`.xls`,
  `.parquet` via a new `create_datasource_from_file` tool; SQL path unchanged.
- **Dashboard output (F-2):** new `create_dashboard_workbook` / sidecar endpoint that
  emits a `.twb` with a `<dashboard>` + `<zones>` element; tiled vertical or horizontal.
- **Three authoring modes via `design_dashboard` (F-3):**
  - Autonomous: business question + audience → `DashboardPlan` in one call.
  - Interview: returns 3–7 clarifying questions; second call with answers → plan.
  - Directed: explicit visualization directions → plan.
- **Audience enum (F-4):** `exec | analyst | operational | mixed` with documented
  constraints on sheet count, mark types, and layout.
- **`build_from_plan` (F-5):** consumes a `DashboardPlan`, creates datasource if
  needed, builds and publishes the dashboard workbook.

### Non-goals

Read/query tools; live-connection datasources; pixel-perfect render validation; LLM
inference inside the server; multi-turn conversation state inside the server; image/PDF
export; map marks beyond experimental; metadata introspection; auto-create projects.

### Success criteria (summary — full detail in `docs/feature-prompt-authoring/REQUIREMENTS.md`)

| Code | What | Env |
|---|---|---|
| PA-1..PA-3 | Multi-format file ingest: parquet/xlsx/json/jsonl round-trip + excel sheet selection | headless |
| DB-1..DB-3 | Dashboard XML structure: `<dashboard>` element, zone count, layout direction offsets | headless |
| MA-1..MA-3 | Autonomous mode: audience constraints enforced on plan output | headless |
| MB-1..MB-2 | Interview mode: question count 3–7; followup resolves to a plan | headless |
| MC-1..MC-2 | Directed mode: directions mapped to sheets; missing directions rejected | headless |
| E2E-1..E2E-2 | build_from_plan orchestration: correct sidecar + REST call sequence | headless |
| E2E-3 | Live demo: published workbook has a dashboard tab on Cloud | gated |
| CI-1..CI-2 | CI matrix green; build + lint + mypy --strict clean | headless |

Full requirements, tool contracts, audience enum effects, and success-criteria
verification methods: `docs/feature-prompt-authoring/REQUIREMENTS.md`.

---

## Problem & goal (v0.1 — baseline)

The official `@tableau/mcp-server` is read-only (VizQL Data Service, Metadata API, Pulse). There
is no MCP tooling to **author and publish** Tableau content. `tableau-mcp-publish` is the **write
companion**: an agent can turn SQL or a DataFrame into a governed published datasource, generate a
starter workbook, and publish both to Tableau Cloud. It uses the **same env-var auth** as the
official server so both drop into one MCP client config.

## Success criteria (binary, testable)

1. `npm run build` compiles clean; `npm run lint` and `npm test` pass with 0 failures.
2. `cd sidecar && uv run ruff check . && uv run mypy --strict . && uv run pytest -q` pass.
3. REST client implements PAT signin and **both** publish paths. Tests assert: (a) a file
   `< 64 MiB` takes the single-request path; (b) a file `>= 64 MiB` (incl. exactly 64 MiB)
   takes the chunked path — aligned to official TSC, which is the authority for the boundary
   (`server-client-python` chunks when `file_size >= FILESIZE_LIMIT_MB * BYTES_PER_MB`);
   (c) a file `> 64 MiB` splits into `ceil(size/chunk)` chunks; (d) a simulated mid-stream chunk
   failure **aborts** the session and surfaces an error (no partial publish).
4. `.hyper` validity = re-open via the Hyper API and assert row count + per-column Hyper types
   match the source. `.tdsx` validity = the zip contains a top-level `.tds` **and** `Data/*.hyper`,
   the `.tds` has `connection class='hyper'`, a `relation`, and one `<column>` per source column.
   `.twb` validity = parseable XML whose datasource is a `class='sqlproxy'` reference to the named
   published datasource, with one `<worksheet>` per sheet (correct `<mark class=…>`) and a
   `<datasource-dependencies>` block whose column names match the requested fields exactly. These
   are asserted against the builders' own output (the field list is an input, so dependency names
   are checkable without a live site).
5. All 11 tools registered. A test asserts each tool's description is non-empty and each declares
   its params/returns. Every **publish** tool requires an explicit project (rejects empty/Default),
   defaults `overwrite=false`, returns `{ luid, url }`. **Destructive/stateful guardrails:**
   `delete_content` requires `contentType` + `luid` + `confirm=true` and refuses Default-project
   targets; `set_permissions` validates grantee + capability against an allowlist; no tool logs the
   PAT (asserted by a log-capture test).
6. CI green on both jobs across the version matrix (Node + Python, see below) on GitHub Actions.
7. One authorized live publish to a Dev site: the datasource opens in the site and the starter
   workbook **renders at least one mark** — captured as a screenshot / manual checklist artifact in
   `ACCEPTANCE.md` (a returned URL alone does not count).

## Architecture

Two layers, localhost-coupled:

- **TypeScript MCP server (`src/`)** — stdio MCP server (`@modelcontextprotocol/sdk`,
  `McpServer.registerTool`). `restClient.ts` (undici) owns auth + REST (projects, content,
  permissions, publish incl. chunked). `sidecar.ts` spawns and calls the Python sidecar over
  `127.0.0.1`.
- **Python sidecar (`sidecar/`)** — FastAPI on localhost only. `hyper_builder` (pantab + Hyper
  API, query→DataFrame), `tds_builder` (hand-built `.tdsx` zip), `twb_builder` (ElementTree
  `.twb`/`.twbx`). Never touches the network except localhost.

Boundary: REST + auth in TS; all file authoring (`.hyper`/`.tdsx`/`.twbx`) in Python.

## Tools (11)

`create_datasource_from_query`, `create_datasource_from_table`, `create_starter_workbook`,
`publish_datasource`, `publish_workbook`, `list_projects`, `create_project`, `set_permissions`,
`list_content`, `refresh_datasource`, `delete_content`.

## Build sequence

1. Scaffold (config, deps pinned to verified-real versions, CI skeleton).
2. REST client + project/content/permission tools + tests.
3. Python sidecar (hyper/tds/twb builders + FastAPI) + pytest + mypy.
4. Authoring tools (`create_datasource_from_query`, `create_datasource_from_table`,
   `create_starter_workbook`, `publish_datasource`, `publish_workbook`) + the `twb_builder` +
   full MCP wiring in `index.ts` + tools tests.
5. CI (version matrix) + docs + gated demo.

## Risk mitigations & hardening (from plan review)

- **Chunked publish lifecycle:** `initiate → append(N) → finalize(uploadSessionId)`. On any append
  failure, abort and raise — never finalize a partial upload. Boundary aligns to official TSC
  `>=`: a file `>= 64 MiB` (incl. exactly 64 MiB) takes the chunked path. Both the boundary and
  a mid-stream failure are unit-tested against mocked undici.
- **Sidecar trust & lifecycle:** the sidecar binds `127.0.0.1` on a configurable port; the TS layer
  generates a **per-spawn random token**, passes it to the child via env, and sends it as an
  `X-Sidecar-Token` header on every call. The sidecar **rejects** requests without the matching
  token (defends against other local processes). Startup uses a `/health` readiness probe with a
  30 s timeout; spawn/exit failures throw with the captured stderr; the child is killed on shutdown.
  The Python env is resolved via `uv run --directory sidecar`.
- **`.tdsx`/`.twb` structural fidelity:** beyond well-formedness, builder output is asserted against
  the structural contract in criterion 4 (connection class, relation, per-column metadata,
  sqlproxy reference, mark class, dependency column names). The flagship demo additionally proves
  real render once against a live site.
- **CI version matrix:** TypeScript job runs Node `22.x`, `24.x`, **and** `26.x`; Python job
  runs `3.12` **and** `3.13`. This covers the Node 22 line the spec intended (the exact `22.7.5`
  patch isn't in the setup-node manifest), the current line, and the local dev runtime
  (Node 26 / Python 3.13.9), closing the "green locally, red in CI" gap on both legs.

## Verification

- Headless/real: Hyper round-trip, `.tdsx`/`.twb` validity, chunk-split math, tool orchestration
  (mocked REST+sidecar), lint, `mypy --strict`, build.
- Mocked: all REST publish in unit tests (no PAT in CI).
- Gated live (authorized): `scripts/demo.ts` against a Dev site → two Cloud URLs.

## Guardrails

PAT-only auth; never log secrets; `.env` gitignored; explicit project required (never Default);
`overwrite` defaults false; feature-branch workflow; private GitHub repo first.

## Out of scope (non-goals)

Read/query tools (use the official server); live-connection datasources (extract-only for v0.1 —
tracked as a follow-up issue); map worksheets render best-effort/experimental; full Tableau-render
validation (only achievable against a live site, done once at the gated demo).
