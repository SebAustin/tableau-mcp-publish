# Tool reference

Every tool registered by `tableau-mcp-publish`, with parameters, return shape, and a sample
natural-language prompt that triggers it. Publish tools **require an explicit project**, default
`overwrite` to `false`, and return the content LUID plus a Cloud URL.

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
automatically for files over 64 MB.

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
