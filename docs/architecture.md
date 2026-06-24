# Architecture

`tableau-mcp-publish` is two layers that share a machine but split responsibilities cleanly.

## TypeScript MCP server (`src/`)

- **`index.ts`** — MCP server entry (stdio). Loads config, signs in to Tableau, spawns the
  sidecar, registers all 14 tools, and handles graceful shutdown (sign out + stop sidecar).
- **`config.ts`** — env-var config (`SERVER`, `SITE_NAME`, `PAT_NAME`, `PAT_VALUE`,
  `TABLEAU_API_VERSION`, `SIDECAR_HOST/PORT`), validated with zod. Errors never echo the PAT.
- **`restClient.ts`** — Tableau REST client over `undici`: PAT sign-in, projects, content,
  permissions, and publishing. Publishing chooses **single multipart** (<64 MB) or **chunked
  `fileUploads`** (≥64 MB, incl. exactly 64 MB); a failed chunk aborts without finalizing.
- **`sidecar.ts`** — spawns the Python sidecar and calls it over loopback HTTP with a per-spawn
  `X-Sidecar-Token` header. Provides `buildDatasourceFromFile` and `buildDashboardWorkbook`
  alongside the existing methods.
- **`tools/`** — one module per tool group; each registers `McpServer.registerTool` with a zod
  input schema, an output schema, and an agent-readable description.
- **`planner/`** — pure TypeScript planning pipeline (no network, no side effects). Used only
  by `design_dashboard`.

## TypeScript planner (`src/planner/`)

The planner is a deterministic, stateless pipeline. Given the same inputs it always returns the
same plan. No LLM calls, no `Date.now()`, no randomness. Each stage is an independently testable
pure function.

```
fieldHints
  └─▶ fields.ts   — field-role inference (dtype → role, 8 name-pattern regexes, cardinality,
  │                  suppress flag for identifiers)
  └─▶ marks.ts    — keyword→markType heuristic (7 priority rules per clause, no-dimension
  │                  downgrade, gap annotations G-01..G-05)
  └─▶ audience.ts — 6-step audience clamp (truncate → drop disallowed marks → KPI lead →
  │                  enforce layout → cap measures/dims per sheet → map guard)
  └─▶ plan.ts     — wires the stages; applies L-01 analyst layout override; returns DashboardPlan
  └─▶ schema.ts   — single Zod source of truth for DashboardPlan / ClarifyingQuestions /
                     SheetSpec / DatasourceSpec; schemaVersion = 1 guard
```

`questions.ts` handles `interview` mode independently: it selects 3–7 questions from a fixed
ordered bank based on which inputs are unknown, then returns a `ClarifyingQuestions` object. No
plan is generated during interview mode.

The `DashboardPlan` schema (in `schema.ts`) is the explicit, versioned contract between
`design_dashboard` (producer) and `build_from_plan` (consumer). Both tools import from this
single module.

## Python authoring sidecar (`sidecar/`)

- **`server.py`** — FastAPI app with five routes, guarded by the shared `X-Sidecar-Token`
  middleware:

  | Route | Purpose |
  |---|---|
  | `GET /health` | Liveness probe |
  | `POST /datasource/from-query` | SQL/CSV → `.hyper` → `.tdsx` |
  | `POST /datasource/from-table` | JSON records or CSV path → `.tdsx` |
  | `POST /datasource/from-file` | CSV / JSON / JSONL / Excel / Parquet → `.tdsx` |
  | `POST /workbook/starter` | Published datasource → `.twbx` (loose worksheets) |
  | `POST /workbook/dashboard` | Published datasource + sheet specs → `.twbx` with `<dashboard>` |

- **`hyper_builder.py`** — DataFrame/SQL/CSV/file → `.hyper` via pantab (Hyper types inferred
  from pandas dtypes). `file_to_dataframe()` dispatches on `fileType`: csv → `pd.read_csv`;
  json → `pd.read_json`; jsonl → `pd.read_json(lines=True)`; xlsx/xls → `pd.read_excel`
  (openpyxl); parquet → `pd.read_parquet` (pyarrow).
- **`tds_builder.py`** — packages a `.hyper` into a `.tdsx` (hand-built `.tds` XML with a `hyper`
  connection + per-column metadata, zipped with the extract under `Data/`).
- **`twb_builder.py`** — generates a `.twb`/`.twbx` bound to a *published* datasource
  (`class='sqlproxy'` + `repository-location`), one worksheet per sheet with shelves + mark class.
  Extended with `_tile_zones()`, `_build_dashboard()`, and optional `dashboards` / `dashboard_layout`
  / `canvas_width` / `canvas_height` params that emit a `<dashboards><dashboard><zones>…</zones>`
  block. When these params are absent the output is byte-identical to the pre-feature version
  (backward-compat guarantee).

## Dashboard XML structure

When `build_from_plan` calls `/workbook/dashboard`, the sidecar appends:

```xml
<dashboards>
  <dashboard name="Dashboard 1">
    <size maxheight="{H}" maxwidth="{W}" minheight="{H}" minwidth="{W}" />
    <zones>
      <zone h="100000" id="1" type="layout-basic" w="100000" x="0" y="0">
        <!-- one <zone type="worksheet"> per sheet -->
        <zone h="50000" id="2" name="Revenue by Region" type="worksheet" w="100000" x="0" y="0" />
        <zone h="50000" id="3" name="KPI"               type="worksheet" w="100000" x="0" y="50000" />
      </zone>
    </zones>
  </dashboard>
</dashboards>
```

Zones use a 0–100000 coordinate grid (independent of the canvas pixel size). `_tile_zones()`
divides the grid evenly; the last zone absorbs the rounding remainder so the total always equals
100,000. Canvas dimensions `W` × `H` come from `AUDIENCE_CONSTRAINTS[audience]` in `audience.ts`
and are passed through `build_from_plan` → sidecar → `<size>`.

## Component and data-flow diagram

```mermaid
flowchart TD
    Agent["AI agent (LLM)\nowns conversation state"] -- "MCP stdio" --> Index["src/index.ts\n14 tools"]

    Index --> CDF["create_datasource_from_file"]
    Index --> DD["design_dashboard"]
    Index --> BFP["build_from_plan"]
    Index --> Other["11 existing tools"]

    DD --> Planner["src/planner/*\npure TS — no network"]
    Planner --> Fields["fields.ts\nfield-role inference"]
    Fields --> Marks["marks.ts\nkeyword heuristic"]
    Marks --> Audience["audience.ts\n6-step clamp"]
    Audience --> Plan["plan.ts\nDashboardPlan"]
    DD -. "plan or questions\n(no side effects)" .-> Agent

    BFP --> SC["sidecar.ts\nbuildDashboardWorkbook"]
    CDF --> SC2["sidecar.ts\nbuildDatasourceFromFile"]
    SC  -- "loopback + X-Sidecar-Token" --> Server["sidecar/server.py"]
    SC2 -- "loopback + X-Sidecar-Token" --> Server

    Server --> Hyper["hyper_builder.py\nfile_to_dataframe + formats"]
    Server --> Tds["tds_builder.py"]
    Server --> Twb["twb_builder.py\n+_build_dashboard\n+_tile_zones\n+canvas size"]

    BFP  --> Rest1["restClient\ngetDatasource / publishWorkbook"]
    CDF  --> Rest2["restClient\npublishDatasource"]
    Other --> Rest3["restClient"]
    Rest1 -- "HTTPS + PAT" --> Cloud[("Tableau Cloud\nREST API")]
    Rest2 -- "HTTPS + PAT" --> Cloud
    Rest3 -- "HTTPS + PAT" --> Cloud
```

## Stateless boundary — the key design invariant

The MCP server is a stateless stdio subprocess. The intelligence about business questions, visual
direction, and conversation history lives entirely in the calling agent (Claude, Cursor, etc.).

| Concern | Owner | Why |
|---|---|---|
| Understanding business questions, judging answers | Calling agent | LLM work; the agent already has the model |
| Conversation history, relaying questions/answers | Calling agent | MCP is request/response; no session memory |
| Deterministic plan generation (field inference → marks → clamps) | Server (`src/planner/`) | Predictable, unit-testable without an LLM call |
| File authoring (`.hyper`/`.tdsx`/`.twb`) and REST publish | Server (Python sidecar + TS REST) | Reuses the proven baseline chain |

Consequence: every planner output is a pure function of its inputs. The interview mode is bounded
to **at most two `design_dashboard` calls** — questions then plan — before `build_from_plan` runs.

## Boundary

REST calls and auth live in TypeScript. All Tableau file authoring lives in Python — the Hyper
and document formats are Python-first, so there is no reason to reimplement them in TS. The
sidecar never touches the network except localhost. The TS planner (`src/planner/`) produces only
structural plans (no network), so it does not cross the TS/Python boundary.

## Why a sidecar (vs. one language)

The MCP ecosystem and the official Tableau server are TypeScript; matching that lets this server
drop into the same client config. But the Hyper API and the practical tooling for `.hyper`/`.tdsx`
authoring are Python. A spawned, token-guarded localhost sidecar gets the best of both without a
network dependency or a reimplementation.

See `docs/publish_lifecycle.md` for the end-to-end create → package → publish flow, and the ADRs
in `docs/adr.md` for the key decisions.
