# Relationship to the official Tableau MCP server

`tableau-mcp-publish` is a **companion** to [`tableau/tableau-mcp`](https://github.com/tableau/tableau-mcp),
not a fork or a competitor. Together they cover the full agentic Tableau loop: the official server
**reads and queries**; this one **builds Hyper extracts and publishes to Cloud**.

## What changed in the official server (v2.18.x)

The "official server = read-only, zero overlap" framing was true at launch but is now **partly
outdated**. `tableau/tableau-mcp` v2.18.x (released 2026-06-24) ships two distinct capability
sets:

1. **Admin-gated mutation tools** (web variant, ~35 tools total) — `delete-datasource`,
   `delete-workbook`, `delete-extract-refresh-task`, `update-cloud-extract-refresh-task`. These are
   behind a site-admin gate (`ADMIN_TOOLS_ENABLED` feature flag) plus a confirmation token
   (`src/tools/web/adminGate.ts`).

2. **Desktop-local workbook-authoring loop** (desktop variant, ~6 tools) — `get-workbook-xml`,
   `apply-workbook` (`readOnlyHint: false`, `destructiveHint: true`), `list-worksheets`,
   `list-dashboards`. This path targets a **locally running Tableau Desktop instance** via a local
   executor (`list-instances` → session → `apply-workbook`). It does not build a `.hyper` extract,
   package a `.tdsx`/`.twbx`, or publish to Tableau Cloud.

**Our niche remains distinct.** `tableau-mcp-publish` is the headless, server-side path: SQL or
file → governed Hyper extract → published Cloud datasource + workbook, with no Desktop required.
That path has no equivalent in the official server.

The datasource and the dashboard workbook are **two independent artifacts**. The `.tdsx` is
published to Cloud as a governed, reusable datasource. The dashboard workbook (`.twbx`) produced
by `build_from_plan` embeds its own copy of the `.hyper` extract (federated connection), making it
self-contained and directly renderable on Tableau Cloud without the workbook needing to bind to the
published datasource at render time. Both artifacts share the same source extract but are published
separately.

## Capability comparison

| Capability | Official `@tableau/mcp-server` (v2.18.x) | `tableau-mcp-publish` (this, 27 tools) |
|---|---|---|
| Query a published datasource (VizQL Data Service) | ✅ web variant | — (use the official server) |
| Read column metadata (Metadata API) | ✅ web variant | — |
| Pulse metrics | ✅ web variant | — |
| Edit a workbook in Tableau Desktop | ✅ desktop variant (`apply-workbook`) | — |
| Delete datasource / workbook (site-admin gate) | ✅ web variant, admin-gated | ✅ `delete_content` (no site-admin required, used within the authoring flow) |
| Trigger / manage extract refreshes (admin gate) | ✅ web variant, admin-gated | ✅ `refresh_datasource` (no site-admin required) |
| List projects | ✅ web variant | ✅ `list_projects` |
| Create a published datasource from SQL | — | ✅ `create_datasource_from_query` |
| Create a published datasource from a CSV / records | — | ✅ `create_datasource_from_table` |
| Create a published datasource from a file (CSV / JSON / JSONL / Excel / Parquet) | — | ✅ `create_datasource_from_file` |
| Build a `.hyper` extract (headless, server-side) | — | ✅ Hyper API sidecar |
| Generate a workbook (`.twb`/`.twbx`) and publish to Cloud | — | ✅ `create_starter_workbook` (sqlproxy binding) |
| Prompt-driven dashboard planning | — | ✅ `design_dashboard` |
| Publish a dashboard plan to Cloud (self-contained `.twbx`, embedded extract) | — | ✅ `build_from_plan` |
| Publish an existing `.tdsx` / `.hyper` / `.twb` / `.twbx` file | — | ✅ `publish_datasource` / `publish_workbook` |
| Create projects / manage permissions | — | ✅ `create_project`, `set_permissions` |

## Designed to run side by side

- **Same auth contract.** Both servers accept identical env vars (`SERVER`, `SITE_NAME`,
  `PAT_NAME`, `PAT_VALUE`), so a single MCP client config runs both on one Personal Access Token.
  The official server also supports `pat`, `uat`, `direct-trust`, and `oauth` auth modes
  (`src/config.ts`); the shared PAT env-var path works with either server.
- **No read/query overlap.** `tableau-mcp-publish` implements zero read/query tools by design.
  Metadata discovery (e.g. listing column names and types from a published datasource) is best
  done with the official server, then the results passed as `fieldHints` into `design_dashboard`.
- **Composable.** An agent reads metadata with the official server, then authors and publishes with
  this one. See issue #3 in the repo.

## Transport and runtime notes

- The official server supports **stdio and HTTP** transports (`src/transports.ts`); this server
  is stdio-only.
- Both servers share a Node floor of **≥ 22.7.5** (`package.json` `engines`), so they install under one runtime.
- Both servers use `@modelcontextprotocol/sdk` and follow the same MCP tool conventions.

## Broader ecosystem context

Two additional Tableau repos are worth knowing about; neither overlaps with `tableau-mcp-publish`:

- **`tableau/tableau_langchain`** (`langchain-tableau` on PyPI) — LangChain/LangGraph integration
  that queries Tableau data via the VizQL Data Service. Read/query only; different framework
  entirely.
- **`tableau/VizQL-Data-Service`** — the OpenAPI + SDK for the read/query layer that both the
  official MCP server and `langchain-tableau` use under the hood. This is the data-reading
  foundation; `tableau-mcp-publish` does not use it.

## Could this go upstream?

The publish tools are intentionally shaped to match the official server's conventions
(`@modelcontextprotocol/sdk`, the same env vars, stdio transport). If the maintainers want an
authoring extension, this repo is a working reference implementation. The recommended first step
is a GitHub Discussion on `tableau/tableau-mcp` (tracked as issue #1 here), not an unsolicited PR.
