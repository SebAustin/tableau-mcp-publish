# Relationship to the official Tableau MCP server

`tableau-mcp-publish` is a **companion** to [`tableau/tableau-mcp`](https://github.com/tableau/tableau-mcp),
not a fork or a competitor. The official server **reads**; this one **writes**. Together they form a
full agentic Tableau loop.

## Read vs. write

| Capability | Official `@tableau/mcp-server` | `tableau-mcp-publish` (this) |
|---|---|---|
| Query a published datasource (VizQL Data Service) | ✅ | — (use the official server) |
| Read column metadata (Metadata API) | ✅ | — |
| Pulse metrics | ✅ | — |
| Create a published datasource from SQL | ❌ | ✅ `create_datasource_from_query` |
| Create a published datasource from a DataFrame/CSV | ❌ | ✅ `create_datasource_from_table` |
| Build a `.hyper` extract | ❌ | ✅ (Hyper API sidecar) |
| Generate a workbook (`.twb`/`.twbx`) | ❌ | ✅ `create_starter_workbook` |
| Publish datasource / workbook to Cloud | ❌ | ✅ `publish_datasource` / `publish_workbook` |
| Manage projects / permissions | ❌ | ✅ `list_projects`, `create_project`, `set_permissions` |
| Refresh / list / delete content | ❌ | ✅ `refresh_datasource`, `list_content`, `delete_content` |

## Designed to run side by side

- **Same auth contract.** Identical env vars (`SERVER`, `SITE_NAME`, `PAT_NAME`, `PAT_VALUE`), so
  a single MCP client config runs both servers on one Personal Access Token.
- **No overlap.** This server implements **zero** read/query tools by design — there is no
  duplication or contention with the official server.
- **Composable.** An agent reads metadata with the official server (e.g. discover a date column),
  then authors with this one (e.g. propose a time-series line). See issue #3 in the repo.

## Could this go upstream?

The publish tools are intentionally shaped to match the official server's conventions
(`@modelcontextprotocol/sdk`, the same env vars, the same stdio transport). If the maintainers
want an authoring extension, this repo is a working reference implementation. The recommended
first step is a GitHub Discussion on `tableau/tableau-mcp` (tracked as issue #1 here), not an
unsolicited PR.
