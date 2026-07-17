# Runbook

How to run, operate, monitor, and troubleshoot `tableau-mcp-publish` — a local stdio MCP server
that an MCP client (Claude Desktop, Cursor, etc.) launches as a subprocess. There is no hosted
service to operate; it runs on the operator's machine and talks to Tableau Cloud over HTTPS,
spawning a Python sidecar over loopback.

## Running it

### First-time setup

```bash
git clone https://github.com/SebAustin/tableau-mcp-publish && cd tableau-mcp-publish
npm install && npm run build
cd sidecar && uv sync && cd ..
cp .env.example .env   # fill SERVER, SITE_NAME, PAT_NAME, PAT_VALUE
```

### Start the server

```bash
npm run dev        # tsx src/index.ts — stdio MCP server, spawns the sidecar
# or, after `npm run build`:
npm start           # node dist/index.js
```

Both read config from the environment (or a `.env` file loaded by the process). On startup the
server signs in to Tableau, starts the Python sidecar, and logs readiness to **stderr** (stdout is
reserved for the MCP protocol channel):

```
[tableau-mcp-publish] Signing in to https://10ax.online.tableau.com (site "mysite")…
[tableau-mcp-publish] Starting Python authoring sidecar…
[tableau-mcp-publish] Ready. Tableau publishing tools are available over stdio.
```

### Pre-flight verification (no live credentials needed to detect config problems)

```bash
npm run verify-setup
```

Checks, in order: `dist/index.js` built, `.env` present and `PAT_VALUE` not a placeholder, config
schema valid, Tableau sign-in succeeds, sidecar starts and reports healthy. Fails fast on the first
broken check with a clear next step. Never prints `PAT_VALUE`.

```bash
npm run test:mcp-smoke
```

Connects to the MCP server over stdio, lists all registered tools (expect **27**), and calls one
read-only probe tool per server. Requires real credentials; exits 0 gracefully when absent.

### Shutdown

`SIGINT`/`SIGTERM` trigger a graceful shutdown: stop the sidecar, sign out of Tableau, exit 0. An
MCP client killing the subprocess (normal disconnect) triggers this path automatically.

## Operating

### CI-equivalent local gate

```bash
make ci   # build + lint + test (TS) and ruff + mypy --strict + pytest (sidecar)
```

Individual targets: `make build`, `make lint`, `make typecheck`, `make test`, `make sidecar-lint`,
`make sidecar-typecheck`, `make sidecar-test`. `make clean` removes `dist/`, caches, and the
sidecar venv.

### Gated live demos

All demos are guarded — they no-op with a message unless `SERVER`, `SITE_NAME`, `PAT_NAME`,
`PAT_VALUE`, and `DEMO_PROJECT` (an existing, **non-Default** project) are all set.

| Command | What it does |
|---|---|
| `npm run demo -- examples/top_customers.csv` | Publishes a datasource + starter workbook from a bundled CSV. |
| `npm run demo:dashboard -- examples/top_customers.csv` | Runs the full propose→build pipeline (autonomous, analyst audience) and publishes a dashboard. |
| `npm run demo:superstore -- --persona ceo` | The richest demo: ingests a Superstore-shaped file (UTF-16/TSV auto-sniffed), designs an exec dashboard (KPI band, color-encoded bar, filled US-state map), optionally applies a named persona's brand, and publishes it. Reads `$HOME/Downloads/Sample - Superstore_Migrated Data.csv` by default, or a path passed as the first positional arg. |

### Local-file refresh automation (no Tableau Bridge required)

```bash
npm run refresh:local -- --file <path> --name "<datasource name>" --project "<project>" [--persona <name>]
```

Re-ingests the local file and republishes with `overwrite=true`. Exits 0/1 cleanly with exactly one
summary log line — safe to wire into cron/launchd's own failure alerting. `--persona` is validated
against `brand.yaml` (fails loud on a typo) and logged for traceability; it does **not** rebuild a
dashboard, only the datasource.

```bash
npm run cron:generate -- --daily 06:30 --file <path> --name "<ds name>" --project "<project>" [--write]
# or: npm run cron:generate -- --hourly --file ... --name ... --project ...
```

Prints a crontab line + a macOS launchd plist that invoke `refresh-local.ts` on the given schedule.
**Nothing is installed automatically** — the command only prints (or, with `--write`, saves under
the gitignored `scripts/cron/`) the artifacts; the operator reviews and installs one of the two
mechanisms themselves (install/verify/uninstall instructions are printed alongside each artifact).

### Monitoring

There is no built-in metrics/telemetry surface (see CODEBASE.md's tech-debt notes). In practice,
monitor via:

- **stderr logs** — every log line is prefixed `[tableau-mcp-publish]`; the MCP client (Cursor,
  Claude Desktop) typically surfaces subprocess stderr in its own MCP server logs panel.
- **Pulse `create_pulse_definition` VDS pre-flight failures** and **REST retry exhaustion** both
  produce structured `TableauApiError`s (`status`/`code`/`summary`/`detail`) surfaced back through
  the MCP tool-call error response — an agent/client sees these as a failed tool call with the
  error message, not a silent failure.
- **Scheduled refreshes and webhooks** are Cloud-side resources once created — monitor their actual
  run history in the Tableau Cloud UI (Site Settings → Scheduled Tasks / Webhooks), not from this
  server, which has no persistent view into runs after task creation.
- **Local-file cron jobs** log to `scripts/cron/refresh-local.log` (path baked into the generated
  crontab line / plist) — tail that file, or rely on cron's mail-on-output / launchd's
  `StandardOutPath`/`StandardErrorPath`.

## Troubleshooting — common failures

| Symptom | Cause | Fix |
|---|---|---|
| Sidecar won't start / "sidecar failed" | `uv` not on `PATH`, or `cd sidecar && uv sync` never run | Run `cd sidecar && uv sync`. The server surfaces the sidecar's stderr on a startup failure — read it first. |
| Port already in use (`EADDRINUSE`, historically port 8899) | A stale sidecar process from a prior run/crash still holds the port | The server auto-selects a free ephemeral port by default; if you pinned `SIDECAR_PORT`, free it: `kill $(lsof -ti :8899)` (or whatever port you pinned). |
| `401` on sign-in | PAT expired/typo'd, or `SITE_NAME` wrong | Verify `PAT_NAME`/`PAT_VALUE` in Tableau Cloud → Account Settings → Personal Access Tokens; `SITE_NAME` is the site's `contentUrl`, not its display name. |
| "Project not found" from any publish tool | `projectName` doesn't match an existing project exactly (case-sensitive, no fuzzy match) | Call `list_projects` first, or `create_project` if it genuinely doesn't exist yet — the server never auto-creates projects. |
| Workbook publishes but a field is "unknown" in Tableau | A sheet spec referenced a field name that doesn't match the datasource's real columns | Call `get_datasource_fields` (or read the `columns` field from `create_datasource_from_file`'s response) and use those exact names as `fieldHints`/sheet fields. |
| `build_from_plan` throws "rendering a dashboard requires…" | The plan has no `datasourceSpec.filePath` (LUID-only, or the `sql` query branch) | Only a local-file-backed `datasourceSpec` can produce an embeddable `.hyper` extract. Re-run `design_dashboard` with a file-backed datasource, or use `create_starter_workbook` against an already-published datasource instead (accepts the LUID-only binding, no embedded extract). |
| `build_from_plan` throws "contains placeholder token" | `design_dashboard` was called without real `fieldHints`, so the plan has `<measure>`/`<dimension>` placeholders | Re-run `design_dashboard` with `fieldHints` from `get_datasource_fields`, or hand-edit the plan's sheet fields before confirming. |
| `schedule_refresh` task created but every run fails on Cloud | The target datasource has no cloud-reachable, credentialed source (e.g. it's a local-file extract) — Cloud cannot refresh it without Tableau Bridge | Expected per the tool's own `note`. Use `npm run refresh:local` + `npm run cron:generate` for file-based datasources instead of a Cloud-side schedule. |
| `create_live_datasource` throws "not REST-publishable" | Snowflake key-pair authentication was requested | Impossible over REST by design (Tableau's Publish Datasource API only accepts username/password or OAuth). Configure key-pair auth in Tableau Desktop and publish from there. |
| `create_webhook` returns 403 | The signed-in PAT's user lacks site-administrator privileges | Webhook creation is Tableau's own admin-gated capability, not something this server can bypass — use a site-admin PAT. |
| `create_pulse_definition` returns a bare 400 with no detail | Live proof of the Pulse create-definition payload shape is currently blocked (see `docs/adr/0011-pulse-best-effort-payload.md`) | Not yet resolvable client-side. Create one definition manually in the Pulse UI, `GET` it back via `list_pulse_definitions`, and use the real shape to correct `src/rest/pulse.ts` before relying on this in production. |
| Any Pulse tool call fails with a Pulse-specific hint about "not enabled" | Tableau Pulse is Cloud-only and must be turned on per-site | Site Settings → Pulse (Cloud only — Tableau Server has no Pulse). |
| `get_datasource_fields` / VDS calls fail with a "VDS unavailable" hint | VizQL Data Service isn't enabled for the site, or the LUID doesn't resolve | Confirm the LUID came from a **published** datasource (not a local-only extract); ask a site admin to enable VDS if the hint suggests it's disabled. |
| A tool call is slow / appears to hang under load | Transient `429`/`5xx` triggered the built-in retry loop (up to 3 attempts, ~8s total budget by default) | Expected behavior, not a hang — see `docs/architecture.md`'s retry-policy section. If it exceeds the budget, the original error is rethrown unchanged. |
| Large file publish is slow | Files ≥ 64 MB automatically switch to chunked upload (multiple round trips) | Expected; a failed chunk aborts without finalizing (no partial publish) rather than retrying automatically mid-chunk-sequence. |
| `design_dashboard` in `directed` mode throws immediately | `directions` is required and non-empty for `directed` mode | Supply an explicit sheet-directions string, e.g. `"a bar chart of revenue by region and a trend line over time"`. |
| Unknown `persona` name throws | Typo, or the persona isn't defined in `brand.yaml` | The error lists every available persona name — check `validate_brand`'s `personas` output, or add the persona to `brand.yaml`. |
| `delete_content` / `delete_refresh_schedule` / `delete_webhook` / `delete_pulse_definition` refuse to run | Missing `confirm: true`, or (for `delete_content`) the target lives in the `Default` project | Pass `confirm: true` explicitly. `delete_content` additionally never deletes from `Default` — this is a hard governance guardrail, not configurable. |

## Least-privilege guidance

Every tool runs with the signed-in PAT's full authority, and any SQL passed to
`create_datasource_from_query` runs with the database credentials supplied in that call. Use a PAT
whose Tableau user has only the roles it needs, and a **read-only** database role for query-backed
datasources. See `SECURITY.md` for the full threat-model writeup (owned separately from this
runbook — do not duplicate its findings here, only operational guidance).

## Escalation / where to look next

- **Tool contracts and guardrails:** `docs/tool_reference.md`.
- **System design and data flow:** `docs/architecture.md`.
- **Why a specific design decision was made:** `docs/adr/`.
- **What's actually been proven against a live Tableau Cloud site (and what hasn't):**
  `ACCEPTANCE.md`.
- **Security posture:** `SECURITY.md`.
