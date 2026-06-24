# Feature: Prompt-Driven Authoring (Datasource + Dashboard from Prompt)

## Problem statement & goal

Today, `tableau-mcp-publish` requires an agent to supply fully-formed `sheets` specs
(field names, mark types, row/col shelves) when calling `create_starter_workbook`.  That
is a builder's interface, not an analyst's interface.  The feature described here lifts
the abstraction: a user can describe what they want in plain language — a file or query
to load, a business question to answer, an audience to design for, and as much or as
little visual direction as they want — and the system produces a **published Tableau
dashboard** (worksheets arranged on a `<dashboard>` layout) plus a governed datasource
on Tableau Cloud.  The gap to close is (a) accepting richer input sources (Parquet, Excel,
JSON, SQL), (b) translating a business question and audience context into a concrete
`sheets` + `dashboard` spec, and (c) providing a structured interview path when the user
wants to be guided rather than directive.

---

## Functional requirements

### F-1 — File-based datasource from prompt

**F-1.1** The system accepts a local file path as the datasource source.  Supported
formats at MVP: CSV (already works), JSON (newline-delimited or array), Excel
(`.xlsx`/`.xls`, first sheet by default).  Parquet is supported if `pyarrow` is already
in the sidecar venv (it is); treat it as supported.

**F-1.2** For Excel files the caller may optionally specify a sheet name or zero-based
sheet index.  If omitted, the first sheet is used.

**F-1.3** For JSON files the caller may provide a `jsonPath` expression (e.g. `$.data`)
to select the array of records.  If omitted, the top-level value is assumed to be the
record array (or newline-delimited JSON).

**F-1.4** The file ingest path is an extension of the existing `create_datasource_from_table`
contract (new `filePath` parameter replacing `csvPath`; `csvPath` stays for backwards
compatibility).  The sidecar's `/datasource/from-table` endpoint gains format-dispatch
logic.

**F-1.5** A SQL query remains a valid alternative to a file.  No change to
`create_datasource_from_query`.

**F-1.6** Unsupported extensions (anything other than `.csv`, `.json`, `.jsonl`, `.xlsx`,
`.xls`, `.parquet`) are rejected at input schema validation with a clear error message
listing supported formats.

---

### F-2 — Dashboard output (not just loose worksheets)

**F-2.1** `create_starter_workbook` (existing) produces loose worksheets.  A new tool,
`create_dashboard_workbook`, produces a `.twbx` that includes both the worksheets
**and** a `<dashboard>` XML element that tiles them in a tiled layout, then publishes
the result.

**F-2.2** The dashboard layout tiles worksheets in a single-column vertical stack by
default.  An optional `dashboardLayout` parameter accepts `"tiled_vertical"` (default)
or `"tiled_horizontal"`.

**F-2.3** The `.twb` XML `<dashboard>` element must include one `<zone>` per worksheet,
each referencing the worksheet by its `name` attribute.  The zones form a
`<layout-options>` `rows`-driven or `cols`-driven tiled grid consistent with the chosen
layout direction.

**F-2.4** The published artifact is a workbook that Tableau Desktop / Cloud can open and
display the dashboard view as the default tab.

---

### F-3 — Three authoring modes

The feature adds one new MCP tool (`design_dashboard`) whose first call always returns a
structured plan (never immediately builds), regardless of the mode chosen.  A separate
tool (`build_from_plan`) consumes the finalized plan and produces the artifacts.  This
two-call pattern is mandatory because MCP is a synchronous request/response protocol:
the agent must be able to relay the plan to the user and accept feedback before the
expensive build step.

#### Mode A — Autonomous ("answer a business question")

**F-3.1** Input: `businessQuestion` (string, required), `datasourceLuid` (string,
required), `datasourceName` (string, required), `audience` (enum, required — see F-4),
`projectName` (string, required), optional `workbookName`.

**F-3.2** `design_dashboard` with `mode: "autonomous"` returns a `DashboardPlan` object
(see Contract section) containing: a suggested workbook name, a 1-3 sentence rationale
explaining how the plan answers the business question, and a `sheets` array of sheet
specs (title, mark type, rows, cols, measures, rationale) plus a `dashboardLayout`
recommendation — all derived from the business question and audience without further user
input.

**F-3.3** The agent relays the plan to the user (the tool text content is the
human-readable version).  The user may accept or modify it, then call `build_from_plan`
with the (possibly modified) plan.

#### Mode B — Interview ("ask me questions first")

**F-3.4** Input to `design_dashboard` with `mode: "interview"`: `datasourceLuid`,
`datasourceName`, `audience` (optional — can be refined via interview), `projectName`,
optional `context` string (any initial context the user wants to give).

**F-3.5** `design_dashboard` with `mode: "interview"` returns a `ClarifyingQuestions`
object: a list of 3–7 structured questions a senior BI analyst would ask before
designing a dashboard, each with an `id`, `question` text, and optional `hint`.  No
build happens at this stage.

**F-3.6** The agent relays the questions to the user one at a time or as a batch (agent
discretion).  The user's answers are collected by the agent and passed to a second call
to `design_dashboard` with `mode: "interview_followup"` supplying the original inputs
plus `answers: Record<questionId, string>`.  This returns a full `DashboardPlan` (same
shape as Mode A output).

**F-3.7** The user then calls `build_from_plan` with the plan.  At most two
`design_dashboard` calls are needed to reach a plan: the initial questions call, then
the `interview_followup` call that resolves to a plan.

#### Mode C — Directed ("I'll tell you what I want")

**F-3.8** Input to `design_dashboard` with `mode: "directed"`: `datasourceLuid`,
`datasourceName`, `audience`, `projectName`, `directions` (string — the user's explicit
visualization and information requirements), optional `workbookName`.

**F-3.9** `design_dashboard` with `mode: "directed"` returns a `DashboardPlan` that
maps the user's explicit directions to concrete `sheets` specs, making only the minimum
interpretive decisions needed where the directions are ambiguous.  The plan's `rationale`
cites which direction maps to which sheet.

---

### F-4 — Audience enum

**F-4.1** `audience` is an enum with four values and must be documented with its effect:

| Value | Label | Design effect |
|---|---|---|
| `"exec"` | Executive | Max 3 KPI tiles + 1 trend; large text; no dense tables; annotation on the most important number; minimal axis labels. |
| `"analyst"` | Analyst / Power User | Up to 8 sheets permitted; dense tables and scatter plots acceptable; full axis labels; no forced large-text mode. |
| `"operational"` | Operational / Frontline | Status indicators (text marks) prominent; action-oriented KPIs; mobile-friendly single-column layout preferred. |
| `"mixed"` | Mixed / General | Balanced: 4–6 sheets; prefer bar/line; one summary KPI; standard density. |

**F-4.2** The `sheets` array produced by `design_dashboard` must be consistent with the
audience rules: sheet count, mark type choices, and layout direction are constrained by
the table above.  These constraints are asserted by unit tests that check the plan output
shape, not live Tableau render.

---

### F-5 — `build_from_plan` tool contract

**F-5.1** Input: a `DashboardPlan` object (produced by `design_dashboard` in any mode)
plus `overwrite: boolean` (default `false`).

**F-5.2** `build_from_plan` calls the existing `create_datasource_from_table` or
`create_datasource_from_query` logic (if the plan carries a `datasourceSpec`) or skips
datasource creation if `datasourceLuid` is already set.

**F-5.3** `build_from_plan` calls the Python sidecar's `/workbook/dashboard` endpoint
(new, see F-2) passing the sheet specs and dashboard layout.

**F-5.4** The result is published via the existing REST publish path and returns
`{ workbookLuid, url }`.

**F-5.5** If `build_from_plan` is called with a plan that references a `datasourceLuid`
that cannot be found on the server (REST 404), it fails with an error before any file
authoring begins.

---

### F-6 — New sidecar endpoint `/workbook/dashboard`

**F-6.1** Accepts a `DashboardWorkbookRequest` (same fields as `WorkbookRequest` plus
`dashboardLayout: "tiled_vertical" | "tiled_horizontal"`).

**F-6.2** Calls the existing `build_starter_twbx` logic, then appends a `<dashboard>`
element to the generated `.twb` XML before zipping.

**F-6.3** The `<dashboard>` element includes `<name>`, `<size>`, and `<zones>` /
`<zone>` child elements referencing each worksheet by its `name`.  The XML is schema-
valid per the Tableau `.twb` format (structurally asserted in pytest).

---

## Tool contracts (MCP semantics)

All tools follow the existing convention: zod `inputSchema`, structured `outputSchema`,
`content[0].text` for the human-readable summary, `structuredContent` for the machine-
readable payload.

### `design_dashboard`

```
Input (all modes share these top-level fields):
  mode:           "autonomous" | "interview" | "interview_followup" | "directed"
  datasourceLuid: string (required for autonomous, directed, interview_followup)
  datasourceName: string (required for autonomous, directed, interview_followup)
  audience:       AudienceEnum (required for autonomous and directed; optional for interview)
  projectName:    string (required)
  workbookName:   string? (optional; auto-generated from businessQuestion if omitted)

  -- Mode A only --
  businessQuestion: string

  -- Mode B (initial) only --
  context:          string?

  -- Mode B (followup) only --
  answers:          Record<string, string>  (questionId → answer text)

  -- Mode C only --
  directions:       string

Output (discriminated union on mode):
  mode "interview"                 → ClarifyingQuestions
  mode "autonomous" | "directed"
       | "interview_followup"      → DashboardPlan

ClarifyingQuestions shape:
  { questions: Array<{ id: string; question: string; hint?: string }> }

DashboardPlan shape:
  {
    workbookName:    string,
    datasourceLuid:  string,
    datasourceName:  string,
    projectName:     string,
    audience:        AudienceEnum,
    rationale:       string,
    sheets:          Array<SheetSpec>,
    dashboardLayout: "tiled_vertical" | "tiled_horizontal",
    datasourceSpec?: DatasourceSpec   // only present when a new datasource must be built
  }

SheetSpec shape (extends existing sheet schema):
  { title, markType, rows, cols, measures, rationale?: string }

DatasourceSpec shape:
  { filePath?: string; sql?: string; connection?: ConnectionObject;
    datasourceName: string; excelSheet?: string | number; jsonPath?: string }
```

### `build_from_plan`

```
Input:
  plan:      DashboardPlan   (the object returned by design_dashboard)
  overwrite: boolean         (default false)

Output:
  { workbookLuid: string; url: string; datasourceLuid?: string }
  (datasourceLuid present only when a new datasource was created as part of this call)
```

### `create_datasource_from_file` (new, replaces ad-hoc filePath on existing tool)

```
Input:
  filePath:       string   (local path — .csv, .json, .jsonl, .xlsx, .xls, .parquet)
  datasourceName: string
  projectName:    string
  overwrite:      boolean  (default false)
  excelSheet?:    string | number
  jsonPath?:      string

Output:
  { datasourceLuid: string; url: string }
```

Note: `create_datasource_from_table` retains its existing signature for backwards
compatibility; `csvPath` continues to work.  `create_datasource_from_file` is the new
single-file-format-agnostic entry point.

---

## Non-goals

These carry forward from the v0.1 non-goals and add new ones specific to this feature.

1. **No read/query tools.** This server does not read from Tableau Cloud; use
   `@tableau/mcp-server` for VizQL Data Service, Metadata API, and Pulse.
2. **Extract-only datasources.** Live-connection datasources remain out of scope for
   this feature (same as v0.1 ADR-004).
3. **No pixel-perfect render validation.** Dashboard layout correctness is verified
   structurally against the `.twb` XML.  What Tableau Desktop actually renders is
   verified only once at the gated live demo (same policy as criterion 7).
4. **No multi-turn conversation state in the MCP server.** The server is stateless; the
   agent (Claude, Cursor, etc.) owns conversation history and supplies the full context
   on each tool call.  The interview mode requires exactly two `design_dashboard` calls
   maximum — not an open-ended chat loop inside the server.
5. **No LLM inference inside the server.** `design_dashboard` produces plans using
   deterministic rules (audience constraints, keyword-to-mark-type heuristics, question
   templates).  The intelligence about business questions and visual direction comes from
   the AI agent that calls the tools; the tool translates structured inputs into
   structured outputs.
6. **No image/PDF export.** Publishing a `.twbx` to Cloud is the delivery mechanism.
   Generating PDFs or PNGs of the dashboard is out of scope.
7. **No map mark support beyond experimental.** Map worksheets remain experimental as
   per v0.1.
8. **No datasource metadata introspection.** The tool does not call the Tableau Metadata
   API to discover available fields.  Field names in `sheets` specs must be supplied
   explicitly; field discovery is the agent's responsibility (via `@tableau/mcp-server`).
9. **No auto-creation of projects.** A non-existent `projectName` remains an error;
   the caller must use `create_project` first.

---

## Measurable success criteria

Each criterion is binary and specifies how it is verified.  Criteria marked "headless"
can pass in CI without a live Tableau site.  Criteria marked "gated" require the live
demo.

| # | Criterion | Verification method | Env |
|---|---|---|---|
| PA-1 | `create_datasource_from_file` ingests a `.parquet`, `.xlsx`, `.json`, and `.jsonl` fixture file, calls the sidecar, and returns a non-empty `tdsx` path.  The `.tdsx` passes the existing zip+schema assertion (connection class `hyper`, one `<column>` per source column). | pytest fixture per format; assert zip structure | headless |
| PA-2 | Calling `create_datasource_from_file` with an unsupported extension (e.g. `.xml`) returns a zod validation error before the sidecar is called. | unit test asserting thrown error and zero sidecar calls | headless |
| PA-3 | Excel ingest with `excelSheet: 1` (index) and `excelSheet: "Sheet2"` (name) each loads the correct sheet.  Verified by row-count assertion against known fixture. | pytest | headless |
| DB-1 | A `.twb` produced by the sidecar's `/workbook/dashboard` endpoint contains a `<dashboard>` element with one `<zone>` per sheet in the input spec. | pytest: parse XML, assert zone count == len(sheets) | headless |
| DB-2 | `"tiled_vertical"` layout produces zones with distinct `y` offsets and equal `x` offsets.  `"tiled_horizontal"` produces zones with distinct `x` offsets and equal `y` offsets. | pytest: zone attribute assertions | headless |
| DB-3 | The `.twbx` produced by `build_from_plan` (mocked sidecar + REST) is a valid zip whose embedded `.twb` is parseable XML containing `<workbook>`, `<datasources>`, `<worksheets>`, and `<dashboard>` elements. | TS vitest, mock sidecar response | headless |
| MA-1 | `design_dashboard(mode: "autonomous", audience: "exec")` returns a `DashboardPlan` with `sheets.length <= 3` and all `markType` values in `["bar", "line", "text"]`. | unit test with fixture inputs | headless |
| MA-2 | `design_dashboard(mode: "autonomous", audience: "analyst")` returns a `DashboardPlan` with `sheets.length <= 8`. | unit test | headless |
| MA-3 | `design_dashboard(mode: "autonomous", audience: "operational")` returns a plan whose `dashboardLayout` is `"tiled_vertical"` and at least one sheet has `markType: "text"`. | unit test | headless |
| MB-1 | `design_dashboard(mode: "interview")` returns a `ClarifyingQuestions` object with `questions.length` between 3 and 7 inclusive.  No `sheets` key is present in the response. | unit test: assert shape | headless |
| MB-2 | `design_dashboard(mode: "interview_followup", answers: {...})` returns a `DashboardPlan` (same shape as autonomous output).  The plan's `rationale` is non-empty. | unit test with fixture answers | headless |
| MC-1 | `design_dashboard(mode: "directed", directions: "show me a table of top 10 customers by revenue and a bar chart of revenue by region")` returns a plan with exactly 2 sheets: one `markType: "text"` and one `markType: "bar"`. | unit test with this exact direction string | headless |
| MC-2 | `design_dashboard(mode: "directed")` called without `directions` returns a zod validation error. | unit test | headless |
| E2E-1 | `build_from_plan` called with a mocked `DashboardPlan` (all modes) calls the sidecar `/workbook/dashboard` endpoint exactly once and calls `rest.publishWorkbook` exactly once.  No `rest.publishDatasource` call is made when `plan.datasourceSpec` is absent. | TS vitest with mocked rest + sidecar | headless |
| E2E-2 | `build_from_plan` with a plan that includes a `datasourceSpec.filePath` calls `create_datasource_from_file` before the workbook build and includes the returned `datasourceLuid` in the response. | TS vitest | headless |
| E2E-3 | Full end-to-end: `design_dashboard(mode: "autonomous") → build_from_plan` against a real Dev site publishes a workbook whose Cloud URL resolves to a workbook with at least one dashboard tab.  Captured in `ACCEPTANCE.md`. | gated live demo (`npm run demo:dashboard`) | gated |
| CI-1 | All new TS tests pass on Node 22.x, 24.x, 26.x; all new Python tests pass on 3.12 and 3.13.  CI matrix green. | GitHub Actions | headless |
| CI-2 | `npm run build` compiles clean; `npm run lint` 0 errors; `uv run ruff check .` and `uv run mypy --strict .` clean after adding new modules. | CI | headless |

Total new tests: target minimum 20 new TS tests + 12 new Python tests (in addition to
the existing 28 + 18 = 46).

---

## Constraints & assumptions

| # | Item | Assumed value | Why | How to override |
|---|---|---|---|---|
| C-1 | Auth | Reuse existing `SERVER`, `SITE_NAME`, `PAT_NAME`, `PAT_VALUE` env vars. No new auth surface. | Feature builds on the same Tableau Cloud connection. | N/A — these are hard constraints. |
| C-2 | Sidecar communication | Extend existing loopback + `X-Sidecar-Token` pattern for new endpoints. | Consistent with ADR-003; no new security surface. | N/A. |
| C-3 | `design_dashboard` is deterministic | Plans are produced by rule-based logic, not LLM inference inside the server. | MCP tools must be predictable and testable without an LLM call from within the tool. The calling agent (Claude) supplies the intelligence. | If future versions embed an LLM call, the tool must document this, add a configurable model env var, and gate on `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. |
| C-4 | `openpyxl` for Excel | Assumed available; add to `sidecar/pyproject.toml` as a non-optional dependency. | Excel is a first-class requested format. `pandas` already uses openpyxl as its Excel engine. | Remove from deps if Excel support is dropped. |
| C-5 | `pyarrow` for Parquet | Already in the sidecar venv (used by pantab); treat as available. | No new dependency needed. | If pyarrow is ever removed from pantab's deps, add it explicitly. |
| C-6 | Dashboard XML format | Tableau `.twb` `<dashboard>` element with `<zones>` / `<zone>` layout (same hand-built XML approach as ADR-002). | No maintained Python Document API exists; hand-built XML is the v0.1 pattern. | If a Document API emerges, prefer it. |
| C-7 | Interview mode question count | 3–7 questions. | Fewer than 3 is not useful; more than 7 is not senior-analyst behavior and degrades UX. | Override via a `maxQuestions` parameter if needed in a future version. |
| C-8 | Dashboard tile limit | `exec` ≤ 3 sheets, `analyst` ≤ 8 sheets, `operational` ≤ 6 sheets, `mixed` ≤ 6 sheets. | Derived from Tableau best-practice density guidelines; avoids unrenderable dashboards. | Caller may override by providing `sheets` directly in a directed plan. |
| C-9 | `.twbx` version string | `TWB_VERSION = "18.1"`, `SOURCE_BUILD = "2024.1.0"` (unchanged from existing `twb_builder.py`). | Proven to open in Cloud (criterion 7 live demo). | Bump only if a new Tableau Cloud version rejects these values. |
| C-10 | Backwards compatibility | `create_datasource_from_table` retains `csvPath` / `records` parameters unchanged. | Existing agents and the demo script use this tool. | Do not remove `csvPath` / `records` in this feature. |
