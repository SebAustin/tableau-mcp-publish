# Architecture

`tableau-mcp-publish` is two layers that share a machine but split responsibilities cleanly.

## TypeScript MCP server (`src/`)

- **`index.ts`** — MCP server entry (stdio). Loads config, signs in to Tableau, spawns the
  sidecar, registers all 11 tools, and handles graceful shutdown (sign out + stop sidecar).
- **`config.ts`** — env-var config (`SERVER`, `SITE_NAME`, `PAT_NAME`, `PAT_VALUE`,
  `TABLEAU_API_VERSION`, `SIDECAR_HOST/PORT`), validated with zod. Errors never echo the PAT.
- **`restClient.ts`** — Tableau REST client over `undici`: PAT sign-in, projects, content,
  permissions, and publishing. Publishing chooses **single multipart** (≤64 MB) or **chunked
  `fileUploads`** (>64 MB); a failed chunk aborts without finalizing.
- **`sidecar.ts`** — spawns the Python sidecar and calls it over loopback HTTP with a per-spawn
  `X-Sidecar-Token` header.
- **`tools/`** — one module per tool group; each registers `McpServer.registerTool` with a zod
  input schema, an output schema, and an agent-readable description.

## Python authoring sidecar (`sidecar/`)

- **`server.py`** — FastAPI app (`/health`, `/datasource/from-query`, `/datasource/from-table`,
  `/workbook/starter`) guarded by the shared token.
- **`hyper_builder.py`** — DataFrame/SQL/CSV → `.hyper` via pantab (Hyper types inferred from
  pandas dtypes); reads the extract back to derive column metadata.
- **`tds_builder.py`** — packages a `.hyper` into a `.tdsx` (hand-built `.tds` XML with a `hyper`
  connection + per-column metadata, zipped with the extract under `Data/`).
- **`twb_builder.py`** — generates a `.twb`/`.twbx` bound to a *published* datasource
  (`class='sqlproxy'` + `repository-location`), one worksheet per sheet with shelves + mark class.

## Boundary

REST calls and auth live in TypeScript. All Tableau file authoring lives in Python — the Hyper
and document formats are Python-first, so there is no reason to reimplement them in TS. The
sidecar never touches the network except localhost.

## Why a sidecar (vs. one language)

The MCP ecosystem and the official Tableau server are TypeScript; matching that lets this server
drop into the same client config. But the Hyper API and the practical tooling for `.hyper`/`.tdsx`
authoring are Python. A spawned, token-guarded localhost sidecar gets the best of both without a
network dependency or a reimplementation.

See `docs/publish_lifecycle.md` for the end-to-end create → package → publish flow, and the ADRs
in this folder for the key decisions.
