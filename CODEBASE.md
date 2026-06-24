# CODEBASE.md — tableau-mcp-publish

## Overview

`tableau-mcp-publish` is the **write side** of Tableau MCP. It exposes 11 MCP tools over stdio that let an AI agent turn a SQL query, CSV file, or inline records into a fully governed, published Tableau datasource and starter workbook on Tableau Cloud — in a single tool call. It is architecturally complementary to Salesforce's read-only `@tableau/mcp-server`.

The system has two layers:

1. **TypeScript MCP server** (`src/`) — handles MCP protocol, Tableau REST API authentication, project resolution, and publish (single-request or chunked). Spawns the Python sidecar on startup.
2. **Python FastAPI sidecar** (`sidecar/`) — does all binary file authoring: Hyper extract creation (via pantab), `.tdsx` packaging (hand-built TDS XML + zip), and `.twbx` workbook XML generation. Lives at `http://127.0.0.1:8899`, bound loopback-only, guarded by a per-spawn random token.

---

## Stack

| Layer | Technology | Version |
|---|---|---|
| TypeScript MCP server | Node.js | ≥20 (tested on 22, 24, 26) |
| Language | TypeScript | 5.7.2 |
| Module system | ESM (`"type": "module"`, `moduleResolution: NodeNext`) | — |
| MCP SDK | `@modelcontextprotocol/sdk` | 1.29.0 |
| HTTP client | `undici` | 7.28.0 |
| Schema validation | `zod` | 3.25.76 |
| Test runner | Vitest | 2.1.8 |
| Linter | ESLint 9 + `typescript-eslint` | 9.17.0 / 8.18.2 |
| Python sidecar | Python | 3.12.x (uv-pinned; 3.12/3.13 both tested in CI) |
| Web framework | FastAPI | 0.115.6 |
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
npm test              # vitest run (28 tests)

# Python sidecar
cd sidecar
uv sync --all-extras  # create .venv with dev + connectors
uv run ruff check .   # lint
uv run mypy --strict . # type check
uv run pytest -q      # 18 tests

# Full CI gate (equivalent to GitHub Actions)
make ci               # build + lint + test + sidecar-lint + sidecar-typecheck + sidecar-test
```

The `make ci` target does **not** run `npm run typecheck` separately; the `build` step already runs `tsc` (which is a full type + emit check). The `lint` step does not run `eslint` with `--max-warnings 0`; it exits non-zero only on errors.

---

## Map

```
tableau-mcp-publish/
├── src/
│   ├── index.ts              # Entry point — registers all tools, signs in, spawns sidecar, starts stdio transport
│   ├── config.ts             # Zod schema for env-based config (SERVER, SITE_NAME, PAT_NAME, PAT_VALUE…)
│   ├── restClient.ts         # TableauRestClient: signIn/signOut, publish (single + chunked), CRUD, permissions
│   ├── sidecar.ts            # AuthoringSidecar: spawns uv/uvicorn, health-polls, posts to /datasource/*, /workbook/*
│   └── tools/
│       ├── context.ts        # ToolContext interface + toolResult() helper
│       ├── projects.ts       # list_projects, create_project
│       ├── content.ts        # list_content, refresh_datasource, delete_content
│       ├── permissions.ts    # set_permissions (allowlist + elevated gate)
│       ├── createDatasourceFromQuery.ts  # create_datasource_from_query
│       ├── createDatasourceFromTable.ts  # create_datasource_from_table
│       ├── createStarterWorkbook.ts      # create_starter_workbook
│       ├── publishDatasource.ts          # publish_datasource (pre-built file)
│       └── publishWorkbook.ts            # publish_workbook (pre-built file)
├── tests/
│   ├── restClient.test.ts    # Unit: chunk math, strategy boundary, signIn, publish, resolveProjectId
│   ├── secrets.test.ts       # PAT-never-logged assertions
│   └── tools.test.ts         # Integration: all 11 tools via FakeServer + mock ctx
├── sidecar/
│   ├── server.py             # FastAPI app — token guard, /health, /datasource/from-query, /datasource/from-table, /workbook/starter
│   ├── hyper_builder.py      # DataFrame/SQL/CSV -> .hyper extract (pantab + tableauhyperapi)
│   ├── tds_builder.py        # .hyper -> .tdsx (hand-built TDS XML + zip)
│   ├── twb_builder.py        # build_twb_xml() / build_starter_twbx() — worksheets only, no dashboard block
│   ├── pyproject.toml        # uv project config, ruff/mypy/pytest settings
│   └── tests/
│       ├── test_hyper_builder.py  # 5 tests: round-trip, column roles, CSV, max_rows
│       ├── test_tds_builder.py    # 3 tests: zip structure, dbname path, column roles
│       ├── test_twb_builder.py    # 5 tests: datasource reference, worksheets, mark classes, default site, zip
│       └── test_server.py         # 5 tests: health, from-table, 400 guard, workbook starter, token guard
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
        │ registers 11 tools       │
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
                        │  /workbook/starter    │
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

**Publish strategy:** `selectPublishStrategy()` at `src/restClient.ts:50` — files ≤64 MB use a single `multipart/mixed` POST; files >64 MB use `fileUploads` chunked session. Mid-stream abort does not issue a finalize POST.

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

### TypeScript (Vitest) — 28 tests

| File | Count | What it tests |
|---|---|---|
| `tests/restClient.test.ts` | 11 | `splitIntoChunks` math, `selectPublishStrategy` 64 MB boundary, `signIn` parsing, single publish, chunked publish (3 chunks, 3 PUTs + 1 finalize), mid-stream abort (no finalize), `resolveProjectId` rejects empty/Default/resolves known |
| `tests/secrets.test.ts` | 3 | PAT not in sign-in output, PAT not in redacted API error, config error does not echo PAT |
| `tests/tools.test.ts` | 14 | All 11 tools registered with description+schemas; `create_datasource_from_query` wiring; `create_starter_workbook` wiring; guardrails: delete needs confirm, delete refuses Default project, `set_permissions` elevated gate, allowlist rejection, valid caps; `create_datasource_from_table` requires csvPath/records |

### Python (pytest) — 18 tests

| File | Count | What it tests |
|---|---|---|
| `sidecar/tests/test_hyper_builder.py` | 5 | Hyper round-trip (row count + types), column roles, CSV source, max_rows cap, records_to_dataframe |
| `sidecar/tests/test_tds_builder.py` | 3 | `.tdsx` zip structure (`.tds` + `Data/*.hyper`), dbname path matches, column roles |
| `sidecar/tests/test_twb_builder.py` | 5 | Published datasource reference (sqlproxy/repository-location), one worksheet per sheet spec, mark class per type, default site path, starter `.twbx` is a valid zip |
| `sidecar/tests/test_server.py` | 5 | Health endpoint, from-table (records) returns `.tdsx`, from-table requires input (400), workbook starter returns `.twbx`, token guard blocks/passes |

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

3. **`twb_builder.py` emits worksheets only — no dashboard block.** The current `build_twb_xml()` produces `<workbook><datasources>…</datasources><worksheets>…</worksheets><windows>…</windows></workbook>`. There is no `<dashboards>` element. Tableau opens the workbook in the first worksheet view. A prompt-driven dashboard feature requires extending `twb_builder.py`.

4. **File-format support is CSV-only** for the `from-table` / `from-query` paths. Parquet, JSON, and other formats would require new branches in `hyper_builder.query_to_dataframe()`.

5. **`getDatasource` has a fallback list-all-datasources** when `contentUrl` is missing from the GET response (`src/restClient.ts:401–416`). This is correct but can be slow on large sites and is a fragility point if `contentUrl` is reliably missing.

6. **Structured logging is `process.stderr.write` concatenation** — no log levels, no JSON format, no correlation IDs. Adequate for an MCP stdio server today but would need a real logger (e.g., `structlog` on the Python side is already in the deps but unused).

---

## Extension Points for Prompt-Driven Authoring

### 1. Datasource from file or SQL query (unified prompt-driven path)

The two existing tools already cover the two sub-cases:

| Sub-case | Existing tool | Sidecar route | Python function |
|---|---|---|---|
| SQL / connection | `create_datasource_from_query` | `POST /datasource/from-query` | `hyper_builder.query_to_dataframe()` → `dataframe_to_hyper()` → `tds_builder.hyper_to_tdsx()` |
| CSV / records | `create_datasource_from_table` | `POST /datasource/from-table` | same chain |

A new unified tool `create_datasource` could accept either a `filePath` (with type inference) or a `connection`+`sql` and dispatch internally to the appropriate sidecar route — or could merge into a single sidecar route that accepts a discriminated-union body.

**Adding Parquet/JSON file support** touches:
- `sidecar/hyper_builder.py` — `query_to_dataframe()` at line 99: add `elif ctype == "parquet": df = pd.read_parquet(...)` and `elif ctype == "json": df = pd.read_json(...)`. No other files change.
- `src/tools/createDatasourceFromTable.ts` — the `csvPath` parameter would be renamed or the schema extended to accept `filePath` + optional `fileType` discriminator.
- `sidecar/server.py` — `TableRequest` model would gain `file_path` / `file_type` fields.

No changes needed to `tds_builder.py`, `twb_builder.py`, or `restClient.ts`.

### 2. Dashboard workbook authoring

**Current state of `twb_builder.py`:**

`build_twb_xml()` (line 138) generates:
```xml
<workbook source-build="2024.1.0" version="18.1">
  <datasources>
    <datasource caption="…" name="sqlproxy.X" version="18.1" inline="true">
      <repository-location id="X" path="/t/site/datasources" revision="1.0" site="site" />
      <connection class="sqlproxy" dbname="X" channel="https" directory="/dataserver" port="443" server="host" />
      <column … />  <!-- one per referenced field -->
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name="Sheet Title">
      <table>
        <view>…datasource reference + datasource-dependencies…</view>
        <rows>…</rows>
        <cols>…</cols>
        <panes><pane><mark class="Bar" /></pane></panes>
      </table>
    </worksheet>
  </worksheets>
  <windows>
    <window class="worksheet" name="Sheet Title" />
  </windows>
</workbook>
```

**There is no `<dashboards>` block.** The workbook opens in the first worksheet. To add real dashboard layout, `build_twb_xml()` needs a new section emitted after `<worksheets>`:

```xml
<dashboards>
  <dashboard name="Dashboard 1">
    <size maxheight="768" maxwidth="1024" minheight="768" minwidth="1024" />
    <zones>
      <zone h="100000" id="1" type="layout-basic" w="100000" x="0" y="0">
        <zone h="50000" id="2" name="Sheet 1 Title" type="worksheet" w="100000" x="0" y="0" />
        <zone h="50000" id="3" name="Sheet 2 Title" type="worksheet" w="100000" x="0" y="50000" />
      </zone>
    </zones>
  </dashboard>
</dashboards>
```

The exact functions to extend in `sidecar/twb_builder.py`:

- **`build_twb_xml()`** (line 138): add an optional `dashboards: list[dict]` parameter; after the `windows` block, call a new `_build_dashboard()` helper and append the resulting `ET.Element` to `workbook`.
- **`_build_dashboard()`** (new function): accept a dashboard spec (name, size, list of zone placements referencing worksheet titles by name) and build the `<dashboard><size /><zones>…</zones></dashboard>` XML.
- **`build_starter_twbx()`** (line 223): accept and forward `dashboards` to `build_twb_xml()`.

On the sidecar API boundary (`sidecar/server.py`):
- `WorkbookRequest` model: add optional `dashboards: list[DashboardModel] = []` field.
- The `workbook_starter` route: pass `dashboards` through to `twb_builder.build_starter_twbx()`.

On the TypeScript side:
- `src/sidecar.ts` `WorkbookArgs` interface: add `dashboards?: DashboardSpec[]`.
- `src/sidecar.ts` `buildStarterWorkbook()`: pass through.
- `src/tools/createStarterWorkbook.ts`: extend the `inputSchema` with an optional `dashboards` zod array; wire into the sidecar call.

### 3. BI analyst planning / interview / audience logic

**Recommendation: keep planning entirely in MCP tool return values; do not embed LLM calls in the server.**

This MCP server runs as a subprocess with stdio. It has no LLM client, no streaming, and no session memory. The "analyst interview" loop — asking clarifying questions, accumulating context, generating a visualization plan — is fundamentally an agent workflow, not a tool call. Embedding it in the server would require either a second LLM client (coupling, cost) or a complex stateful session mechanism alien to the MCP protocol.

The right decomposition:

1. Add a new **`plan_dashboard`** tool (or extend `create_starter_workbook`) that accepts a `prompt: string`, `audience: enum(exec|analyst|ops|…)`, and `mode: enum(autonomous|interview|direct)`. The tool's handler runs a lightweight deterministic planning step (field selection heuristics from the datasource's column list, audience-driven size/mark-type defaults) and returns a **structured plan** object: `{ clarifyingQuestions: string[] | null, sheets: SheetSpec[], dashboardLayout: DashboardSpec, rationale: string }`.
2. In `interview` mode the tool returns `clarifyingQuestions` and an incomplete plan; the agent presents the questions to the user, collects answers, and calls the tool again with the updated prompt.
3. The agent (Claude, Cursor, etc.) is the interviewer and the LLM. The MCP tool is the structured-output engine and the publisher.

This approach requires:
- A new `src/tools/planDashboard.ts` (or augmented `createStarterWorkbook.ts`).
- A new sidecar route or TypeScript-only planning logic (no Python required if the plan is purely structural).
- The `audience` parameter shapes defaults: exec = text/KPI marks, fewer sheets, large fonts; analyst = bar/line, dense, multi-sheet; ops = table/text, live-refresh emphasis.

---

## Regression-Guard Tests That Must Keep Passing

All 46 tests must stay green on `make ci` (modulo the `.cursor/` lint issue which predates the feature):

**TypeScript (28 tests — `npm test`):**
- `tests/restClient.test.ts`: `splitIntoChunks` math, `selectPublishStrategy` 64 MB boundary, `signIn` parsing, single-request publish, 3-chunk upload (3 PUTs + 1 finalize POST), mid-stream abort (no finalize), `resolveProjectId` rejects empty/Default/resolves known.
- `tests/secrets.test.ts`: PAT not in sign-in output, PAT not in redacted API error, config error does not echo PAT.
- `tests/tools.test.ts`: 11-tool registration count, all tool names present, `create_datasource_from_query` full wiring, `create_starter_workbook` wiring, all guardrails (delete confirm, delete Default refusal, elevated-capability gate, allowlist rejection), `create_datasource_from_table` input validation.

**Python (18 tests — `cd sidecar && uv run pytest -q`):**
- `sidecar/tests/test_hyper_builder.py`: hyper round-trip row count + all column types, column role assignment, CSV source read, max_rows cap, records_to_dataframe.
- `sidecar/tests/test_tds_builder.py`: `.tdsx` zip has exactly one `.tds` and one `Data/*.hyper`, dbname path format, column role attributes.
- `sidecar/tests/test_twb_builder.py`: sqlproxy datasource reference present, repository-location attributes, one worksheet per sheet spec with correct datasource-dependencies, mark class mapping (bar/line/text), default site path, `.twbx` is a valid zip containing a parseable `<workbook>`.
- `sidecar/tests/test_server.py`: health returns `{"status":"ok"}`, from-table (records) produces a valid `.tdsx` zip, from-table without input returns 400, workbook starter produces `.twbx`, token guard blocks requests without header and passes with matching header.
