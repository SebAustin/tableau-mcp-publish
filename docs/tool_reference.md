# Tool reference

Every tool registered by `tableau-mcp-publish` (`registerAllTools` in `src/index.ts`), with
parameters, return shape, guardrails, and a sample natural-language prompt that triggers it.

There are **27 tools** in total, grouped into eight families below:

| Family | Count | Tools |
|---|---|---|
| [Ingest & datasources](#ingest--datasources) | 6 | `create_datasource_from_query`, `create_datasource_from_table`, `create_datasource_from_file`, `create_live_datasource`, `publish_datasource`, `publish_workbook` |
| [Design & build](#design--build) | 3 | `design_dashboard`, `build_from_plan`, `create_starter_workbook` |
| [Branding](#branding) | 1 | `validate_brand` |
| [Metadata](#metadata) | 1 | `get_datasource_fields` |
| [Scheduling & automation](#scheduling--automation) | 3 | `schedule_refresh`, `list_refresh_schedules`, `delete_refresh_schedule` |
| [Webhooks](#webhooks) | 3 | `create_webhook`, `list_webhooks`, `delete_webhook` |
| [Pulse](#pulse) | 4 | `create_pulse_definition`, `list_pulse_definitions`, `create_pulse_metric`, `delete_pulse_definition` |
| [Lifecycle & site management](#lifecycle--site-management) | 6 | `list_projects`, `create_project`, `list_content`, `refresh_datasource`, `delete_content`, `set_permissions` |

Publish tools **require an explicit project** (never auto-created), default `overwrite` to
`false`, and return the content LUID plus a Cloud URL. Destructive tools (`delete_content`,
`delete_refresh_schedule`, `delete_webhook`, `delete_pulse_definition`) require an explicit
`confirm: true` and throw otherwise — see each entry below.

---

## Ingest & datasources

### `create_datasource_from_query`

Run a SQL query against a connection, materialize the result as a Hyper extract, package it as a
`.tdsx`, and publish it to a Tableau Cloud project.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `connection` | object | yes | — | `{ type: "snowflake" \| "postgres" \| "csv", … }`. For csv: `{ type, path }`. |
| `sql` | string | yes | — | SQL to run (ignored for csv). |
| `datasourceName` | string | yes | — | Published datasource name. |
| `projectName` | string | yes | — | Target project (must exist). |
| `overwrite` | boolean | no | `false` | |
| `maxRows` | number | no | 1,000,000 | Row cap. |

**Returns:** `{ datasourceLuid, url }`

**Guardrails:** `projectName` must resolve to an existing project.

> *"Run `SELECT customer, region, revenue FROM sales ORDER BY revenue DESC LIMIT 100` on Snowflake
> and publish it as 'Top Customers' in the Sales project."*

---

### `create_datasource_from_table`

Materialize a CSV file or an array of JSON records as a Hyper extract → `.tdsx` → publish.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `csvPath` | string | no* | — | Local CSV path. |
| `records` | object[] | no* | — | Row objects (alternative to `csvPath`). |
| `datasourceName` | string | yes | — | Published datasource name. |
| `projectName` | string | yes | — | Target project. |
| `overwrite` | boolean | no | `false` | |

\* Exactly one of `csvPath` / `records` is required — the tool throws before any sidecar call
otherwise. **Returns:** `{ datasourceLuid, url }`

> *"Publish this CSV as a datasource named 'Q2 Pipeline' in the Sales project."*

---

### `create_datasource_from_file`

Read a local file (CSV, JSON, JSONL, Excel `.xlsx`/`.xls`, or Parquet) into a Hyper extract and
publish it to Tableau Cloud as a `.tdsx`. File type is inferred from the extension when `fileType`
is omitted; unsupported extensions are rejected before the sidecar is called. Numeric- and
date-string columns are coerced to real types by the sidecar's ingest path (see
`hyper_builder.py`), so downstream tools like `get_datasource_fields` and Pulse pre-flight see
real `NUMBER`/`DATE` types instead of strings.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Datasource display name in Tableau. |
| `filePath` | string | yes | — | Absolute path to the source file on disk. |
| `fileType` | `"csv"` \| `"json"` \| `"jsonl"` \| `"xlsx"` \| `"xls"` \| `"parquet"` | no | inferred | Override inferred extension. |
| `excelSheet` | string \| integer | no | first sheet | Excel only: sheet name or 0-based index. |
| `jsonPath` | string | no | top-level array | JSON/JSONL only: single-level JSONPath selector (e.g. `$.data`). |
| `projectName` | string | yes | — | Target project (must already exist). |
| `overwrite` | boolean | no | `false` | Overwrite an existing datasource with the same name. |

**Returns:** `{ datasourceLuid, contentUrl, url }`

**Guardrails:**
- Unsupported file extensions (anything other than the six listed above) throw before any sidecar call.
- `projectName` must resolve to an existing project; the server never auto-creates projects.

> *"Publish `sales.parquet` as a datasource named 'Regional Sales' in the Analytics project."*

---

### `create_live_datasource`

Publish a **LIVE** Cloud connection (Snowflake or Presto/Trino) — no extract, no locally
materialized data. The sidecar builds a `.tds` that carries only connection topology
(server/warehouse/schema/database/table); credentials are embedded **separately at publish time**
via Tableau's `<connectionCredentials>` element (`src/rest/credentials.ts`) and are never written
into the `.tds` file or logged.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Datasource display name in Tableau. |
| `projectName` | string | yes | — | Target project (must already exist). |
| `connection.type` | `"snowflake"` \| `"presto"` | yes | — | Discriminates the rest of `connection`. |
| `connection.server` | string | yes | — | Account/coordinator host. |
| `connection.schema` | string | yes | — | Schema name. |
| `connection.table` | string | yes | — | Table this datasource binds to. |
| `connection.warehouse` / `.dbname` | string | yes (snowflake) | — | Snowflake-only. |
| `connection.authentication` | string | no | `"username-password"` | `"username-password"` or `"oauth"`. Key-pair auth throws (see guardrails). |
| `connection.role` | string | no | — | Snowflake-only optional role. |
| `connection.port` | number | no | 8080 | Presto-only. |
| `connection.catalog` | string | yes (presto) | — | Presto-only. |
| `connection.ssl` | boolean | no | `true` | Presto-only. |
| `connection.useRemoteQueryAgent` | boolean | no | `true` | Presto-only — assume Tableau Bridge is needed unless the endpoint is internet-reachable and allowlisted. |
| `credentials.username` / `.password` | string | yes | — | Source from environment; never hardcode or log (see caveats). |
| `overwrite` | boolean | no | `false` | |

**Returns:** `{ datasourceLuid, url, note }` — `note` always restates the connectivity caveat for
the connection type used.

**Guardrails / honest caveats:**
- **Snowflake key-pair authentication is IMPOSSIBLE over REST** — Tableau's Publish Datasource API
  only supports embedding username/password or OAuth credentials. The tool throws a clear,
  actionable error (`"…is not REST-publishable…"`) rather than silently emitting XML that will
  never authenticate; configure key-pair auth in Tableau Desktop and publish from there instead.
- **Presto/Trino is generally Tableau-Bridge-dependent on Cloud** — Cloud usually cannot reach a
  private-network Presto cluster directly. `useRemoteQueryAgent` defaults to `true`; set it `false`
  only when the endpoint is internet-reachable and allowlisted.
- **VERIFY-LIVE:** the exact XML attribute spelling for both connector classes
  (`sidecar/tds_builder.py`'s `SNOWFLAKE_ATTRS`/`PRESTO_ATTRS`) is this project's best-documented
  mapping and has not yet been confirmed against a Desktop-exported `.tds` — treat as best-effort
  until verified against a live site.
- Credentials never touch the `.tds` file, logs, or error messages (asserted in `tests/credentials.test.ts`).

> *"Publish a live Snowflake connection to the ORDERS table in the SALES schema of the ANALYTICS
> database, warehouse COMPUTE_WH, as 'Live Orders' in the Sales project — use username/password
> auth from my environment."*

---

### `publish_datasource` / `publish_workbook`

Publish an existing local file (`.tdsx`/`.hyper` or `.twb`/`.twbx`). Chunked upload is used
automatically for files 64 MB or larger.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `filePath` | string | yes | — | Local file path. |
| `name` | string | yes | — | Published name. |
| `projectName` | string | yes | — | Target project. |
| `overwrite` | boolean | no | `false` | |

**Returns:** `{ datasourceLuid \| workbookLuid, url }`

---

## Design & build

### `design_dashboard`

Generates a **`DashboardProposal`** (`kind: "proposal"`) once enough context is known, or a
**`ClarifyingQuestions`** object (`kind: "questions"`) in interview mode, from a business question,
target audience (or named persona), and optional field hints. **This tool never builds or
publishes anything** — building is always a separate, explicit step. All planning is deterministic
and stateless: the same inputs always produce the same output.

### Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `mode` | `"autonomous"` \| `"interview"` \| `"interview_followup"` \| `"directed"` | yes | — | Planning mode (see below). |
| `audience` | `"exec"` \| `"analyst"` \| `"operational"` \| `"mixed"` | no | `"mixed"` (or persona base) | Required for `autonomous`/`directed`/`interview_followup` unless `persona` is supplied. |
| `persona` | string | no | — | Named persona from `brand.yaml` (e.g. `"ceo"`, `"cto"`, `"analyst"`). Resolves the persona's base audience (used when `audience` is omitted) and applies its overrides (`maxSheets`, `chartDeny`, `kpiEmphasis`, `preferredArtifact`, `tone`). An explicit `audience` always wins over the persona's base. |
| `brandPath` | string | no | repo-root `brand.yaml` | Only used when `persona` is supplied. |
| `businessQuestion` | string | no | — | Required for `autonomous`/`interview_followup`. |
| `directions` | string | no | — | Required for `directed` — throws if empty/absent. |
| `answers` | `Record<string, string>` | no | — | Required for `interview_followup` — map of question IDs to answers from a preceding `interview` call. |
| `fieldHints` | `FieldHint[]` | no | — | `{ name, role?, dataType? }[]` from the target datasource. Omitted → placeholder tokens in the plan. Get real ones from `get_datasource_fields`. |
| `datasourceLuid` / `datasourceName` / `projectName` | string | yes* | — | Required for `autonomous`/`directed`/`interview_followup`. |
| `workbookName` | string | no | auto-derived | |
| `requestedLayout` | `"tiled_vertical"` \| `"tiled_horizontal"` | no | audience default | Ignored for `operational` (always `tiled_vertical`). |

### Modes

**`autonomous`** — Derive sheets and layout entirely from `businessQuestion` + `fieldHints`.

**`interview`** — Return 3–7 clarifying questions without producing a plan/proposal. Selected
deterministically based on which inputs are unknown. Does not require `datasourceLuid`.

**`interview_followup`** — Consume `answers` from a preceding `interview` call and produce a full proposal.

**`directed`** — Map explicit `directions` to concrete sheet specs.

### The propose→confirm loop (stateless — the agent drives)

1. Agent calls `design_dashboard` (`autonomous`/`directed`/`interview_followup`) → server returns
   `kind: "proposal"`.
2. Agent presents the proposal to the user: `summary`, `kpiStrip`, `views`, `layoutSummary`,
   `storyOutline` (if present).
3. **User says "change X"** → agent re-calls `design_dashboard` in `directed` mode with updated
   `directions` for a fresh proposal — no session state is stored server-side.
4. **User says "confirm"** → agent calls `build_from_plan` with `proposal.plan` **verbatim** (do
   not modify it first).

### Return shape

`interview` mode:

```json
{ "schemaVersion": 1, "kind": "questions", "questions": [{ "id": "q_goal", "question": "…", "hint": "…" }] }
```

All other modes return a `DashboardProposal` (wrapped as `{ result: <proposal> }` in the tool
response):

```json
{
  "schemaVersion": 1,
  "kind": "proposal",
  "workbookName": "Revenue by Region",
  "audience": "exec",
  "datasourceName": "Regional Sales",
  "projectName": "Analytics",
  "summary": "Exec dashboard on \"Regional Sales\" featuring a KPI band of 3 metrics …",
  "kpiStrip": [{ "label": "Sales", "primaryMeasure": "Sales", "deltaMeasure": "Sales Difference", "direction": "up_good" }],
  "views": [{ "title": "Sales by Category", "chartType": "bar", "encodingSummary": "bar: Sales by Category, colored by Segment", "fields": ["Sales", "Category", "Segment"] }],
  "layoutSummary": "KPI band of 3 tiles … above a 2-chart row (…).",
  "storyOutline": ["Headline KPIs", "Profit", "Sales by Category"],
  "openQuestions": ["…"],
  "plan": { "schemaVersion": 1, "kind": "plan", "…": "…" }
}
```

`proposal.plan` is the input `DashboardPlan` embedded verbatim — pass it unchanged to
`build_from_plan` on user confirmation.

### Audience constraints

| Audience | Max sheets | Canvas | Allowed mark types | KPI required | Max measures/sheet | Max dims/sheet |
|---|---|---|---|---|---|---|
| `exec` | 3 | 1000×800 | bar, line, text, map_filled (no bare scatter) | yes (index 0) | 1 | 1 |
| `analyst` | 8 | 1200×900 | bar, line, text, map, scatter, map_filled | no | 4 | 3 |
| `operational` | 6 | 800×1200 | text, bar, line | yes (index 0) | 2 | 1 |
| `mixed` | 6 | 1000×900 | bar, line, text | no | 2 | 2 |

A persona's `maxSheets`/`chartDeny`/`kpiEmphasis` overrides apply on top of these base constraints
(BI_DESIGN §9.1) — e.g. `persona: "ceo"` caps `maxSheets` at 3 and denies no extra chart types by
default; `persona: "client"` denies `scatter`/`map`.

### Guardrails

- `directed` mode throws immediately if `directions` is empty or absent.
- Non-interview modes without `datasourceLuid`/`datasourceName`/`projectName` throw.
- Unknown `persona` names throw, listing every available persona from `brand.yaml`.
- When `fieldHints` is omitted, sheets contain placeholder tokens (`"<measure>"`, `"<dimension>"`)
  and the proposal's `openQuestions` calls this out; `build_from_plan` rejects these tokens.
- A persona whose `preferredArtifact` is `"pulse"` gets an honest `openQuestions` note — Pulse
  metric generation is a separate tool family (below), not something `design_dashboard` produces.
  `preferredArtifact: "story"` is fully supported (see `storyArc`/`storyOutline`).

> *"Design an exec dashboard for the 'ceo' persona answering 'How is revenue trending by
> region?' — use autonomous mode."*

> *"I'm not sure what I need. Ask me clarifying questions before designing the dashboard."*

---

### `build_from_plan`

The **only side-effecting tool in the design/build family**. Consumes a `DashboardPlan` (from
`proposal.plan`), builds a self-contained `.twbx` with all worksheets tiled inside a `<dashboard>`
element (plus an optional `<dashboard type='storyboard'>` when the plan carries a `storyArc`), and
publishes it to Tableau Cloud.

The workbook embeds the `.hyper` extract directly (federated connection), so it renders on Tableau
Cloud without depending on a separately published datasource binding. A governed `.tdsx` datasource
is also published as an independent artifact.

### Embedded-extract requirement

Tableau Cloud renders a workbook only when all datasource bindings can be resolved at publish time.
The only reliable path is embedding the `.hyper` extract directly (federated connection), which
requires the datasource to be materialized from a local file in the *same* `build_from_plan` call.

| Scenario | Error |
|---|---|
| Plan has no `datasourceSpec` (LUID-only) | Throws before any sidecar/REST work — binding via `datasourceLuid` alone produces a non-rendering workbook (Tableau Cloud error 400011). |
| `datasourceSpec.sql` (query branch) | Throws — the query path produces a `.tdsx` but no standalone `.hyper` to embed. |

**The only supported path is `datasourceSpec.filePath`** — a local CSV, JSON, JSONL, Excel, or
Parquet file.

### Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `plan` | object | yes | — | The `DashboardPlan` from `proposal.plan`. Must have `schemaVersion: 1`, `kind: "plan"`, and `datasourceSpec.filePath`. |
| `overwrite` | boolean | no | `false` | Overwrite an existing workbook (and datasource) with the same name. |
| `brandPath` | string | no | repo-root `brand.yaml` | Applies branding when `plan.personaName`/`plan.brandName` is set (i.e. the plan came from a persona-resolved `design_dashboard` call) or when explicitly passed. Branding is skipped entirely when none of the three are present. |

### Returns

`{ workbookLuid, url, datasourceLuid }` — `datasourceLuid` is always present (the `filePath` path
always creates a new datasource).

### Guardrails

- Missing `datasourceSpec` or `datasourceSpec.filePath` throws before any sidecar/REST call.
- Placeholder tokens (`<measure>`/`<dimension>`) in any sheet field throw before any work begins.
- Audience invariants (mark type, measure count, dimension count) are re-validated at build time —
  defense in depth against a hand-edited plan.
- `storyArc[].capturedSheet` references are re-validated against the plan's own sheet titles
  before calling the sidecar (which validates a third time against the actual built workbook).
- `plan.projectName` must resolve to an existing project.
- `overwrite` defaults to `false`.

> *"Publish the confirmed proposal — build and publish that dashboard now."*

### Worked example — rewriting story captions before confirming

`design_dashboard`'s deterministic `storyArc` captions (`docs/feature-prompt-authoring/
BI_DESIGN.md` §9.2) are a template FLOOR, not the intended final copy — the calling agent is
expected to rewrite them with real narrative before calling `build_from_plan`. `capturedSheet`
values must stay untouched (they must remain one of `plan.sheets[].title`); only `caption` text
changes:

```jsonc
// 1. design_dashboard (businessQuestion: "Tell the story of how sales and profit
//    are performing across categories and states") returns proposal.plan.storyArc:
[
  { "caption": "The headline: sales and profit … — start with Sales at a glance.", "capturedSheet": "Sales" },
  { "caption": "Profit: watch this lever alongside Sales.", "capturedSheet": "Profit" },
  { "caption": "Category drives the mix — where Sales concentrates.", "capturedSheet": "Sales by Category" },
  { "caption": "The geography: where Sales shows up on the map.", "capturedSheet": "Sales by State" }
]

// 2. The agent rewrites `caption` only, using data/context it has and the
//    planner never sees — capturedSheet values are unchanged:
[
  { "caption": "Q4 finished strong: Sales are up 14% YoY, led by the West.", "capturedSheet": "Sales" },
  { "caption": "Profit held pace with Sales — margin discipline is intact this quarter.", "capturedSheet": "Profit" },
  { "caption": "Technology is the standout category — nearly a third of total Sales.", "capturedSheet": "Sales by Category" },
  { "caption": "California and Texas anchor the map; the Midwest is the clearest whitespace.", "capturedSheet": "Sales by State" }
]

// 3. build_from_plan(plan) with the rewritten plan — assertStoryArcCapturedSheetsExist
//    still passes (capturedSheet is untouched) and the storyboard builds from the
//    new captions.
```

Dropping or reordering points is also fine — every remaining `capturedSheet` just has to name
an existing `plan.sheets[].title`. Inventing a `capturedSheet` that doesn't exist throws before
any sidecar/REST call (the guardrail above).

---

### `create_starter_workbook`

Generate a `.twbx` bound to an *existing published* datasource (one worksheet per sheet spec, via
`sqlproxy`/`repository-location` — no embedded extract) and publish it. Mark types `bar`, `line`,
`text` are fully supported; `map` is experimental.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `datasourceLuid` | string | yes | — | LUID of the published datasource. |
| `datasourceName` | string | yes | — | Its display name. |
| `workbookName` | string | yes | — | Published workbook name. |
| `projectName` | string | yes | — | Target project. |
| `sheets` | object[] | yes | — | `{ title, markType, rows[], cols[], measures[] }`. |
| `overwrite` | boolean | no | `false` | |

**Returns:** `{ workbookLuid, url }`

> *"Build a starter workbook on the Top Customers datasource with a bar chart of revenue by
> region."*

---

## Branding

### `validate_brand`

Loads and validates the brand kit file (`brand.yaml`) that drives palette, typography, number
formats, and named personas used by `design_dashboard`. **Never throws** on an invalid or missing
file — always returns a result so problems can be surfaced to the user instead of failing the call.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `path` | string | no | repo-root `brand.yaml` | Optional override path. |

**Returns:** `{ valid, warnings: string[], personas: string[], summary }`

**Guardrails:** an absent file returns `valid: true` with a warning (built-in defaults apply — see
`DEFAULT_BRAND` in `src/branding/schema.ts`); a malformed/schema-invalid file returns
`valid: false` with the field-level issues in `warnings`, never an exception.

> *"Validate my brand.yaml and list the personas it defines."*

---

## Metadata

### `get_datasource_fields`

Fetch the **real** field names, captions, data types, and default aggregations for a published
datasource via the VizQL Data Service (`read-metadata`). Use this before `design_dashboard` so its
`fieldHints` reference fields that actually exist, and as a pre-flight check before
`create_pulse_definition`.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `datasourceLuid` | string | yes | — | LUID of a published datasource. |

**Returns:** `{ fields: [{ name, caption?, dataType?, defaultAggregation? }], count }`

**Guardrails:** a 404 is rewrapped with a hint to confirm the LUID and that the datasource is
published (not local/embedded-only); a "feature disabled" response is rewrapped with a hint that
VDS may not be enabled for the site. GET is safely retried on 429/502/503/504.

> *"What are the real field names on the Regional Sales datasource?"*

---

## Scheduling & automation

Tableau Cloud has **no classic Server-style shared schedules** over REST — each task carries its
own embedded frequency + interval schedule. These tools are distinct from `refresh_datasource`
(below), which triggers a single immediate refresh with no recurring schedule.

### `schedule_refresh`

Create a recurring extract-refresh task on Tableau Cloud for a published datasource or workbook.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `targetType` | `"datasource"` \| `"workbook"` | yes | — | |
| `targetId` | string | yes | — | LUID of the target. |
| `type` | `"FullRefresh"` \| `"IncrementalRefresh"` | no | `"FullRefresh"` | `IncrementalRefresh`'s exact token spelling is VERIFY-LIVE — prefer `FullRefresh` until confirmed. |
| `frequency` | `"Hourly"` \| `"Daily"` \| `"Weekly"` \| `"Monthly"` | yes | — | |
| `frequencyDetails` | object | yes | — | `{ start: "HH:MM:SS", end?, intervals[] }`. Hourly needs ≥1 interval with `hours`; Weekly needs ≥1 with `weekDay`; Monthly needs exactly one with `monthDay`; Daily needs none. |

**Returns:** `{ taskId, frequency?, nextRunAt?, note }`

**Guardrails / honest caveat (always included in `note`, not just on failure — it can't be
reliably detected from the API response):** Tableau Cloud can only **execute** this schedule for a
connection that is cloud-reachable with embedded credentials (e.g. Snowflake via
`create_live_datasource`). A datasource published from a local file (CSV/Excel/local Hyper
extract) accepts the schedule-creation call successfully, but Cloud **cannot** refresh a file-based
extract without **Tableau Bridge** — the task may be created while every run fails. Use the
[local-file cron design-around](#local-file-refresh-automation) for file-based datasources instead.

> *"Schedule a daily 3am refresh of the Live Orders datasource."*

---

### `list_refresh_schedules`

List every extract-refresh task on the site.

**Returns:** `{ schedules: [{ taskId, type, targetKind?, targetId?, frequency?, nextRunAt? }] }`

> *"List every scheduled refresh on this site."*

---

### `delete_refresh_schedule`

Delete a recurring extract-refresh task by `taskId`. **Destructive and irreversible.**

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `taskId` | string | yes | — | From `schedule_refresh` / `list_refresh_schedules`. |
| `confirm` | boolean | no | `false` | Must be explicitly `true`; deletion is refused otherwise. |

**Returns:** `{ deleted, taskId }`

> *"Delete refresh schedule task-abc123 — yes I'm sure."*

---

## Webhooks

### `create_webhook`

Create a site webhook that POSTs to an HTTPS destination when a Tableau event fires.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Human-readable webhook name. |
| `event` | enum | yes | — | One of `Datasource{RefreshStarted,RefreshSucceeded,RefreshFailed,Created,Updated,Deleted}` or the `Workbook*` equivalents. |
| `url` | string | yes | — | Destination URL. **Must be HTTPS** — the tool rejects `http://` before any REST call. |

**Returns:** `{ webhookId, name, event }`

**Guardrails:** requires site-administrator privileges on the signed-in PAT — a 403 is rewrapped
with that explanation rather than a bare "Forbidden". The destination receives
`{ resource, event_type, resource_name, site_luid, resource_luid, created_at }`.

> *"Create a webhook that posts to https://example.com/hooks/tableau when a datasource refresh
> fails."*

---

### `list_webhooks`

List every webhook configured on the site.

**Returns:** `{ webhooks: [{ webhookId, name, event, url? }] }`

---

### `delete_webhook`

Delete a webhook by `webhookId`. **Destructive and irreversible.**

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `webhookId` | string | yes | — | From `create_webhook` / `list_webhooks`. |
| `confirm` | boolean | no | `false` | Must be explicitly `true`. |

**Returns:** `{ deleted, webhookId }`

---

## Pulse

**Tableau Pulse is Cloud-only** (not available on Tableau Server) and must be enabled for the site
under Site Settings → Pulse. This note is always surfaced on `create_pulse_definition`'s output —
Pulse enablement/permission failures can't be reliably distinguished from other 404s.

> **Honest status (2026-07-17):** all four tools are code-complete against Tableau's official
> `pulse-api-utilities` reference payload shape, with a VDS-backed pre-flight check. Live proof is
> **blocked**: `POST /api/-/pulse/definitions` returns a bare 400 against the real API — the
> `basic_specification` internals are specified-by-example only in Tableau's own reference repo
> (which clones, never constructs, that block from scratch), so the exact wire shape is not yet
> locked against a live fixture. Every enum beyond the few tokens the research brief explicitly
> confirms is marked VERIFY-LIVE in `src/rest/pulse.ts`. Treat these tools as best-effort until a
> definition is created once in the Pulse UI, fetched back via GET, and used to correct the client.
> **2026-07-20 addendum:** an external agent-skill cross-check (`docs/adr/0014-external-skill-analysis.md`)
> added purely additive, VERIFY-LIVE enum/field widening (`currencyCode`, `insightSettings`,
> `rowLevelIdField`/`rowLevelNameField`/`rowLevelEntityNames`, fiscal granularity/comparison
> tokens) — this is schema-readiness only and does **not** close the live-blocked 400 above.

### `create_pulse_definition`

Create a Tableau Pulse metric definition.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Display name for the definition. |
| `datasourceLuid` | string | yes | — | LUID of a single **published** datasource with a real date dimension. |
| `measure.field` | string | yes | — | Measure field name/caption, e.g. `"Sales"`. |
| `measure.aggregation` | `"SUM"` \| `"AVERAGE"` \| `"MEDIAN"` \| `"MAX"` \| `"MIN"` \| `"COUNT"` \| `"COUNT_DISTINCT"` | yes | — | |
| `timeDimension.field` | string | yes | — | Date-like field name/caption, e.g. `"Order Date"`. |
| `filters` | object[] | no | `[]` | Specification filters — shape VERIFY-LIVE. |
| `allowedDimensions` | string[] | no | `[]` | Dimensions Pulse may use for insight breakdowns. |
| `numberFormat` | `"NUMBER"` \| `"CURRENCY"` \| `"PERCENT"` | no | `"NUMBER"` | |
| `sentiment` | `"NONE"` \| `"UP_IS_GOOD"` \| `"DOWN_IS_GOOD"` | no | `"NONE"` | |
| `isRunningTotal` | boolean | no | `false` | |
| `currencyCode` | `"USD"` \| `"EUR"` \| `"GBP"` \| `"JPY"` \| `"UNSPECIFIED"` | no | — | VERIFY-LIVE (schema-readiness only — see honest status note). Emitted as `representation_options.currency_code` only when set. |
| `insightSettings` | `{ type, disabled? }[]` | no | `[]` | VERIFY-LIVE. `type` is one of the 8 `INSIGHT_TYPE_*` values (`CURRENT_TREND`, `NEW_TREND`, `TOP_DRIVERS`, `TOP_DETRACTORS`, `BOTTOM_CONTRIBUTORS`, `RISKY_MONOPOLY`, `UNUSUAL_CHANGE`, `RECORD_LEVEL_OUTLIERS`). Emitted as `insights_options.settings` entries. |
| `rowLevelIdField` / `rowLevelNameField` | string | no | — | VERIFY-LIVE. Identifier/label columns enabling `INSIGHT_TYPE_RECORD_LEVEL_OUTLIERS`. |
| `rowLevelEntityNames` | `{ singular, plural }` | no | — | VERIFY-LIVE. Emitted as `specification.row_level_entity_names.{singular_noun,plural_noun}`. |
| `skipPreflight` | boolean | no | `false` | Skip the VDS pre-flight field check (e.g. when VDS itself is known unavailable). |

**Returns:** `{ definitionId, name, note }`

**Guardrails:**
- Runs a VDS-backed pre-flight check before calling Pulse: verifies `measure.field` and
  `timeDimension.field` exist on the datasource (throws, listing every available field, on a
  mismatch) and that the time dimension's VDS `dataType` looks date-like; warns (without blocking)
  when the measure's VDS `defaultAggregation` disagrees with the requested aggregation. If VDS
  itself fails, pre-flight is automatically skipped with a warning rather than blocking creation.
- Requires `tableau:insight_definitions:create`/`update` PAT scope, and write+publish permission
  on the target datasource.
- See the honest status note above — live creation currently returns a 400.

> *"Create a Pulse metric definition tracking monthly Sales, aggregated as SUM, using Order Date
> as the time dimension."*

---

### `list_pulse_definitions`

List every Pulse metric definition on the site.

**Returns:** `{ definitions: [{ definitionId, name, datasourceLuid? }], count }`

**Guardrails:** requires `tableau:insight_definitions_metrics:read` PAT scope.

---

### `create_pulse_metric`

Create a Pulse metric instance from an existing definition.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `definitionId` | string | yes | — | From `create_pulse_definition` / `list_pulse_definitions`. |
| `filters` | object[] | no | `[]` | Shape VERIFY-LIVE. |
| `granularity` | enum | no | `"GRANULARITY_BY_MONTH"` | Only this default is confirmed against Tableau's official reference; other values are VERIFY-LIVE. |
| `range` | enum | no | `"RANGE_LAST_COMPLETE"` | Same caveat. |
| `comparison` | enum | no | `"TIME_COMPARISON_PREVIOUS_PERIOD"` | Same caveat. |

**Returns:** `{ metricId }`

**Guardrails:** requires `tableau:insight_metrics:create` PAT scope. Prefer the defaults until the
live fixture confirms other enum values.

---

### `delete_pulse_definition`

Delete a Pulse metric definition by id. **Destructive and irreversible — also removes any metrics
built on it.**

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `definitionId` | string | yes | — | |
| `confirm` | boolean | no | `false` | Must be explicitly `true`. |

**Returns:** `{ deleted, definitionId }`

**Guardrails:** the exact delete response shape is unconfirmed against a live site (VERIFY-LIVE) —
per Tableau's own guidance, create one definition in the UI and GET it back to lock a fixture
before relying on this in production.

---

## Lifecycle & site management

### `list_projects` / `create_project`

`list_projects` → `{ projects: [{ id, name }] }`.
`create_project({ name, description? })` → `{ id, name }`.

> *"What projects do I have?"* / *"Create a project called Analytics Sandbox."*

---

### `list_content` / `refresh_datasource` / `delete_content`

- `list_content` → `{ items: [{ id, name, type, projectName?, updatedAt? }] }`
- `refresh_datasource({ datasourceId })` → `{ ok, datasourceId }` — a **single immediate** refresh,
  not a recurring schedule (see `schedule_refresh` above for recurring).
- `delete_content({ contentType, luid, confirm })` → `{ deleted, contentType, luid }`.
  **Destructive — requires `confirm: true`**, and additionally refuses to delete content that
  lives in the `Default` project (governance guardrail — checked via `list_content` before the
  delete call).

> *"List my published content."* / *"Refresh the Top Customers extract."* /
> *"Delete workbook `<luid>` — yes I'm sure."*

---

### `set_permissions`

Grant or deny capabilities on a published datasource or workbook.

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `contentType` | `"datasource"` \| `"workbook"` | yes | — | |
| `contentId` | string | yes | — | LUID. |
| `grants` | object[] | yes | — | `{ granteeId, granteeType?: "user"\|"group", capability, mode: "Allow"\|"Deny" }`. |
| `confirmElevated` | boolean | no | `false` | Required when any grant uses an elevated capability. |

**Returns:** `{ ok, grantsApplied }`

**Guardrails:**
- Capabilities are validated against an 18-entry allowlist (`Read`, `Write`, `Connect`,
  `ExportData`, `ChangePermissions`, `Delete`, `ProjectLeader`, `ChangeHierarchy`, …) — an unknown
  capability throws, listing the allowlist.
- Elevated capabilities (`ChangePermissions`, `Delete`, `ProjectLeader`, `ChangeHierarchy`) require
  `confirmElevated: true`; otherwise the call throws before any REST work.

> *"Grant Read access on the Top Customers datasource to the Sales group."*
