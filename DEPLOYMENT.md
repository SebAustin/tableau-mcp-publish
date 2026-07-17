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

The four core variables are required at runtime — the server refuses to start without them (a
zod-validated config error, never echoing the PAT):

| Variable | Description |
|----------|-------------|
| `SERVER` | Full URL of the Tableau Cloud/Server pod, e.g. `https://10ax.online.tableau.com` |
| `SITE_NAME` | The site's `contentUrl` (empty string `""` for the Default site) |
| `PAT_NAME` | Personal Access Token name |
| `PAT_VALUE` | Personal Access Token secret — never log, never commit |

Optional (`config.ts` defaults applied when absent):

| Variable | Default | Description |
|----------|---------|-------------|
| `TABLEAU_API_VERSION` | `3.28` | Tableau REST API version. |
| `SIDECAR_HOST` | `127.0.0.1` | Loopback host for the Python sidecar. |
| `SIDECAR_PORT` | auto-selected free port | Pin only if you need a fixed port. |

Required only for specific demos/scripts (not for the server itself to start):

| Variable | Used by |
|----------|---------|
| `DEMO_PROJECT` | `npm run demo`, `npm run demo:dashboard`, `npm run demo:superstore` — must name an existing, non-Default project. |
| `PERSONA` | `npm run demo:superstore` — alternative to passing `--persona <name>` on the command line. |

**Live-connection credentials are NOT environment variables.** `create_live_datasource` (Snowflake/
Presto) takes `credentials.username`/`credentials.password` as **tool-call parameters**, not
server-side config — the MCP server's own environment surface is unchanged by this feature. The
tool's description recommends the *calling agent* source these from its own environment (e.g. a
convention like `SNOWFLAKE_USER`/`SNOWFLAKE_PASSWORD` in the agent's own process) and pass them as
call arguments — never hardcode them in a prompt or a saved plan. This is a documented convention
for callers, not something `config.ts` reads or `.env.example` should declare, since the server
process never consults such variables itself. See `docs/adr/0009-live-connection-credential-handling.md`.

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

## Registered tools (27 total)

### Ingest & datasources (6)

| Tool | Description |
|------|-------------|
| `create_datasource_from_query` | Run SQL against Snowflake/Postgres/csv → Hyper extract → publish `.tdsx` |
| `create_datasource_from_table` | CSV or inline records → Hyper extract → publish `.tdsx` |
| `create_datasource_from_file` | Local CSV, JSON, JSONL, Excel (.xlsx/.xls), or Parquet → Hyper extract → publish `.tdsx` (numeric/date coercion applied) |
| `create_live_datasource` | Live Snowflake/Presto connection → `.tds` (no extract) → publish with embedded credentials |
| `publish_datasource` | Publish a pre-built `.tdsx` or `.hyper` file |
| `publish_workbook` | Publish a pre-built `.twb`/`.twbx` file |

### Design & build (3)

| Tool | Description |
|------|-------------|
| `design_dashboard` | Business question + audience/persona → `DashboardProposal` (no publish); supports `autonomous`, `interview`, `interview_followup`, `directed` modes |
| `build_from_plan` | `proposal.plan` → embedded-extract `.twbx` (+ story dashboard, if any) + governed `.tdsx` → publish both |
| `create_starter_workbook` | Build and publish a `.twbx` bound to an already-published datasource |

### Branding (1)

| Tool | Description |
|------|-------------|
| `validate_brand` | Validate `brand.yaml`; list personas; never throws |

### Metadata (1)

| Tool | Description |
|------|-------------|
| `get_datasource_fields` | Real field names/types/aggregations for a published datasource, via VDS |

### Scheduling & automation (3)

| Tool | Description |
|------|-------------|
| `schedule_refresh` | Create a recurring Cloud extract-refresh task (honest Bridge caveat always included) |
| `list_refresh_schedules` | List every extract-refresh task on the site |
| `delete_refresh_schedule` | Delete a refresh task (`confirm: true` required) |

### Webhooks (3)

| Tool | Description |
|------|-------------|
| `create_webhook` | Create an HTTPS webhook on a Tableau site event (site-admin PAT required) |
| `list_webhooks` | List webhooks configured on the site |
| `delete_webhook` | Delete a webhook (`confirm: true` required) |

### Pulse (4)

| Tool | Description |
|------|-------------|
| `create_pulse_definition` | Create a Pulse metric definition (VDS pre-flight validated; live creation currently returns 400 pending a fixture — see `docs/adr/0011-pulse-best-effort-payload.md`) |
| `list_pulse_definitions` | List Pulse metric definitions on the site |
| `create_pulse_metric` | Create a Pulse metric instance from a definition |
| `delete_pulse_definition` | Delete a Pulse metric definition (`confirm: true` required) |

### Site management (6)

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects on the site |
| `create_project` | Create a Tableau project |
| `list_content` | List published datasources or workbooks |
| `refresh_datasource` | Trigger a single immediate extract refresh (not recurring — see `schedule_refresh`) |
| `delete_content` | Delete a published datasource or workbook (`confirm: true` required; refuses `Default` project) |
| `set_permissions` | Set permissions on a content item (allowlisted capabilities; elevated grants need `confirmElevated: true`) |

6 + 3 + 1 + 1 + 3 + 3 + 4 + 6 = **27**. Full parameter/return/guardrail reference:
[`docs/tool_reference.md`](docs/tool_reference.md).

## Python sidecar — dependency history

| Package | Version | Why |
|---------|---------|-----|
| `openpyxl` | 3.1.5 | Excel `.xlsx`/`.xls` read support for `create_datasource_from_file` |
| `pyarrow` | (transitive via pantab) | Parquet read support |
| `fastapi` | 0.121.0 | `/datasource/from-file`, `/datasource/live`, and `/workbook/dashboard` (brand + story blocks) routes |
| `yaml` (TypeScript, `yaml@2.9.0`) | 2.9.0 | `brand.yaml` parsing (`src/branding/load.ts`) |

All declared in `sidecar/pyproject.toml` / `package.json` and pinned in `sidecar/uv.lock` /
`package-lock.json`. `uv sync` / `npm install` installs them automatically — no manual step needed.

## Gated live demos

All demos are guarded and no-op with a clear message unless `SERVER`, `SITE_NAME`, `PAT_NAME`,
`PAT_VALUE`, and `DEMO_PROJECT` (an existing, non-Default project) are set.

### Original demo — datasource + starter workbook

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
export DEMO_PROJECT="Sales"      # a project that already exists on your site
npm run demo -- examples/top_customers.csv
```

### Dashboard demo — propose→build pipeline

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
export DEMO_PROJECT="Sales"
npm run demo:dashboard -- examples/top_customers.csv
```

Publishes a Hyper datasource from the CSV, runs the autonomous planning pipeline (analyst
audience) to derive a `DashboardProposal`, calls `build_from_plan` with `proposal.plan`, and prints
both published Cloud URLs.

### Superstore demo — the richest example (KPI band, color, geo, branding, stories)

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
export DEMO_PROJECT="Sales"
npm run demo:superstore                          # reads $HOME/Downloads/Sample - Superstore_Migrated Data.csv by default
npm run demo:superstore -- "/path/to/file.csv"    # or pass an explicit path
npm run demo:superstore -- --persona ceo          # also resolves a named persona and applies its brand
```

Ingests a UTF-16/TSV-shaped Superstore export (encoding/delimiter auto-sniffed), designs an exec
dashboard (KPI band with period deltas, a Segment-colored bar, a filled US-state map), optionally
applies a persona's brand (palette/typography/number formats), builds the self-contained embedded-
extract workbook, and publishes it. This is the one-command live proof for the branding system
(Phase E1) and the richest exec-plan path (Phase 1).

### Local-file refresh automation (no live-site write beyond a republish)

```bash
export SERVER=...  SITE_NAME=...  PAT_NAME=...  PAT_VALUE=...
npm run refresh:local -- --file examples/top_customers.csv --name "Top Customers" --project "Sales"
```

Re-ingests the file and republishes with `overwrite=true`. Proven live: republished the Superstore
datasource in 4.7s against a real Cloud site (see `ACCEPTANCE.md`'s E2 section).

```bash
npm run cron:generate -- --daily 06:30 --file examples/top_customers.csv --name "Top Customers" --project "Sales"
```

Prints (does not install) a crontab line + macOS launchd plist for the schedule above.

Use a free [Tableau Developer Program](https://www.tableau.com/developer) site for testing. Free
port 8899 first if a stale sidecar is bound: `kill $(lsof -ti :8899)`.

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

Connects to the MCP server over stdio and lists all registered tools. Verifies **27** tools
respond. Requires real credentials in `.env` or the environment; exits 0 gracefully when they are
absent.

Note: if the sidecar's port is already bound by a running sidecar, `verify-setup` and
`test:mcp-smoke` will report a sidecar startup error. Kill the existing sidecar process first
(default historical port 8899): `kill $(lsof -ti :8899)`.

## Rollout (gated)

1. **Private GitHub repo first.** Push the branch, let CI go green, review.
2. **Flip to public** only on explicit confirmation — once the live demos have produced real URLs
   and the README/docs read well.
3. **npm publish (later, gated).** When ready to ship `npx tableau-mcp-publish`:
   ```bash
   npm publish --access public
   ```
   Requires an npm login; not run automatically. The `files` allowlist in `package.json` ensures
   the tarball includes `dist/` + `brand.yaml` + `sidecar/*.py` + `sidecar/pyproject.toml` +
   `sidecar/uv.lock` and excludes tests, caches, logs, and `.cursor/`. Verified via
   `npm pack --dry-run`: **130 files, ~269 kB packed / ~1.1 MB unpacked**.

## Runbook

Day-to-day operation, monitoring, and troubleshooting live in
[`docs/runbook.md`](docs/runbook.md) — quick highlights:

- **Least privilege (important):** every tool runs with the PAT's full authority, and the SQL you
  pass to `create_datasource_from_query` runs with the DB credentials you supply. Use a PAT whose
  Tableau user has only the roles it needs, and a **read-only** Snowflake/Postgres role for queries.
  See `SECURITY.md`.
- **Sidecar won't start:** ensure `uv` is on PATH and `cd sidecar && uv sync` succeeded. The server
  surfaces the sidecar's stderr on a startup failure.
- **Port already in use:** another sidecar process is running (common after a Cursor restart
  without a clean shutdown). Run `kill $(lsof -ti :8899)` (or your pinned `SIDECAR_PORT`) to free it.
- **401 on sign-in:** PAT expired/typo, or `SITE_NAME` is wrong (use the site's contentUrl).
- **"Project not found":** `projectName` must match an existing project exactly; create it first
  with `create_project`.
- **Workbook opens but a field is unknown:** the sheet referenced a field that isn't in the
  published datasource — call `get_datasource_fields` and use exact field names.
- **Large files:** publishing automatically switches to chunked upload at 64 MB or larger.
- **Scheduled refresh created but every run fails:** the target datasource is file-based, not
  cloud-reachable — see the local-file cron design-around above instead of `schedule_refresh`.
- **Pulse `create_pulse_definition` returns a bare 400:** known, honestly-flagged limitation
  pending a live fixture (`docs/adr/0011-pulse-best-effort-payload.md`) — not resolvable client-side today.
- **`design_dashboard` in directed mode:** `directions` is required and must be non-empty; the
  tool rejects calls where it is absent or blank.

## Deployment-readiness checklist

- [x] Config via environment; no secrets in code; `.env` gitignored; `.env.example` provided.
- [x] CI green on both layers (525 TS + 338 Python) across the version matrix.
- [x] All 27 tools compile into `dist/` (`npm run build` clean, zero TS errors).
- [x] Headless tests for the authoring + publish + scheduling + webhook + Pulse paths; mocked REST (no PAT in CI).
- [x] Graceful shutdown (sign out + stop sidecar) on SIGINT/SIGTERM.
- [x] Sidecar bound to loopback with a per-spawn token.
- [x] Live-connection credentials never written to disk/logs (asserted in tests).
- [x] Local-file cron template generator shell-escapes all interpolated values (VB-02 fix).
- [x] npm tarball verified via `npm pack --dry-run`: 130 files, ~269 kB packed / ~1.1 MB unpacked.
- [x] Smoke test (`npm run test:mcp-smoke`) and verify-setup (`npm run verify-setup`) both exit
      gracefully without creds; pass when real credentials are present (requires a free sidecar port).
- [x] Live demos executed against a Dev site (datasource, dashboard, branded dashboard, story,
      local-file refresh — URLs recorded in `ACCEPTANCE.md`).
- [ ] Live proof of `schedule_refresh` against a genuinely cloud-reachable (Snowflake) connection
      (needs account credentials — see `ACCEPTANCE.md`'s E2 pending items).
- [ ] Pulse create-definition live fixture (needs a manually-created Pulse UI definition to GET back
      and lock the payload shape — see `docs/adr/0011-pulse-best-effort-payload.md`).
- [ ] npm publish (deferred until after public release).
