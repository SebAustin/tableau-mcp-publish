# DEPLOYMENT.md

How `tableau-mcp-publish` is packaged, run, and rolled out.

## What it is to deploy

A **local stdio MCP server** that an MCP client (Claude Desktop, Cursor, etc.) launches as a
subprocess. There is no hosted service to operate — it runs on the user's machine next to the MCP
client and talks to Tableau Cloud over HTTPS. It spawns a Python sidecar over loopback.

## Prerequisites

- Node 22+ and `npm`.
- Python 3.12–3.13 and [`uv`](https://docs.astral.sh/uv/) (the server spawns `uv run` for the sidecar).
- A Tableau Cloud/Server site and a Personal Access Token (`PAT_NAME` / `PAT_VALUE`).

## Install & run (from source)

```bash
npm install && npm run build
cd sidecar && uv sync && cd ..
cp .env.example .env   # fill SERVER, SITE_NAME, PAT_NAME, PAT_VALUE
npm run dev            # stdio MCP server + spawns the sidecar
```

## MCP client config

```json
{
  "mcpServers": {
    "tableau-publish": {
      "command": "npx",
      "args": ["-y", "tableau-mcp-publish@latest"],
      "env": {
        "SERVER": "https://my-pod.online.tableau.com",
        "SITE_NAME": "mysite",
        "PAT_NAME": "my_pat",
        "PAT_VALUE": "…"
      }
    }
  }
}
```

Runs side by side with the official `tableau` server on the same PAT.

## Gated live verification (the demo)

```bash
export SERVER=… SITE_NAME=… PAT_NAME=… PAT_VALUE=…
export DEMO_PROJECT="Sales"      # a project that already exists on your site
npm run demo -- examples/top_customers.csv
```

Publishes a datasource + starter workbook from the bundled CSV and prints two Cloud URLs. Use a
free [Tableau Developer Program](https://www.tableau.com/developer) site. This is the only step
that writes to a real site; everything else is unit/headless tested.

## Rollout (gated)

1. **Private GitHub repo first.** Push the branch, let CI go green, review. *(done in this run)*
2. **Flip to public** only on explicit confirmation — once the live demo has produced real URLs and
   the README/docs read well.
3. **npm publish (later, gated).** When ready to ship `npx tableau-mcp-publish`:
   `npm publish --access public`. Requires an npm login; not run automatically. Ensure `files`
   includes `dist` + `sidecar` (it does) so the sidecar ships with the package.

## Runbook

- **Least privilege (important):** every tool runs with the PAT's full authority, and the SQL you
  pass to `create_datasource_from_query` runs with the DB credentials you supply. Use a PAT whose
  Tableau user has only the roles it needs, and a **read-only** Snowflake/Postgres role for queries.
  See `SECURITY.md` (F-02, F-12).
- **Sidecar won't start:** ensure `uv` is on PATH and `cd sidecar && uv sync` succeeded. The server
  surfaces the sidecar's stderr on a startup failure.
- **401 on sign-in:** PAT expired/typo, or `SITE_NAME` is wrong (use the site's contentUrl).
- **"Project not found":** `projectName` must match an existing project exactly; create it first
  with `create_project`.
- **Workbook opens but a field is unknown:** the sheet referenced a field that isn't in the
  published datasource — field names must match the datasource columns exactly.
- **Large files:** publishing automatically switches to chunked upload above 64 MB.

## Deployment-readiness checklist

- [x] Config via environment; no secrets in code; `.env` gitignored; `.env.example` provided.
- [x] CI green on both layers across the version matrix.
- [x] Headless tests for the authoring + publish paths; mocked REST (no PAT in CI).
- [x] Graceful shutdown (sign out + stop sidecar) on SIGINT/SIGTERM.
- [x] Sidecar bound to loopback with a per-spawn token.
- [ ] Live demo executed against a Dev site (recorded in `ACCEPTANCE.md`).
- [ ] npm publish (deferred until after public release).
