# DEPLOYMENT.md

How `tableau-mcp-publish` is packaged, run, and rolled out.

## What it is to deploy

A **local stdio MCP server** that an MCP client (Claude Desktop, Cursor, etc.) launches as a
subprocess. There is no hosted service to operate — it runs on the user's machine next to the MCP
client and talks to Tableau Cloud over HTTPS. It spawns a Python sidecar over loopback.

## Prerequisites

- Node ≥ 22.7.5 and `npm`.
- Python 3.12–3.13 and [`uv`](https://docs.astral.sh/uv/) (the server spawns `uv run` for the sidecar).
- A Tableau Cloud/Server site and a Personal Access Token (`PAT_NAME` / `PAT_VALUE`).

## Install & run (from source)

```bash
npm install && npm run build
cd sidecar && uv sync && cd ..
cp .env.example .env   # fill SERVER, SITE_NAME, PAT_NAME, PAT_VALUE
npm run dev            # stdio MCP server + spawns the sidecar
```

### Sidecar install commands (reference)

| Purpose | Command |
|---------|---------|
| Runtime only (what the MCP server needs) | `cd sidecar && uv sync` |
| All extras including DB connectors | `cd sidecar && uv sync --all-extras` |
| CI / locked dev environment | `cd sidecar && uv sync --extra dev --frozen` |
| With DB connector extras | `cd sidecar && uv sync --extra connectors` |

The lockfile (`sidecar/uv.lock`) is committed and ships inside the npm tarball so `uv sync` is
fully reproducible without network access to PyPI beyond what uv already has cached.

## Environment variables

All four are required at runtime. No others are read from the environment by default.

| Variable | Description |
|----------|-------------|
| `SERVER` | Full URL of the Tableau Cloud/Server pod, e.g. `https://10ax.online.tableau.com` |
| `SITE_NAME` | The site's `contentUrl` (empty string `""` for the Default site) |
| `PAT_NAME` | Personal Access Token name |
| `PAT_VALUE` | Personal Access Token secret — never log, never commit |

`DEMO_PROJECT` is also required when running the dashboard demo (see below).

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
        "PAT_VALUE": "..."
      }
    }
  }
}
```

Runs side by side with the official `tableau` server on the same PAT.

## Registered tools (14 total)

### Data ingestion (tools 1–5)

| Tool | Description |
|------|-------------|
| `create_datasource_from_query` | Run SQL against Snowflake/Postgres → Hyper extract → publish `.tdsx` |
| `create_datasource_from_table` | CSV or inline records → Hyper extract → publish `.tdsx` |
| `create_datasource_from_file` | **New (M2)** — local CSV, JSON, JSONL, Excel (.xlsx/.xls), or Parquet → Hyper extract → publish `.tdsx` |
| `publish_datasource` | Publish a pre-built `.tdsx` or `.hyper` file |
| `publish_workbook` | Publish a pre-built `.twbx` file |

### Workbook authoring (tools 6–8)

| Tool | Description |
|------|-------------|
| `create_starter_workbook` | Build and publish a `.twbx` bound to a published datasource |
| `design_dashboard` | **New (M5)** — generate a `DashboardPlan` JSON from a business question + audience + field hints; supports `autonomous`, `interview`, `interview_followup`, and `directed` modes |
| `build_from_plan` | **New (M6)** — build and publish a dashboard `.twbx` from a `DashboardPlan` produced by `design_dashboard` |

### Site management (tools 9–14)

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects on the site |
| `create_project` | Create a Tableau project |
| `list_content` | List published datasources or workbooks |
| `refresh_datasource` | Trigger a background refresh of a published datasource |
| `delete_content` | Delete a published datasource or workbook |
| `set_permissions` | Set project-level permissions on a content item |

## Python sidecar — new deps in this release

| Package | Version | Why |
|---------|---------|-----|
| `openpyxl` | 3.1.5 | Excel `.xlsx`/`.xls` read support for `create_datasource_from_file` |
| `pyarrow` | 24.0.0 | Parquet read support (pulled in transitively by `pantab==5.2.0`) |
| `fastapi` | 0.121.0 | Bumped; adds `/datasource/from-file` and `/workbook/dashboard` routes |

These are all declared in `sidecar/pyproject.toml` and pinned in `sidecar/uv.lock`.
`uv sync` (any form) installs them automatically — no manual step needed.

## Gated live demos

### Original demo — datasource + starter workbook

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
export DEMO_PROJECT="Sales"      # a project that already exists on your site
npm run demo -- examples/top_customers.csv
```

### Dashboard demo (GATED — authorized action only)

This is the **single outward action** that writes to a real Tableau site. Run it only after
confirming the target project and credentials.

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
export DEMO_PROJECT="Sales"
npm run demo:dashboard -- examples/top_customers.csv
```

Publishes a Hyper datasource from the CSV, runs the autonomous planning pipeline to derive
a `DashboardPlan`, builds a `.twbx` with a dashboard tiling all sheets via the sidecar, and
publishes it to Tableau Cloud. Prints both Cloud URLs on completion.

Use a free [Tableau Developer Program](https://www.tableau.com/developer) site. This is the only
step that writes to a real site; everything else is unit/headless tested.

## Pre-flight verification (no live credentials needed)

```bash
npm run verify-setup
```

Checks: `dist/index.js` built, `.env` present and PAT not a placeholder, Tableau sign-in,
sidecar health. Fails fast with a clear message on each missing condition.

```bash
npm run test:mcp-smoke
# or: npm run test:mcp-smoke -- --publish-only
```

Connects to the MCP server over stdio and lists all registered tools. Verifies 14 tools respond.
Requires real credentials in `.env` or the environment; exits 0 gracefully when they are absent.

Note: if port 8899 is already bound by a running sidecar, `verify-setup` and `test:mcp-smoke`
will report a sidecar startup error. Kill the existing sidecar process first:
`kill $(lsof -ti :8899)`

## Rollout (gated)

1. **Private GitHub repo first.** Push the branch, let CI go green, review. *(done in this run)*
2. **Flip to public** only on explicit confirmation — once the live demo has produced real URLs and
   the README/docs read well.
3. **npm publish (later, gated).** When ready to ship `npx tableau-mcp-publish`:
   ```bash
   npm publish --access public
   ```
   Requires an npm login; not run automatically. The `files` allowlist in `package.json` ensures
   the tarball includes `dist/` + `sidecar/*.py` + `sidecar/pyproject.toml` + `sidecar/uv.lock`
   and excludes tests, caches, logs, and `.cursor/`.

## Runbook

- **Least privilege (important):** every tool runs with the PAT's full authority, and the SQL you
  pass to `create_datasource_from_query` runs with the DB credentials you supply. Use a PAT whose
  Tableau user has only the roles it needs, and a **read-only** Snowflake/Postgres role for queries.
  See `SECURITY.md` (F-02, F-12).
- **Sidecar won't start:** ensure `uv` is on PATH and `cd sidecar && uv sync` succeeded. The server
  surfaces the sidecar's stderr on a startup failure.
- **Port 8899 already in use:** another sidecar process is running (common after a Cursor restart
  without a clean shutdown). Run `kill $(lsof -ti :8899)` to free it.
- **401 on sign-in:** PAT expired/typo, or `SITE_NAME` is wrong (use the site's contentUrl).
- **"Project not found":** `projectName` must match an existing project exactly; create it first
  with `create_project`.
- **Workbook opens but a field is unknown:** the sheet referenced a field that isn't in the
  published datasource — field names must match the datasource columns exactly.
- **Large files:** publishing automatically switches to chunked upload at 64 MB or larger.
- **`design_dashboard` in directed mode:** `directions` is required and must be non-empty; the
  tool rejects calls where it is absent or blank.

## Deployment-readiness checklist

- [x] Config via environment; no secrets in code; `.env` gitignored; `.env.example` provided.
- [x] CI green on both layers (87 TS + 114 Python) across the version matrix.
- [x] All 14 tools compile into `dist/` (`npm run build` clean, zero TS errors).
- [x] Headless tests for the authoring + publish paths; mocked REST (no PAT in CI).
- [x] Graceful shutdown (sign out + stop sidecar) on SIGINT/SIGTERM.
- [x] Sidecar bound to loopback with a per-spawn token.
- [x] New Python deps (openpyxl, pyarrow, fastapi bump) pinned in `uv.lock`; `uv sync --frozen` clean.
- [x] npm tarball verified via `npm pack --dry-run`: 75 files, 119 kB packed / 432 kB unpacked.
      Includes: `dist/` (all tools + planner), `sidecar/*.py` (4 files), `sidecar/pyproject.toml`,
      `sidecar/uv.lock`, `LICENSE`, `README.md`, `package.json`. Excludes: `sidecar/tests/`,
      `sidecar/.venv/`, `scripts/`, `tests/`, caches, logs, `.cursor/`.
- [x] Smoke test (`npm run test:mcp-smoke`) and verify-setup (`npm run verify-setup`) both exit
      gracefully without creds; pass when real credentials are present (requires free port 8899).
- [ ] Live dashboard demo (`npm run demo:dashboard`) executed against a Dev site (record URLs in `ACCEPTANCE.md`).
- [ ] npm publish (deferred until after public release).
