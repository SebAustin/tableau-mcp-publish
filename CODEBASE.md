# CODEBASE.md — tableau-mcp-publish

## Overview

`tableau-mcp-publish` is the **write side** of Tableau MCP. It exposes 14 MCP tools over stdio that let an AI agent turn a SQL query, CSV file, or inline records into a fully governed, published Tableau datasource and starter workbook on Tableau Cloud — in a single tool call. It is architecturally complementary to `tableau/tableau-mcp`, which covers reading, querying, Desktop-local workbook editing, and admin-gated content lifecycle.

The system has two layers:

1. **TypeScript MCP server** (`src/`) — handles MCP protocol, Tableau REST API authentication, project resolution, and publish (single-request or chunked). Spawns the Python sidecar on startup.
2. **Python FastAPI sidecar** (`sidecar/`) — does all binary file authoring: Hyper extract creation (via pantab), `.tdsx` packaging (hand-built TDS XML + zip), and `.twbx` workbook XML generation. Lives at `http://127.0.0.1:8899`, bound loopback-only, guarded by a per-spawn random token.

---

## Stack

| Layer | Technology | Version |
|---|---|---|
| TypeScript MCP server | Node.js | ≥ 22.7.5 (tested on 22, 24, 26) |
| Language | TypeScript | 5.7.2 |
| Module system | ESM (`"type": "module"`, `moduleResolution: NodeNext`) | — |
| MCP SDK | `@modelcontextprotocol/sdk` | 1.29.0 |
| HTTP client | `undici` | 7.28.0 |
| Schema validation | `zod` | 3.25.76 |
| Test runner | Vitest | 2.1.8 |
| Linter | ESLint 9 + `typescript-eslint` | 9.17.0 / 8.18.2 |
| Python sidecar | Python | 3.12.x (uv-pinned; 3.12/3.13 both tested in CI) |
| Web framework | FastAPI | 0.121.0 |
| ASGI server | uvicorn[standard] | 0.32.0 |
| Validation | Pydantic v2 | 2.9.2 |
| Hyper extract | tableauhyperapi | 0.0.21408 |
| DataFrame bridge | pantab | 5.2.0 |
| DataFrames | pandas | 2.2.3 |
| Python linter | ruff | 0.8.4 |
| Python type check | mypy | 1.13.0 (strict) |
| Python test | pytest | 8.3.4 |
| Package manager (TS) | npm | — |
| Package manager (Py) | uv | 0.11.18+ |

Optional Python extras (`uv sync --extra connectors`): `snowflake-connector-python`, `psycopg[binary]`, `sqlalchemy` — not required for CI or the authoring path.

---

## Build / Run / Test

```bash
# TypeScript
npm install
npm run build         # tsc -> dist/
npm run lint          # eslint .
npm run typecheck     # tsc --noEmit
npm test              # vitest run (87 tests)

# Python sidecar
cd sidecar
uv sync --all-extras  # create .venv with dev + connectors
uv run ruff check .   # lint
uv run mypy --strict . # type check
uv run pytest -q      # 114 tests

# Full CI gate (equivalent to GitHub Actions)
make ci               # build + lint + test + sidecar-lint + sidecar-typecheck + sidecar-test
```

The `make ci` target does **not** run `npm run typecheck` separately; the `build` step already runs `tsc` (which is a full type + emit check). The `lint` step does not run `eslint` with `--max-warnings 0`; it exits non-zero only on errors.

---

## Map

```
tableau-mcp-publish/
├── src/
│   ├── index.ts              # Entry point — registers all 14 tools, signs in, spawns sidecar, starts stdio transport
│   ├── config.ts             # Zod schema for env-based config (SERVER, SITE_NAME, PAT_NAME, PAT_VALUE…)
│   ├── restClient.ts         # TableauRestClient: signIn/signOut, publish (single + chunked), CRUD, permissions
│   ├── sidecar.ts            # AuthoringSidecar: spawns uv/uvicorn, health-polls, posts to /datasource/*, /workbook/*
│   ├── planner/              # Deterministic BI planner (no LLM calls)
│   │   ├── schema.ts         # DashboardPlan Zod types
│   │   ├── fields.ts         # Field-role inference from column hints
│   │   ├── marks.ts          # Mark-type selection logic
│   │   ├── audience.ts       # Audience → canvas size + sheet-count clamps
│   │   ├── questions.ts      # Interview-mode clarifying-question generation
│   │   └── plan.ts           # planDashboard() entry point
│   └── tools/
│       ├── context.ts        # ToolContext interface + toolResult() helper
│       ├── projects.ts       # list_projects, create_project
│       ├── content.ts        # list_content, refresh_datasource, delete_content
│       ├── permissions.ts    # set_permissions (allowlist + elevated gate)
│       ├── createDatasourceFromQuery.ts  # create_datasource_from_query
│       ├── createDatasourceFromTable.ts  # create_datasource_from_table
│       ├── createDatasourceFromFile.ts   # create_datasource_from_file (csv/json/jsonl/xlsx/parquet)
│       ├── createStarterWorkbook.ts      # create_starter_workbook
│       ├── designDashboard.ts            # design_dashboard (autonomous/interview/interview_followup/directed)
│       ├── buildFromPlan.ts              # build_from_plan (DashboardPlan → embedded .twbx → publish)
│       ├── publishDatasource.ts          # publish_datasource (pre-built file)
│       └── publishWorkbook.ts            # publish_workbook (pre-built file)
├── tests/
│   ├── restClient.test.ts      # Unit: chunk math, strategy boundary, signIn, publish, resolveProjectId (11 tests)
│   ├── secrets.test.ts         # PAT-never-logged assertions (3 tests)
│   ├── sidecar.test.ts         # AuthoringSidecar: startup, health, build calls (7 tests)
│   ├── sidecar-columns.test.ts # Column-schema pass-through from /datasource/from-file (3 tests)
│   ├── planner.test.ts         # planDashboard(): autonomous/interview/directed × audiences (29 tests)
│   └── tools.test.ts           # Integration: all 14 tools via FakeServer + mock ctx (34 tests)
├── sidecar/
│   ├── server.py             # FastAPI app — token guard, /health, /datasource/from-query,
│   │                         #   /datasource/from-table, /datasource/from-file, /workbook/starter,
│   │                         #   /workbook/dashboard
│   ├── hyper_builder.py      # DataFrame/SQL/CSV/JSON/XLSX/Parquet → .hyper extract (pantab + tableauhyperapi)
│   ├── tds_builder.py        # .hyper → .tdsx (hand-built TDS XML + zip)
│   ├── twb_builder.py        # build_twb_xml() / build_starter_twbx() / build_embedded_twb_xml() /
│   │                         #   build_embedded_twbx() — worksheets + optional <dashboards> block
│   ├── pyproject.toml        # uv project config, ruff/mypy/pytest settings
│   └── tests/
│       ├── test_hyper_builder.py        # 12 tests: round-trip, column roles, CSV, max_rows, records_to_df
│       ├── test_hyper_builder_formats.py# 16 tests: csv/json/jsonl/xlsx/parquet format round-trips, byte cap
│       ├── test_tds_builder.py          # 3 tests: zip structure, dbname path, column roles
│       ├── test_twb_builder.py          # 13 tests: datasource ref, worksheets, mark classes, A2 structural pins
│       ├── test_twb_dashboard.py        # 21 tests: zone count/names, geometry invariants, canvas size, regression
│       ├── test_twb_embedded.py         # 16 tests: federated datasource, field refs, zip structure, XSD gate
│       ├── test_twb_schema_validation.py# 4 tests: XSD-valid output + XXE-safe parser guard
│       ├── test_server.py               # 5 tests: health, from-table, 400 guard, workbook starter, token guard
│       └── test_server_new_routes.py    # 24 tests: /datasource/from-file (5 formats), /workbook/dashboard
├── Makefile                  # CI gate: build lint test sidecar-lint sidecar-typecheck sidecar-test
├── package.json
├── tsconfig.json             # strict, NodeNext, rootDir=src, outDir=dist
├── eslint.config.js          # ignores: dist/, node_modules/, sidecar/, coverage/  (NOT .cursor/)
└── vitest.config.ts          # tests/**/*.test.ts, extensionAlias .js->.ts
```

---

## Architecture & Data Flow

```
AI agent (Claude / Cursor / etc.)
        │  MCP stdio (JSON-RPC)
        ▼
  src/index.ts  ──────────── McpServer (MCP SDK)
        │                          │
        │ registers 14 tools       │
        ▼                          │
  ToolContext { config, rest, sidecar }
        │                          │
  ┌─────────────┐       ┌──────────────────────┐
  │ TableauRest │       │  AuthoringSidecar     │
  │ Client      │       │  uv run uvicorn       │
  │ (undici)    │       │  server:app           │
  │             │       │  :8899 loopback only  │
  │ Tableau     │       │                       │
  │ REST API    │       │  FastAPI routes:      │
  │ v3.28       │       │  /health              │
  │             │       │  /datasource/from-query│
  └─────────────┘       │  /datasource/from-table│
                        │  /datasource/from-file│
                        │  /workbook/starter    │
                        │  /workbook/dashboard  │
                        │                       │
                        │  hyper_builder.py     │
                        │   pandas + pantab     │
                        │   -> .hyper           │
                        │                       │
                        │  tds_builder.py       │
                        │   XML + zip           │
                        │   -> .tdsx            │
                        │                       │
                        │  twb_builder.py       │
                        │   XML + zip           │
                        │   -> .twbx            │
                        └──────────────────────┘
```

**Trust boundaries:**
- The sidecar is spawned with `stdio: ['ignore','ignore','pipe']` — its stdout never reaches the MCP channel.
- The sidecar binds `127.0.0.1` only. A random 24-byte hex token is generated per spawn, injected as `SIDECAR_TOKEN` env, and required as `X-Sidecar-Token` on every request (constant-time `hmac.compare_digest`).
- The Tableau PAT secret is sent only in the sign-in body, never logged (asserted in `tests/secrets.test.ts`). Config validation errors print the offending field path, never the value.
- `resolveProjectId` hard-refuses the `"Default"` project by name and empty names, preventing silent publishes to ungoverned space.

**Publish strategy:** `selectPublishStrategy()` in `src/restClient.ts` — files `< 64 MiB` use a single `multipart/mixed` POST; files `>= 64 MiB` (incl. exactly 64 MiB) use the `fileUploads` chunked session (TSC-aligned). Mid-stream abort does not issue a finalize POST.

---

## Alignment with official Tableau tooling

This section records deliberate divergences from official Tableau libraries and the rationale for
two non-adoptions, so they are not re-evaluated on every review.

### Divergences from `tableau/server-client-python` (TSC)

TSC is the canonical Python REST client for Tableau Server/Cloud. Our publish path aligns with its
semantics. The chunking boundary was aligned to TSC's `>=` in A3 (exact 64 MiB now takes the chunked
path); two deliberate differences remain:

| Aspect | TSC | `tableau-mcp-publish` | Rationale |
|---|---|---|---|
| Chunking boundary | `file_size >= 64 MB` (exact 64 MB → chunked) | `file_size >= 64 MB` — **aligned to TSC (A3)** | Was previously `>` (exact 64 MB single); a single multipart request at exactly 64 MB exceeds the cap once boundary overhead is added, so `>=` is correct. |
| Chunk size | 50 MB per chunk | 64 MB per chunk | Larger chunks reduce round-trips; acceptable on Cloud. |
| REST API version | Auto-negotiated (latest supported by the server) | Pinned to 3.28 | Predictability over auto-negotiation; update explicitly when new endpoints are needed. |
| Abort / unfinalized session | Aborts unfinalized upload sessions | Does not issue a `finalize` POST on mid-stream abort | Behaviour matches TSC: an unfinalized session is automatically discarded by the server. |

### Official TWB XSD (`tableau/tableau-document-schemas`)

`tableau/tableau-document-schemas` publishes `schemas/2026_1/twb_2026.1.0.xsd` — a W3C XSD that
describes the `.twb` XML format, maintained by the official Tableau team as a machine-validatable
fidelity gate. The sidecar test suite vendors this schema and validates `build_twb_xml()` output
against it via `lxml`. This catches the class of "parses but won't render" defects that are
invisible to structural assertions about expected elements.

### Non-adoption: `tableau/document-api-python`

`document-api-python` is a library for modifying **existing** `.twb`/`.tds` files. Its own README
states it "doesn't support creating files from scratch"; `Workbook.__init__` only opens existing
files; `_prepare_dashboards()` returns names only (no zone writer); worksheets are name stubs
(`# TODO: A real worksheet object`). It cannot author the XML we need to emit. We hand-roll the
TWB XML in `sidecar/twb_builder.py`. No re-evaluation is needed unless the library gains
create-from-scratch capability.

### Non-adoption: `tableau/tableau-ui`

`@tableau/tableau-ui` is a React-16 browser component library for Tableau-look-and-feel UI.
`tableau-mcp-publish` is a headless stdio MCP server with no DOM or React attachment point. The
library is relevant only in the single future scenario where a separate **web admin console** is
built (e.g. a job/permission GUI) — and even then it carries a React-16-only constraint. Deferred
as FUTURE-ONLY.

---

## Conventions

**TypeScript:**
- ESM-only; `.js` import extensions pointing at `.ts` source (NodeNext resolution).
- All tools follow the same pattern: a single `registerXxx(server, ctx)` function in `src/tools/`, calling `server.registerTool(name, { title, description, inputSchema, outputSchema }, handlerFn)`. Input and output schemas are Zod objects. The handler calls `ctx.sidecar.*` and/or `ctx.rest.*`, then returns `toolResult(text, structuredContent)`.
- `process.stderr.write(...)` is used for structured logging — `console.*` is never used (stdout is the MCP channel).
- Errors thrown from handlers propagate as MCP error responses.

**Python:**
- All modules use `from __future__ import annotations`.
- Pydantic v2 models with `model_config = ConfigDict(populate_by_name=True)` and camelCase aliases for the JSON API boundary.
- Ruff line-length 100, target py312, rules E/F/I/UP/B/SIM.
- mypy `--strict`, excludes `tests/`.
- Output files go to `tempfile.gettempdir()/tableau-mcp-publish/<uuid4>.<ext>`.

**Git/commit conventions:** Conventional commits (`feat`, `fix`, `docs`, `ci`, `chore`, `perf`). Scope tags used, e.g. `feat(demo)`, `fix(pkg)`, `docs:`. Co-authored attribution in commit footers.

---

## Tests

### TypeScript (Vitest) — 87 tests

| File | Count | What it tests |
|---|---|---|
| `tests/restClient.test.ts` | 11 | `splitIntoChunks` math, `selectPublishStrategy` 64 MB boundary, `signIn` parsing, single publish, chunked publish (3 chunks, 3 PUTs + 1 finalize), mid-stream abort (no finalize), `resolveProjectId` rejects empty/Default/resolves known |
| `tests/secrets.test.ts` | 3 | PAT not in sign-in output, PAT not in redacted API error, config error does not echo PAT |
| `tests/sidecar.test.ts` | 7 | AuthoringSidecar: startup health-poll, buildDatasource, buildStarterWorkbook, buildDashboardWorkbook call wiring |
| `tests/sidecar-columns.test.ts` | 3 | Column schema pass-through from `/datasource/from-file` response |
| `tests/planner.test.ts` | 29 | `planDashboard()` autonomous/interview/interview_followup/directed × exec/analyst/operational audiences; field inference; mark-type constraints; question count |
| `tests/tools.test.ts` | 34 | All 14 tools registered with description+schemas; full wiring for `create_datasource_from_query`, `create_starter_workbook`, `design_dashboard`, `build_from_plan`; guardrails: delete confirm, delete refuses Default project, `set_permissions` elevated gate, allowlist rejection, valid caps; `create_datasource_from_table` input validation; `create_datasource_from_file` unsupported extension (0 sidecar calls) |

### Python (pytest) — 114 tests

| File | Count | What it tests |
|---|---|---|
| `sidecar/tests/test_hyper_builder.py` | 12 | Hyper round-trip (row count + types), column roles, CSV source, max_rows cap, records_to_dataframe, read_hyper_columns |
| `sidecar/tests/test_hyper_builder_formats.py` | 16 | csv/json/jsonl/xlsx/parquet format round-trips; byte cap; `file_to_dataframe` unsupported type error; Excel sheet by index/name |
| `sidecar/tests/test_tds_builder.py` | 3 | `.tdsx` zip structure (`.tds` + `Data/*.hyper`), dbname path matches, column roles |
| `sidecar/tests/test_twb_builder.py` | 13 | Published datasource reference (sqlproxy/repository-location), one worksheet per sheet spec, mark class per type, default site path, starter `.twbx` is a valid zip; A2 structural pins (simple-id, cards, viewpoint, aggregation, style, explain-data) |
| `sidecar/tests/test_twb_dashboard.py` | 21 | Zone count/names match sheets; worksheet zones have `name` and no `type`; tiling geometry (Σ==100000, no overlap, distinct offsets); canvas size element; default-None regression (byte-identical + no `<dashboards>`) |
| `sidecar/tests/test_twb_embedded.py` | 16 | Federated datasource (not sqlproxy); hyper named-connection; field references use `[federated.*]`; zip contains `Data/*.hyper`; XSD gate (with and without dashboard); FileNotFoundError on missing extract |
| `sidecar/tests/test_twb_schema_validation.py` | 4 | Official TWB XSD gates sqlproxy + embedded output; XXE-safe parser guard; malformed-XML rejection |
| `sidecar/tests/test_server.py` | 5 | Health endpoint, from-table (records) returns `.tdsx`, from-table requires input (400), workbook starter returns `.twbx`, token guard blocks/passes |
| `sidecar/tests/test_server_new_routes.py` | 24 | `/datasource/from-file` for all 5 formats returns valid `.tdsx`; `/workbook/dashboard` returns valid `.twbx` with embedded extract |

Run commands: `npm test` (TS) and `cd sidecar && uv run pytest -q` (Python).

---

## Dependencies & Risk

**Production TypeScript deps (3):**
- `@modelcontextprotocol/sdk@1.29.0` — Anthropic's official MCP server SDK.
- `undici@7.28.0` — Node.js HTTP client; bumped to 7.28.0 in the most recent security fix cycle; `npm audit --omit=dev` reports 0 vulnerabilities.
- `zod@3.25.76` — schema validation.

**Python deps of note:**
- `tableauhyperapi@0.0.21408` — Tableau-proprietary Hyper engine; binary wheel; no Python 3.13 wheel yet (uv uses Python 3.12 inside the venv).
- `pantab@5.2.0` — thin pandas/Arrow bridge over tableauhyperapi.
- Database connectors are optional extras, not in the default install.

**License:** MIT (repo). Dependencies are MIT/BSD/Apache except `tableauhyperapi` (Tableau proprietary).

---

## Tech Debt / Issues

1. **Lint gate broken by `.cursor/` directory.** `eslint.config.js:7` ignores `dist/`, `node_modules/`, `sidecar/`, `coverage/` — but not `.cursor/`. The `.cursor/` directory was added to the working tree after the last green CI run. ESLint now reports ~200 errors on those CJS hook scripts, so `make ci` (`npm run lint`) exits non-zero **locally**. The upstream GitHub CI never saw `.cursor/` (it is `.gitignore`d), so CI remains green. The fix is one line: add `".cursor/**"` to the `ignores` array. This must be done before the next feature branch runs `make ci` locally.

2. **No `typecheck` step in `make ci`.** The Makefile runs `build` (which emits JS and catches type errors), but a standalone `typecheck` (`tsc --noEmit`) step is absent from the `ci` target. In practice, `tsc` errors block `build`, so this is not a real gap, but a dedicated `typecheck` step would catch import-only type errors without producing artifacts.

3. ~~**`twb_builder.py` emits worksheets only — no dashboard block.**~~ **Shipped.** `build_twb_xml()` / `build_embedded_twb_xml()` now emit an optional `<dashboards>` + `<viewpoints>` block; `build_from_plan` drives the embedded-extract path end-to-end.

4. ~~**File-format support is CSV-only.**~~ **Shipped.** `create_datasource_from_file` (`/datasource/from-file`) accepts csv, json, jsonl, xlsx, and parquet via `hyper_builder.file_to_dataframe()`.

5. **`getDatasource` has a fallback list-all-datasources** when `contentUrl` is missing from the GET response (`src/restClient.ts:401–416`). This is correct but can be slow on large sites and is a fragility point if `contentUrl` is reliably missing.

6. **Structured logging is `process.stderr.write` concatenation** — no log levels, no JSON format, no correlation IDs. Adequate for an MCP stdio server today but would need a real logger (e.g., `structlog` on the Python side is already in the deps but unused).

---

---

## Regression-Guard Tests That Must Keep Passing

All 201 tests must stay green on `make ci`:

**TypeScript (87 tests — `npm test`):**
- `tests/restClient.test.ts`: `splitIntoChunks` math, `selectPublishStrategy` 64 MB boundary, `signIn` parsing, single-request publish, 3-chunk upload (3 PUTs + 1 finalize POST), mid-stream abort (no finalize), `resolveProjectId` rejects empty/Default/resolves known.
- `tests/secrets.test.ts`: PAT not in sign-in output, PAT not in redacted API error, config error does not echo PAT.
- `tests/sidecar.test.ts`: AuthoringSidecar startup, health-poll, build* call wiring.
- `tests/sidecar-columns.test.ts`: column schema pass-through from `/datasource/from-file`.
- `tests/planner.test.ts`: `planDashboard()` autonomous/interview/directed × all audiences; field inference; mark constraints; question count.
- `tests/tools.test.ts`: 14-tool registration count, all tool names present, `create_datasource_from_query` full wiring, `create_starter_workbook` wiring, `design_dashboard` wiring, `build_from_plan` wiring; all guardrails (delete confirm, delete Default refusal, elevated-capability gate, allowlist rejection), `create_datasource_from_table` input validation, `create_datasource_from_file` unsupported extension.

**Python (114 tests — `cd sidecar && uv run pytest -q`):**
- `sidecar/tests/test_hyper_builder.py`: hyper round-trip row count + all column types, column role assignment, CSV source read, max_rows cap, records_to_dataframe, read_hyper_columns.
- `sidecar/tests/test_hyper_builder_formats.py`: csv/json/jsonl/xlsx/parquet round-trips; byte cap; unsupported type error; Excel sheet by index/name.
- `sidecar/tests/test_tds_builder.py`: `.tdsx` zip has exactly one `.tds` and one `Data/*.hyper`, dbname path format, column role attributes.
- `sidecar/tests/test_twb_builder.py`: sqlproxy datasource reference present, repository-location attributes, one worksheet per sheet spec with correct datasource-dependencies, mark class mapping (bar/line/text), default site path, `.twbx` is a valid zip containing a parseable `<workbook>`; A2 structural pins (simple-id, cards with shelf content, viewpoint, aggregation, style, explain-data).
- `sidecar/tests/test_twb_dashboard.py`: zone count/names match sheets; worksheet zones have `name` and no `type`/`type-v2`; tiling geometry invariants (Σ==100000, no overlap, distinct offsets); canvas size element matches inputs; default-None regression (byte-identical, no `<dashboards>`).
- `sidecar/tests/test_twb_embedded.py`: federated datasource (not sqlproxy); hyper named-connection; `[federated.*]` field references in rows/cols; zip contains `Data/*.hyper`; XSD gate (with and without dashboard); FileNotFoundError on missing extract.
- `sidecar/tests/test_twb_schema_validation.py`: official TWB XSD gates sqlproxy and embedded output; XXE-safe parser config; malformed-XML rejected by schema.
- `sidecar/tests/test_server.py`: health returns `{"status":"ok"}`, from-table (records) produces a valid `.tdsx` zip, from-table without input returns 400, workbook starter produces `.twbx`, token guard blocks requests without header and passes with matching header.
- `sidecar/tests/test_server_new_routes.py`: `/datasource/from-file` for all 5 formats returns valid `.tdsx`; `/workbook/dashboard` returns valid `.twbx` with embedded extract.
