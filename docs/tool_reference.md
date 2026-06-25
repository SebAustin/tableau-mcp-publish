# Tool reference

Every tool registered by `tableau-mcp-publish`, with parameters, return shape, and a sample
natural-language prompt that triggers it. Publish tools **require an explicit project**, default
`overwrite` to `false`, and return the content LUID plus a Cloud URL.

There are **14 tools** in total. The three tools added by the prompt-driven authoring feature
(`create_datasource_from_file`, `design_dashboard`, `build_from_plan`) are documented at the end
of this file.

---

## `create_datasource_from_query`

Run a SQL query against a connection, materialize the result as a Hyper extract, package it as a
`.tdsx`, and publish it to a Tableau Cloud project.

| Param | Type | Notes |
|---|---|---|
| `connection` | object | `{ type: "snowflake" \| "postgres" \| "csv", … }`. For csv: `{ type, path }`. |
| `sql` | string | SQL to run (ignored for csv). |
| `datasourceName` | string | Published datasource name. |
| `projectName` | string | Target project (must exist). |
| `overwrite` | boolean | Default `false`. |
| `maxRows` | number? | Row cap (default 1,000,000). |

**Returns:** `{ datasourceLuid, url }`

> *"Run `SELECT customer, region, revenue FROM sales ORDER BY revenue DESC LIMIT 100` on Snowflake
> and publish it as 'Top Customers' in the Sales project."*

---

## `create_datasource_from_table`

Materialize a CSV file or an array of JSON records as a Hyper extract → `.tdsx` → publish.

| Param | Type | Notes |
|---|---|---|
| `csvPath` | string? | Local CSV path. |
| `records` | object[]? | Row objects (alternative to `csvPath`). |
| `datasourceName` | string | Published datasource name. |
| `projectName` | string | Target project. |
| `overwrite` | boolean | Default `false`. |

Exactly one of `csvPath` / `records` is required. **Returns:** `{ datasourceLuid, url }`

> *"Publish this CSV as a datasource named 'Q2 Pipeline' in the Sales project."*

---

## `create_starter_workbook`

Generate a `.twbx` bound to an existing published datasource (one worksheet per sheet spec) and
publish it. Mark types `bar`, `line`, `text` are fully supported; `map` is experimental.

| Param | Type | Notes |
|---|---|---|
| `datasourceLuid` | string | LUID of the published datasource. |
| `datasourceName` | string | Its display name. |
| `workbookName` | string | Published workbook name. |
| `projectName` | string | Target project. |
| `sheets` | object[] | `{ title, markType, rows[], cols[], measures[] }` |
| `overwrite` | boolean | Default `false`. |

**Returns:** `{ workbookLuid, url }`

> *"Build a starter workbook on the Top Customers datasource with a bar chart of revenue by
> region."*

---

## `publish_datasource` / `publish_workbook`

Publish an existing local file (`.tdsx`/`.hyper` or `.twb`/`.twbx`). Chunked upload is used
automatically for files 64 MB or larger.

| Param | Type | Notes |
|---|---|---|
| `filePath` | string | Local file path. |
| `name` | string | Published name. |
| `projectName` | string | Target project. |
| `overwrite` | boolean | Default `false`. |

**Returns:** `{ datasourceLuid \| workbookLuid, url }`

---

## `list_projects` / `create_project`

`list_projects` → `{ projects: [{ id, name }] }`.
`create_project({ name, description? })` → `{ id, name }`.

> *"What projects do I have?"* / *"Create a project called Analytics Sandbox."*

---

## `set_permissions`

Grant or deny capabilities on a published datasource or workbook. Capabilities are validated
against an allowlist (e.g. `Read`, `Write`, `Connect`, `ExportData`, `ChangePermissions`, …).

| Param | Type | Notes |
|---|---|---|
| `contentType` | `"datasource" \| "workbook"` | |
| `contentId` | string | LUID. |
| `grants` | object[] | `{ granteeId, granteeType?, capability, mode: "Allow"\|"Deny" }` |

**Returns:** `{ ok, grantsApplied }`

---

## `list_content` / `refresh_datasource` / `delete_content`

- `list_content` → `{ items: [{ id, name, type, projectName?, updatedAt? }] }`
- `refresh_datasource({ datasourceId })` → `{ ok, datasourceId }`
- `delete_content({ contentType, luid, confirm })` → `{ deleted, contentType, luid }`.
  **Destructive — requires `confirm: true`.**

> *"List my published content."* / *"Refresh the Top Customers extract."* /
> *"Delete workbook <luid> — yes I'm sure."*

---

## `create_datasource_from_file`

Read a local file (CSV, JSON, JSONL, Excel `.xlsx`/`.xls`, or Parquet) into a Hyper extract and
publish it to Tableau Cloud as a `.tdsx`. File type is inferred from the extension when `fileType`
is omitted; unsupported extensions are rejected before the sidecar is called.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Datasource display name in Tableau. |
| `filePath` | string | yes | — | Absolute path to the source file on disk. |
| `fileType` | `"csv"` \| `"json"` \| `"jsonl"` \| `"xlsx"` \| `"xls"` \| `"parquet"` | no | inferred | Override inferred extension. |
| `excelSheet` | string \| integer | no | first sheet | Excel only: sheet name or 0-based index. |
| `jsonPath` | string | no | top-level array | JSON/JSONL only: single-level JSONPath selector (e.g. `$.data`). |
| `projectName` | string | yes | — | Target project (must already exist). |
| `overwrite` | boolean | no | `false` | Overwrite an existing datasource with the same name. |

**Returns:** `{ datasourceLuid: string, contentUrl: string, url: string }`

**Guardrails:**
- Unsupported file extensions (anything other than the six listed above) throw before any sidecar call.
- `projectName` must resolve to an existing project; the server never auto-creates projects.

> *"Publish `sales.parquet` as a datasource named 'Regional Sales' in the Analytics project."*

> *"Load the 'Summary' sheet from `report.xlsx` and publish it as 'Q2 Summary' in the Finance project."*

---

## `design_dashboard`

Generates a `DashboardPlan` JSON object (or a `ClarifyingQuestions` object for interview mode)
from a business question, target audience, and optional field hints. **This tool never publishes
anything.** All planning is deterministic and stateless — the same inputs always produce the same
output. Pass the returned plan to `build_from_plan` to publish.

### Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `mode` | `"autonomous"` \| `"interview"` \| `"interview_followup"` \| `"directed"` | yes | — | Planning mode (see below). |
| `audience` | `"exec"` \| `"analyst"` \| `"operational"` \| `"mixed"` | no | `"mixed"` | Required for `autonomous`, `directed`, `interview_followup`. Optional for `interview`. |
| `businessQuestion` | string | no | — | Required for `autonomous` and `interview_followup`. The question the dashboard should answer. |
| `directions` | string | no | — | Required for `directed`. Explicit sheet descriptions, e.g. `"a bar chart of revenue by region and a trend line over time"`. |
| `answers` | `Record<string, string>` | no | — | Required for `interview_followup`. Map of question IDs to user answers (from the preceding `interview` call). |
| `fieldHints` | `FieldHint[]` | no | — | Optional list of `{ name, role?, dataType? }` objects from the target datasource. When omitted the plan uses placeholder tokens. |
| `datasourceLuid` | string | no* | — | LUID of the target datasource. Required for `autonomous`, `directed`, `interview_followup`. |
| `datasourceName` | string | no* | — | Display name of the target datasource. Required for `autonomous`, `directed`, `interview_followup`. |
| `projectName` | string | no* | — | Target project name propagated into the plan. Required for `autonomous`, `directed`, `interview_followup`. |
| `workbookName` | string | no | auto-derived | Override the workbook name auto-derived from the business question. |
| `requestedLayout` | `"tiled_vertical"` \| `"tiled_horizontal"` | no | audience default | Ignored for `operational` audience (always `tiled_vertical`). |

### Modes

**`autonomous`** — Derive sheets and layout entirely from `businessQuestion` + `fieldHints`. Requires `datasourceLuid`, `datasourceName`, `projectName`.

**`interview`** — Return 3–7 clarifying questions without producing a plan. The server selects questions deterministically based on what inputs are still unknown (e.g. missing `audience` → `q_audience`; no field hints → `q_key_metric`). Does not require `datasourceLuid`.

**`interview_followup`** — Consume answers from an `interview` call and produce a full `DashboardPlan`. Requires the same fields as `autonomous` plus `answers`.

**`directed`** — Map explicit directions to concrete sheet specs. Requires `directions` (throws if omitted).

### Return shape

`interview` mode returns a `ClarifyingQuestions` object:

```json
{
  "schemaVersion": 1,
  "kind": "questions",
  "questions": [
    { "id": "q_goal", "question": "What is the primary business question?", "hint": "..." }
  ]
}
```

All other modes return a `DashboardPlan`:

```json
{
  "schemaVersion": 1,
  "kind": "plan",
  "workbookName": "Revenue by Region",
  "datasourceLuid": "abc-123",
  "datasourceName": "Regional Sales",
  "projectName": "Analytics",
  "audience": "exec",
  "rationale": "Executive view: ...",
  "dashboardLayout": "tiled_vertical",
  "sheets": [
    { "title": "KPI", "markType": "text", "cols": [], "rows": ["Region"], "measures": ["Revenue"] }
  ]
}
```

The `plan` object is wrapped inside `{ plan: <DashboardPlan|ClarifyingQuestions> }` in the tool response.

### Audience constraints

| Audience | Max sheets | Canvas | Allowed mark types | KPI required | Max measures/sheet | Max dims/sheet |
|---|---|---|---|---|---|---|
| `exec` | 3 | 1000×800 | bar, line, text | yes (index 0) | 1 | 1 |
| `analyst` | 8 | 1200×900 | bar, line, text, map | no | 4 | 3 |
| `operational` | 6 | 800×1200 | text, bar, line | yes (index 0) | 2 | 1 |
| `mixed` | 6 | 1000×900 | bar, line, text | no | 2 | 2 |

### Guardrails

- `directed` mode throws immediately if `directions` is empty or absent.
- Plans without `datasourceLuid` / `datasourceName` / `projectName` are rejected for non-interview modes.
- When `fieldHints` is omitted, sheets contain placeholder tokens (`"<measure>"`, `"<dimension>"`). `build_from_plan` will reject these — fill in real field names before calling it.

> *"Design an exec dashboard answering 'How is revenue trending by region?' — use autonomous mode."*

> *"I'm not sure what I need. Ask me clarifying questions before designing the dashboard."*

---

## `build_from_plan`

The **only side-effecting tool in the prompt-driven authoring set**. Consumes a `DashboardPlan`
produced by `design_dashboard`, builds a self-contained `.twbx` with all worksheets tiled inside a
`<dashboard>` element, and publishes it to Tableau Cloud.

The workbook embeds the `.hyper` extract directly (federated connection). This makes it
self-contained and directly renderable on Tableau Cloud. A separately-published governed datasource
(`.tdsx`) is also created as an independent artifact.

### Embedded-extract requirement

Tableau Cloud renders a workbook only when all datasource bindings can be resolved at publish time.
The only reliable path is embedding the `.hyper` extract directly in the `.twbx` (federated
connection). For this to work, `build_from_plan` requires the datasource to be materialized from a
local file in the same call.

**Two scenarios are rejected with an actionable error before any sidecar or REST work begins:**

| Scenario | Error |
|---|---|
| Plan has no `datasourceSpec` (LUID-only) | The `.hyper` file is not available on disk; embedding is impossible. Binding via `datasourceLuid` alone produces a non-rendering workbook (Tableau Cloud error 400011). |
| `datasourceSpec.sql` (query branch) | `buildDatasourceFromQuery` produces a `.tdsx` but no standalone `.hyper` path; embedding is not possible on this path. |

**The only supported path is `datasourceSpec.filePath`** — a local CSV, JSON, JSONL, Excel, or
Parquet file that the sidecar can materialize into both a `.tdsx` (published as the governed
datasource) and a `.hyper` (embedded in the workbook).

### Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `plan` | `DashboardPlan` (object) | yes | — | The object returned in `design_dashboard`'s `plan` field. Must have `schemaVersion: 1` and `kind: "plan"`. Must include `datasourceSpec.filePath`. |
| `overwrite` | boolean | no | `false` | Overwrite an existing workbook (and datasource) with the same name. |

### Returns

`{ workbookLuid: string, url: string, datasourceLuid: string }`

`datasourceLuid` is always present because a new datasource is always created (the `filePath` path
always creates one).

### Execution sequence

1. Parse and schema-validate the plan (`schemaVersion: 1` guard).
2. Reject any plan whose sheet fields still contain placeholder tokens (`<measure>`, `<dimension>`).
3. Re-validate per-sheet audience invariants (mark types, measure counts, dimension counts).
4. Reject if `plan.datasourceSpec` is absent (LUID-only) — throws actionable error.
5. Reject if `plan.datasourceSpec.filePath` is absent (SQL branch) — throws actionable error.
6. Call the sidecar `/datasource/from-file` to build the `.hyper` extract and `.tdsx`.
7. Publish the `.tdsx` to Tableau Cloud as the governed datasource; capture `datasourceLuid`.
8. Resolve the datasource's `contentUrl` via REST.
9. Call the sidecar `/workbook/dashboard` with `hyperPath` to build the embedded-extract `.twbx`
   (canvas size derived from `plan.audience`).
10. Publish the workbook and return `{ workbookLuid, url, datasourceLuid }`.

### Guardrails

- Missing `datasourceSpec` or missing `datasourceSpec.filePath` throws before any sidecar or REST call.
- Placeholder tokens (`<measure>`, `<dimension>`) in any sheet field throw before any sidecar or REST call.
- Audience invariant violations (disallowed mark type, too many measures or dimensions) throw before publishing.
- `plan.projectName` must resolve to an existing project; the server never auto-creates projects.
- `overwrite` defaults to `false`; passing `overwrite: true` is required to replace existing content.

> *"From `sales.parquet`, build and publish the dashboard plan the agent just generated."*

> *"Publish the revised plan with overwrite enabled — the workbook already exists."*
