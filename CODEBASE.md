# CODEBASE.md — tableau-mcp-publish

## Overview

`tableau-mcp-publish` is the **write side** of Tableau MCP — **vibe-BI on Tableau Cloud**. It
exposes **27** MCP tools over stdio that let an AI agent go from a prompt to governed datasources
(files, queries, or live Snowflake/Presto connections), branded persona-aware dashboards, Tableau
Stories, Pulse metrics, and scheduled/cron-automated refresh — in a handful of tool calls. It is
architecturally complementary to `tableau/tableau-mcp`, which covers reading, querying,
Desktop-local workbook editing, and admin-gated content lifecycle.

The system has two layers:

1. **TypeScript MCP server** (`src/`) — MCP protocol, Tableau REST/VDS/Pulse API access (with
   bounded retry/backoff), project resolution, publish (single-request or chunked), the
   deterministic BI planner, and brand-kit/persona resolution. Spawns the Python sidecar on
   startup.
2. **Python FastAPI sidecar** (`sidecar/`) — does all binary file authoring: Hyper extract creation
   (via pantab), `.tdsx`/`.tds` packaging (hand-built XML + zip), and `.twbx` workbook XML
   generation (including branded output and Tableau Stories). Bound loopback-only, guarded by a
   per-spawn random token.

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
| YAML parsing | `yaml` | 2.9.0 — added for `brand.yaml` (Phase E1) |
| Test runner | Vitest | 2.1.8 |
| Linter | ESLint 9 + `typescript-eslint` | 9.17.0 / 8.18.2 |
| Python sidecar | Python | 3.12.x (uv-pinned; 3.12/3.13 both tested in CI) |
| Web framework | FastAPI | 0.121.0 |
| ASGI server | uvicorn[standard] | 0.32.0 |
| Validation | Pydantic v2 | 2.9.2 |
| Hyper extract | tableauhyperapi | 0.0.21408 |
| DataFrame bridge | pantab | 5.2.0 |
| DataFrames | pandas | 2.2.3 |
| Excel read | openpyxl | 3.1.5 |
| Parquet read | pyarrow | (transitive via pantab) |
| Python linter | ruff | 0.8.4 |
| Python type check | mypy | 1.13.0 (strict) |
| Python test | pytest | 8.3.4 |
| Package manager (TS) | npm | — |
| Package manager (Py) | uv | 0.11.18+ |

Optional Python extras (`uv sync --extra connectors`): `snowflake-connector-python`, `psycopg[binary]`, `sqlalchemy` — used only by the `connection: {type: snowflake|postgres}` branch of `create_datasource_from_query`; not required for CI, the file-ingest path, or `create_live_datasource` (which builds a live `.tds` without a driver — Tableau's own connector handles the live query at render time, not this process).

---

## Build / Run / Test

```bash
# TypeScript
npm install
npm run build         # tsc -> dist/
npm run lint           # eslint .
npm run typecheck      # tsc --noEmit
npm test                # vitest run (525 tests)

# Python sidecar
cd sidecar
uv sync --all-extras   # create .venv with dev + connectors
uv run ruff check .     # lint
uv run mypy --strict .  # type check
uv run pytest -q       # 338 tests

# Full CI gate (equivalent to GitHub Actions)
make ci               # build + lint + test + sidecar-lint + sidecar-typecheck + sidecar-test
```

Real counts as of this branch (`feat/exec-dashboards`, re-run directly for this doc pass):
**525 TypeScript tests across 23 files** (`npm test`) + **338 Python tests across 20 files**
(`cd sidecar && uv run pytest -q`) = **863 tests total**. `ACCEPTANCE.md`'s last recorded gate
(859) predates one small follow-up security-hardening commit (VB-02, shell-quoting the cron
templates) that added 4 TypeScript tests — see `git log` for `fix(security): shell-quote cron-line
interpolations`.

The `make ci` target does **not** run `npm run typecheck` separately; the `build` step already runs
`tsc` (which is a full type + emit check). The `lint` step does not run `eslint` with
`--max-warnings 0`; it exits non-zero only on errors.

---

## Map

```
tableau-mcp-publish/
├── src/
│   ├── index.ts              # Entry point — registers all 27 tools, signs in, spawns sidecar, starts stdio transport
│   ├── config.ts             # Zod schema for env-based config (SERVER, SITE_NAME, PAT_NAME, PAT_VALUE…)
│   ├── restClient.ts         # TableauRestClient: signIn/signOut, publish (single + chunked), CRUD, permissions,
│   │                         #   delegates schedule/webhook/Pulse/VDS request-building to rest/
│   ├── sidecar.ts            # AuthoringSidecar: spawns uv/uvicorn, health-polls, posts to /datasource/*, /workbook/*
│   ├── rest/                 # REST/VDS/Pulse module family (Phase E2/E3) — see docs/architecture.md
│   │   ├── xml.ts            #   xmlEscape / asArray shared helpers
│   │   ├── errors.ts         #   TableauApiError, parseTableauErrorBody, parseRetryAfterMs
│   │   ├── retry.ts          #   withRetry() — shared bounded exponential-backoff loop
│   │   ├── vds.ts            #   readDatasourceMetadata() — VizQL Data Service field metadata
│   │   ├── schedules.ts      #   extract-refresh task XML + frequency/interval validation
│   │   ├── webhooks.ts       #   webhook create/list/delete XML + HTTPS-only validation
│   │   ├── credentials.ts    #   <connectionCredentials> XML fragment for live datasources
│   │   └── pulse.ts          #   Pulse definition/metric JSON bodies + VDS-backed pre-flight check
│   ├── branding/              # Brand-kit loading + persona resolution (Phase E1)
│   │   ├── schema.ts         #   Zod schema for brand.yaml (every field defaulted)
│   │   ├── load.ts           #   loadBrand() / resolvePersona() — the ONLY I/O in this layer
│   │   └── builderBrand.ts   #   Pure projection: BrandFile → sidecar's flat BuilderBrand wire shape
│   ├── planner/               # Deterministic BI planner (no LLM calls)
│   │   ├── schema.ts         #   DashboardPlan / ClarifyingQuestions / DashboardProposal Zod types
│   │   ├── fields.ts         #   Field-role inference from column hints
│   │   ├── marks.ts          #   Mark-type selection, KPI-strip/color/scatter/geo encoding, Few/Tufte hints
│   │   ├── audience.ts       #   Audience + persona-override clamp (chartDeny, kpiEmphasis, maxSheets)
│   │   ├── questions.ts      #   Interview-mode clarifying-question generation
│   │   ├── plan.ts           #   generatePlan()/generateInterview() — wires the stages, builds storyArc
│   │   └── proposal.ts       #   buildProposal() — DashboardPlan → human-readable DashboardProposal
│   └── tools/
│       ├── context.ts        # ToolContext interface + toolResult() helper
│       ├── projects.ts       # list_projects, create_project
│       ├── content.ts        # list_content, refresh_datasource, delete_content
│       ├── permissions.ts    # set_permissions (allowlist + elevated gate)
│       ├── createDatasourceFromQuery.ts  # create_datasource_from_query
│       ├── createDatasourceFromTable.ts  # create_datasource_from_table
│       ├── createDatasourceFromFile.ts   # create_datasource_from_file (csv/json/jsonl/xlsx/parquet)
│       ├── createLiveDatasource.ts       # create_live_datasource (Snowflake/Presto, no extract)
│       ├── createStarterWorkbook.ts      # create_starter_workbook
│       ├── designDashboard.ts            # design_dashboard (propose→confirm; persona-aware)
│       ├── buildFromPlan.ts              # build_from_plan (DashboardPlan → embedded .twbx + story → publish)
│       ├── validateBrand.ts              # validate_brand
│       ├── getDatasourceFields.ts        # get_datasource_fields (VDS)
│       ├── schedules.ts                  # schedule_refresh, list_refresh_schedules, delete_refresh_schedule
│       ├── webhooks.ts                   # create_webhook, list_webhooks, delete_webhook
│       ├── pulse.ts                      # create_pulse_definition, list_pulse_definitions, create_pulse_metric, delete_pulse_definition
│       ├── publishDatasource.ts          # publish_datasource (pre-built file)
│       └── publishWorkbook.ts            # publish_workbook (pre-built file)
├── tests/                     # 23 files, 525 tests (vitest) — one file per src/ module, roughly
│   ├── restClient.test.ts, restRetry.test.ts, retry.test.ts   # publish strategy, retry policy
│   ├── secrets.test.ts, credentials.test.ts                    # PAT/password never logged
│   ├── sidecar.test.ts, sidecar-columns.test.ts                 # AuthoringSidecar wiring
│   ├── vds.test.ts, schedules.test.ts, webhooks.test.ts, pulse.test.ts  # rest/ module tests
│   ├── branding.test.ts, builderBrand.test.ts                   # brand.yaml load + projection
│   ├── planner.test.ts, planner-slice4.test.ts, planner-slice5.test.ts,
│   │   planner-slice6.test.ts, planner-storyArc.test.ts         # planner pipeline + proposal + storyArc
│   ├── schema-growth.test.ts                                    # DashboardPlan schema backward-compat
│   ├── cronTemplates.test.ts, generateCron.test.ts, refreshLocalArgs.test.ts  # local-file automation
│   └── tools.test.ts                                            # integration: all 27 tools registered
├── scripts/
│   ├── demo.ts                 # npm run demo — datasource + starter workbook
│   ├── demo-dashboard.ts       # npm run demo:dashboard — propose→build pipeline
│   ├── demo-superstore.ts      # npm run demo:superstore — rich exec dashboard + optional persona brand
│   ├── refresh-local.ts        # npm run refresh:local — re-ingest + republish a local-file datasource
│   ├── generate-cron.ts        # npm run cron:generate — emit crontab/launchd artifacts (never installs)
│   ├── cronTemplates.ts        # pure template builders for generate-cron.ts (no I/O; shell-quoted)
│   ├── mcp-smoke.ts             # npm run test:mcp-smoke — stdio handshake + tool listing
│   └── verify-setup.ts         # npm run verify-setup — pre-flight config/sign-in/sidecar check
├── sidecar/
│   ├── server.py             # FastAPI app — token guard, /health, /datasource/from-query,
│   │                         #   /datasource/from-table, /datasource/from-file, /datasource/live,
│   │                         #   /workbook/starter, /workbook/dashboard  (7 routes)
│   ├── hyper_builder.py      # DataFrame/SQL/CSV/JSON/XLSX/Parquet → .hyper extract (pantab + tableauhyperapi),
│   │                         #   numeric/date-string coercion
│   ├── tds_builder.py        # .hyper → .tdsx (hand-built TDS XML + zip); build_live_tds() for live connections
│   ├── twb_builder.py        # build_twb_xml() / build_starter_twbx() / build_embedded_twb_xml() /
│   │                         #   build_embedded_twbx() — worksheets + <dashboards> block + _build_story()
│   ├── pyproject.toml        # uv project config, ruff/mypy/pytest settings
│   └── tests/                # 20 files, 338 tests
│       ├── test_hyper_builder.py, test_hyper_builder_formats.py  # extract round-trips, format coverage
│       ├── test_tds_builder.py, test_tds_builder_live.py         # .tdsx / live .tds packaging
│       ├── test_twb_builder.py, test_twb_dashboard.py, test_twb_dashboard_layout.py,
│       │   test_twb_embedded.py, test_twb_encodings.py, test_twb_kpi_tile.py,
│       │   test_twb_map_filled.py, test_twb_scatter.py, test_twb_branding.py, test_twb_story.py
│       │                                                          # worksheet/dashboard/story XML structure
│       ├── test_twb_schema_validation.py                          # official TWB XSD gate + XXE guard
│       ├── test_schema_growth.py                                   # sidecar Pydantic models backward-compat
│       └── test_server.py, test_server_new_routes.py,
│           test_server_live_datasource.py, test_server_rich_dashboard.py  # FastAPI route integration
├── brand.yaml                 # Brand kit: palette/typography/formats/rules/personas (Phase E1)
├── Makefile                    # CI gate: build lint test sidecar-lint sidecar-typecheck sidecar-test
├── package.json
├── tsconfig.json               # strict, NodeNext, rootDir=src, outDir=dist
├── eslint.config.js            # ignores: dist/, node_modules/, sidecar/, coverage/  (NOT .cursor/)
└── vitest.config.ts            # tests/**/*.test.ts, extensionAlias .js->.ts
```

---

## Architecture & Data Flow

```
AI agent (Claude / Cursor / etc.)
        │  MCP stdio (JSON-RPC)
        ▼
  src/index.ts  ──────────── McpServer (MCP SDK)
        │                          │
        │ registers 27 tools       │
        ▼                          │
  ToolContext { config, rest, sidecar }
        │                          │
  ┌─────────────┐       ┌──────────────────────┐
  │ TableauRest │       │  AuthoringSidecar     │
  │ Client      │       │  uv run uvicorn       │
  │ (undici)    │       │  server:app           │
  │ + rest/*.ts │       │  loopback only,       │
  │ (retry-     │       │  per-spawn token      │
  │  hardened)  │       │                       │
  │             │       │  FastAPI routes:      │
  │ Tableau     │       │  /health              │
  │ REST API    │       │  /datasource/from-query│
  │ v3.28       │       │  /datasource/from-table│
  │ + VDS       │       │  /datasource/from-file│
  │ + Pulse     │       │  /datasource/live     │
  │             │       │  /workbook/starter    │
  └─────────────┘       │  /workbook/dashboard  │
                        │                       │
  branding/load.ts ─┐   │  hyper_builder.py     │
  (reads brand.yaml,│   │   pandas + pantab     │
   only I/O in the  │   │   -> .hyper (+ coercion)│
   branding layer)  │   │                       │
        │            │  │  tds_builder.py       │
        ▼            └─▶│   XML + zip           │
   planner/* (pure) ────┤   -> .tdsx / .tds     │
                        │                       │
                        │  twb_builder.py       │
                        │   XML + zip           │
                        │   -> .twbx (+ story)  │
                        └──────────────────────┘
```

**Trust boundaries:**
- The sidecar is spawned with `stdio: ['ignore','ignore','pipe']` — its stdout never reaches the MCP channel.
- The sidecar binds `127.0.0.1` only. A random 24-byte hex token is generated per spawn, injected as `SIDECAR_TOKEN` env, and required as `X-Sidecar-Token` on every request (constant-time `hmac.compare_digest`).
- The Tableau PAT secret is sent only in the sign-in body, never logged (asserted in `tests/secrets.test.ts`). Config validation errors print the offending field path, never the value.
- Live-connection database credentials (`create_live_datasource`) are embedded only at publish time via an in-memory `<connectionCredentials>` XML fragment — never written to the `.tds` file or logged (asserted in `tests/credentials.test.ts`).
- `resolveProjectId` hard-refuses the `"Default"` project by name and empty names, preventing silent publishes to ungoverned space.

**Publish strategy:** `selectPublishStrategy()` in `src/restClient.ts` — files `< 64 MiB` use a single `multipart/mixed` POST; files `>= 64 MiB` (incl. exactly 64 MiB) use the `fileUploads` chunked session (TSC-aligned). Mid-stream abort does not issue a finalize POST.

**Retry policy:** `withRetry()` (`src/rest/retry.ts`), shared by `restClient.ts`, `rest/vds.ts`, and `rest/pulse.ts`. Retries only 429/502/503/504, only for calls marked idempotent by their caller, with full-jitter exponential backoff (max 3 attempts, ~8s total budget by default) honoring an upstream `Retry-After` header. See `docs/architecture.md` and `docs/adr/0008-rest-retry-hardening.md`.

---

## Alignment with official Tableau tooling

This section records deliberate divergences from official Tableau libraries and the rationale for
two non-adoptions, so they are not re-evaluated on every review.

### Divergences from `tableau/server-client-python` (TSC)

TSC is the canonical Python REST client for Tableau Server/Cloud. Our publish path aligns with its
semantics.

| Aspect | TSC | `tableau-mcp-publish` | Rationale |
|---|---|---|---|
| Chunking boundary | `file_size >= 64 MB` (exact 64 MB → chunked) | `file_size >= 64 MB` — **aligned to TSC** | A single multipart request at exactly 64 MB exceeds the cap once boundary overhead is added, so `>=` is correct. |
| Chunk size | 50 MB per chunk | 64 MB per chunk | Larger chunks reduce round-trips; acceptable on Cloud. |
| REST API version | Auto-negotiated (latest supported by the server) | Pinned to 3.28 | Predictability over auto-negotiation; update explicitly when new endpoints are needed. |
| Abort / unfinalized session | Aborts unfinalized upload sessions | Does not issue a `finalize` POST on mid-stream abort | Behaviour matches TSC: an unfinalized session is automatically discarded by the server. |
| Retry policy | TSC does not retry by default | Bounded idempotency-aware retry (429/5xx only, GET/idempotent-POST only) | Cloud rate-limits aggressively under the tool surface's higher call volume (scheduling, webhooks, Pulse, VDS); see `docs/adr/0008-rest-retry-hardening.md`. |

### Official TWB XSD (`tableau/tableau-document-schemas`)

`tableau/tableau-document-schemas` publishes `schemas/2026_1/twb_2026.1.0.xsd` — a W3C XSD that
describes the `.twb` XML format, maintained by the official Tableau team as a machine-validatable
fidelity gate. The sidecar test suite vendors this schema and validates every builder output
(regular dashboards, embedded extracts, and Stories) against it via `lxml`. This catches the class
of "parses but won't render" defects that are invisible to structural assertions about expected
elements — it caught a real bug during Story implementation (two sibling `<dashboards>` wrappers;
see `docs/adr/0012-story-shared-dashboards-container.md`).

### Non-adoption: `tableau/document-api-python`

`document-api-python` is a library for modifying **existing** `.twb`/`.tds` files. Its own README
states it "doesn't support creating files from scratch"; `Workbook.__init__` only opens existing
files; `_prepare_dashboards()` returns names only (no zone writer); worksheets are name stubs
(`# TODO: A real worksheet object`). It cannot author the XML we need to emit. We hand-roll the
TWB/TDS XML in `sidecar/twb_builder.py`/`sidecar/tds_builder.py`. No re-evaluation is needed unless
the library gains create-from-scratch capability.

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
- Errors thrown from handlers propagate as MCP error responses. REST/VDS/Pulse failures throw a typed `TableauApiError` (`src/rest/errors.ts`).
- Destructive tools follow a uniform `confirm: boolean` gate pattern (`delete_content`, `delete_refresh_schedule`, `delete_webhook`, `delete_pulse_definition`); permission-elevation follows a uniform `confirmElevated` pattern (`set_permissions`).

**Python:**
- All modules use `from __future__ import annotations`.
- Pydantic v2 models with `model_config = ConfigDict(populate_by_name=True)` and camelCase aliases for the JSON API boundary — but `.model_dump()` always yields snake_case field names, never the aliases ("the model_dump lesson," documented at each affected model in `server.py`).
- Ruff line-length 100, target py312, rules E/F/I/UP/B/SIM.
- mypy `--strict`, excludes `tests/`.
- Output files go to `tempfile.gettempdir()/tableau-mcp-publish/<uuid4>.<ext>`.

**Git/commit conventions:** Conventional commits (`feat`, `fix`, `docs`, `ci`, `chore`, `perf`). Scope tags used, e.g. `feat(pulse)`, `fix(security)`, `docs(acceptance)`.

---

## Tests

Real counts as run for this doc pass (2026-07-17): **525 TypeScript tests** (`npm test`) across 23
files, **338 Python tests** (`cd sidecar && uv run pytest -q`) across 20 files. See the file map
above for what each test file covers; the highlights:

- `tests/tools.test.ts` — the single integration test asserting all **27** tools register with a
  description + input/output schemas, plus full call-wiring for the multi-step tools
  (`design_dashboard`, `build_from_plan`, `create_live_datasource`) and every destructive-tool
  confirm-gate.
- `tests/planner*.test.ts` + `tests/planner-storyArc.test.ts` — the full deterministic planning
  pipeline: field inference, mark selection, audience + persona clamps, KPI-strip/color/scatter/geo
  encoding, `storyArc` generation, and `DashboardProposal` projection.
- `tests/restClient.test.ts`, `tests/restRetry.test.ts`, `tests/retry.test.ts` — chunk math,
  publish-strategy boundary, sign-in parsing, and the shared retry/backoff policy (deterministic
  via injected `sleep`/`jitterFn`).
- `tests/vds.test.ts`, `tests/schedules.test.ts`, `tests/webhooks.test.ts`, `tests/pulse.test.ts` —
  each `rest/` module's request-building, response-parsing, and error-classification behavior.
- `tests/branding.test.ts`, `tests/builderBrand.test.ts` — `brand.yaml` load/default/validation and
  the pure projection to the sidecar's wire shape.
- `tests/secrets.test.ts`, `tests/credentials.test.ts` — standing assertions that the PAT and live-
  connection passwords never appear in logs, sign-in output, or redacted API errors.
- `tests/cronTemplates.test.ts`, `tests/generateCron.test.ts`, `tests/refreshLocalArgs.test.ts` —
  local-file refresh automation, including adversarial shell-injection test cases (`$(id)`, quote
  breakout) for the crontab-line template.
- Sidecar: `test_twb_schema_validation.py` gates every builder output path (starter, embedded,
  branded, story) against the official TWB XSD; `test_twb_story.py`, `test_twb_branding.py`,
  `test_twb_kpi_tile.py`, `test_twb_scatter.py`, `test_twb_map_filled.py`,
  `test_twb_dashboard_layout.py` cover each Phase-1/E1/E4 encoding and layout feature end-to-end
  through the actual XML output.

---

## Dependencies & Risk

**Production TypeScript deps (4):**
- `@modelcontextprotocol/sdk@1.29.0` — Anthropic's official MCP server SDK.
- `undici@7.28.0` — Node.js HTTP client; `npm audit --omit=dev` reports 0 vulnerabilities.
- `yaml@2.9.0` — `brand.yaml` parsing (added Phase E1).
- `zod@3.25.76` — schema validation.

**Python deps of note:**
- `tableauhyperapi@0.0.21408` — Tableau-proprietary Hyper engine; binary wheel; no Python 3.13 wheel yet (uv uses Python 3.12 inside the venv).
- `pantab@5.2.0` — thin pandas/Arrow bridge over tableauhyperapi.
- `starlette==0.49.3` (transitive via `fastapi==0.121.0`) — pinned explicitly to clear a set of transitive advisories flagged in an earlier ecosystem review.
- Database connectors are optional extras, not in the default install; `create_live_datasource` needs none of them (it builds connection-topology XML only — Tableau's own server-side connector executes the live query).

**License:** MIT (repo). Dependencies are MIT/BSD/Apache except `tableauhyperapi` (Tableau proprietary).

**npm tarball (`npm pack --dry-run`):** 130 files, ~269 kB packed / ~1.1 MB unpacked. Includes:
`dist/` (all tools + planner + branding + rest/), `brand.yaml`, `sidecar/*.py` (4 files),
`sidecar/pyproject.toml`, `sidecar/uv.lock`, `LICENSE`, `README.md`, `package.json`. Excludes:
`sidecar/tests/`, `sidecar/.venv/`, `scripts/`, `tests/`, caches, logs, `.cursor/`.

---

## Tech Debt / Issues

1. **Lint gate broken by `.cursor/` directory.** `eslint.config.js` ignores `dist/`,
   `node_modules/`, `sidecar/`, `coverage/` — but not `.cursor/`. If `.cursor/` is present in the
   working tree (it is `.gitignore`d, so this affects local runs only, not upstream CI), ESLint
   reports errors on those CJS hook scripts and `make ci` (`npm run lint`) exits non-zero locally.
   The fix is one line: add `".cursor/**"` to the `ignores` array.

2. **No standalone `typecheck` step in `make ci`.** The Makefile runs `build` (which emits JS and
   catches type errors via `tsc`), but a dedicated `typecheck` (`tsc --noEmit`) step is absent from
   the `ci` target. In practice `tsc` errors already block `build`, so this is not a real coverage
   gap, just a missing explicit step for import-only type errors that produce no artifacts.

3. **`getDatasource` has a fallback list-all-datasources** when `contentUrl` is missing from the
   GET response (`src/restClient.ts`). Correct but can be slow on large sites and is a fragility
   point if `contentUrl` is reliably missing.

4. **Structured logging is `process.stderr.write` concatenation** — no log levels, no JSON format,
   no correlation IDs, and no persistent metrics/telemetry surface. Adequate for an MCP stdio
   server today (see `docs/runbook.md`'s monitoring section for the practical workaround) but would
   need a real logger (e.g., `structlog` on the Python side is already in the deps but unused) for
   anything beyond single-session debugging.

5. **Pulse's create-definition payload shape is unconfirmed against a live site** (`400` with no
   field-level detail on the one live attempt so far). Code-complete and honestly flagged; see
   `docs/adr/0011-pulse-best-effort-payload.md` and `docs/tool_reference.md`'s Pulse section for the
   concrete next step.

6. **Live-connection XML attribute mapping is VERIFY-LIVE.** `sidecar/tds_builder.py`'s
   `SNOWFLAKE_ATTRS`/`PRESTO_ATTRS` are this project's best-documented guess at the connector-class
   attribute spelling and have not yet been confirmed against a Desktop-exported `.tds`.

7. **`IncrementalRefresh`'s exact token spelling is VERIFY-LIVE** in `schedule_refresh` — reference
   material is inconsistent between `IncrementalRefresh` and `IncrementalExtract`. The tool defaults
   every caller to `FullRefresh` and only emits `IncrementalRefresh` on explicit request.
