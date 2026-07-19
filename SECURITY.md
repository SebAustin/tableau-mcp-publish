# SECURITY.md

**Audit date:** 2026-06-20 (baseline) · 2026-06-24 (prompt-driven authoring add-on) · **Method:** STRIDE decomposition + manual code/dependency review.
**Result:** 0 CRITICAL · 0 HIGH · 4 MEDIUM · 7 LOW · 1 PASS. The core safety controls (PAT never
logged, stderr-only logging, loopback+token sidecar, confirm gate on delete, capability allowlist,
no silent overwrite / no Default-project publish) are present and effective.

**Prompt-driven authoring add-on (2026-06-24):** 0 CRITICAL · 0 HIGH · 2 MEDIUM · 2 LOW · 4 PASS — see the "Prompt-driven authoring — added surface" section below. No CRITICAL/HIGH issues; the feature is shippable.

## Trust boundaries

| Boundary | From → To | Control |
|---|---|---|
| stdio | MCP client (agent) → TS server | Tool args are agent-controlled (untrusted intent) |
| TLS | TS server → Tableau Cloud REST | PAT sign-in → session token (`X-Tableau-Auth`) |
| loopback | TS server → Python sidecar | `127.0.0.1` bind + per-spawn `X-Sidecar-Token` |
| TLS | sidecar → Snowflake/Postgres | Per-call credentials in the `connection` object |

## STRIDE summary

| Threat | Result |
|---|---|
| Spoofing | Mitigated — loopback bind + per-spawn token. |
| Tampering | XML values escaped; multipart boundary random. Gaps fixed: filename/part-name + ID escaping. |
| Repudiation | Tableau Cloud records the publishing user. |
| Information disclosure | PAT handling clean (PASS). Error-body redaction applied. |
| Denial of service | `maxRows` cap; whole-file buffering noted (LOW). |
| Elevation of privilege | Caller-supplied SQL is by-design (least-privilege documented); elevated-capability gate added. |

## Findings & remediation status

| ID | Sev | Finding | Status |
|---|---|---|---|
| F-01 | PASS | PAT/secret handling — never logged; errors don't echo the PAT; `.env` gitignored | ✅ Verified clean |
| F-02 | MED | Caller-supplied SQL runs against the DB (by design) | ✅ Documented: use a read-only / least-privilege DB role + PAT |
| F-03 | MED | Upstream API error bodies returned verbatim to the agent | ✅ Fixed — full body to stderr, redacted message to caller |
| F-04 | MED | Multipart `name`/`filename` header injection (CRLF/quote) | ✅ Fixed — sanitized in `buildMultipart` |
| F-05 | LOW | Sidecar token compared non-constant-time | ✅ Fixed — `hmac.compare_digest` |
| F-06 | LOW | Unescaped IDs in permission/publish XML | ✅ Fixed — `xmlEscape` on all interpolated IDs |
| F-07 | LOW | DB password in Postgres conninfo string | ✅ Fixed — keyword args to `psycopg.connect` |
| F-08 | LOW | `hyperd.log`/tests/caches shipped in the npm package | ✅ Fixed — `files` allowlist narrowed to `dist` + `sidecar/*.py` + `pyproject.toml` + `uv.lock`; verified clean via `npm pack --dry-run` (`.npmignore` kept as defense-in-depth) |
| F-09 | LOW | Generated extracts in shared temp (default perms) | ⚠️ Accepted — loopback-only; cleanup noted in runbook |
| F-10 | LOW | `csvPath` reads an arbitrary local file | ✅ Hardened — regular-file check; documented |
| F-11 | LOW | Whole-file / whole-result in memory | ⚠️ Accepted — `maxRows` cap mitigates; streaming is a follow-up |
| F-12 | MED | Prompt-injection → destructive/permission tools | ✅ Fixed — elevated capabilities (`Delete`, `ChangePermissions`, `ProjectLeader`, `ChangeHierarchy`) require `confirmElevated=true` |

## Dependencies

All deps are pinned. `undici` bumped `7.2.0 → 7.28.0` (later 7.x security patches). zod stays on v3
(v4 breaks the MCP SDK). Re-run `npm audit --omit=dev` and `pip-audit` periodically.

## Agentic-deployment guidance

Every tool runs with the PAT's full authority and the agent supplies its own confirmations, so an
injected agent is the realistic threat. Mitigations: least-privilege PAT + DB role; `delete_content`
needs `confirm=true`; elevated permission grants need `confirmElevated=true`; `overwrite` defaults
false; publishing always requires an explicit project (never Default). Operators wanting a
publish-only posture can avoid wiring `delete_content` / `set_permissions` into their client.

## Prompt-driven authoring — added surface

**Audited:** 2026-06-24 · branch `feat/prompt-driven-authoring` · STRIDE + dependency/secret/input
review of the new feature only (baseline findings F-01..F-12 above are unchanged and still hold).

### New surface
- Tools (TS): `create_datasource_from_file`, `design_dashboard`, `build_from_plan`
  (`src/tools/*.ts`), pure planner (`src/planner/{fields,marks,audience,questions,schema,plan}.ts`).
- Sidecar client methods `buildDatasourceFromFile` / `buildDashboardWorkbook` (`src/sidecar.ts`).
- Sidecar routes `POST /datasource/from-file`, `POST /workbook/dashboard` (`sidecar/server.py`),
  multi-format ingest `file_to_dataframe` (`sidecar/hyper_builder.py`), dashboard XML
  (`sidecar/twb_builder.py`).
- New Python deps: `openpyxl` (xlsx), `pyarrow` (parquet, via pantab); no new npm deps.

### Trust-boundary note (local file read)
`create_datasource_from_file` / `/datasource/from-file` read **any** file the MCP-server process
can read — there is no allowed-roots / traversal / symlink restriction; the only check is
`Path.is_file()` (`hyper_builder.py:115`). This is **by design and matches the existing `csvPath`
posture (F-10)**: the agent already runs with the PAT's full authority and is trusted to name
paths. The realistic threat is therefore a *prompt-injected* agent, not the path mechanism itself.
The path is **not** written to any log (the ingest path has no logger) and only appears in a `400`
`detail` returned to the same agent that supplied it — no cross-tenant disclosure. Reading a hostile
*content* file (parsing) is covered by PA-2 below.

### STRIDE summary (new surface)

| Threat | Result |
|---|---|
| Spoofing | Mitigated — both new routes sit behind the same global `token_guard` middleware (127.0.0.1 bind + per-spawn `X-Sidecar-Token`, `hmac.compare_digest`). No route opts out. |
| Tampering | Mitigated — all user strings (field names, sheet/dashboard titles, datasource name) are written via ElementTree text/attribute nodes, which escape `< > & "`; verified well-formed under injection input. No string-concatenated XML in the new builder. |
| Repudiation | Unchanged — Tableau Cloud records the publishing user; `build_from_plan` is the only side-effecting new tool. |
| Information disclosure | Mitigated — no secrets in the new code; file paths not logged; sidecar error bodies are agent-supplied paths only. |
| Denial of service | Mitigated — `file_to_dataframe` enforces a pre-read `MAX_FILE_BYTES` size cap and a `max_rows` clamp (PA-1, fixed); loopback+token-gated, so the attacker is a trusted/injected agent. |
| Elevation of privilege | Mitigated — `build_from_plan` still routes publishing through `resolveProjectId`, which rejects empty and `Default` projects; `overwrite` defaults `false`; PAT/sidecar token never logged. `design_dashboard` is pure (no sidecar/REST calls). |

### Findings & remediation status (new surface)

| ID | Sev | Finding | Status |
|---|---|---|---|
| PA-1 | MED | `file_to_dataframe` (xlsx/parquet/json/csv) loaded the entire file into memory with no pre-read size cap and no `max_rows` cap — a hostile/huge file an injected agent points at could exhaust sidecar memory. | ✅ **Fixed** — `file_to_dataframe` now enforces a pre-read `MAX_FILE_BYTES` cap (raises a clean `ValueError`) and a `max_rows` clamp mirroring `query_to_dataframe`; the `/datasource/from-file` route returns a clean 4xx on oversize. Tested in `sidecar/tests/test_hyper_builder_formats.py` + `test_server_new_routes.py`. |
| PA-2 | LOW | Untrusted-file parsing via `openpyxl` / `pyarrow` / `pandas.read_json`. No known RCE/unsafe-deser path at the pinned versions; openpyxl reads xlsx as data (no macro execution), pyarrow parquet read is data-only. Residual risk is the DoS already tracked as PA-1. | ⚠️ Accepted — versions current; revisit if a parser CVE lands. |
| PA-3 | MED | Transitive `starlette==0.41.3` (via `fastapi==0.115.6`) carried 8 advisories (PYSEC-2026-161, CVE-2025-54121, CVE-2025-62727, CVE-2026-48818, CVE-2026-48817, CVE-2026-54283, CVE-2026-54282). None was reachable here (no `StaticFiles`/`FileResponse`/`HTTPEndpoint`/`request.url`/form parsing; token guard keys off the header; loopback-only). | ✅ **Fixed** — `fastapi` bumped to `0.121.0`, pulling `starlette==0.49.3` (≥ 0.49.1), clearing all 8 advisories; `uv.lock` regenerated. |
| PA-4 | LOW | `openpyxl>=3.1.5` was the only non-`==` pin in `pyproject.toml`; the floor allowed drift on the next `uv lock`. | ✅ **Fixed** — pinned to `openpyxl==3.1.5` for parity with the rest of the manifest. |
| PA-5 | PASS | XML injection into `.twb`/`.tds`: field names, sheet/dashboard titles, and the `<rows>`/`<cols>` text nodes are all ElementTree-escaped (verified: injected `<script>`, `">`, `&` round-trip as `&lt;`/`&quot;`/`&amp;`, output parses). | ✅ Verified clean |
| PA-6 | PASS | Guardrail preservation: `build_from_plan` and `create_datasource_from_file` both publish through `resolveProjectId` (rejects empty + `Default`); `overwrite` defaults `false` in all three new tool schemas; `design_dashboard` performs no sidecar/REST calls. | ✅ Verified clean |
| PA-7 | PASS | Token guard on new routes: `token_guard` is global FastAPI middleware applied to every request incl. `/datasource/from-file` and `/workbook/dashboard`; 127.0.0.1 bind; constant-time token compare. No new route bypasses it. | ✅ Verified clean |
| PA-8 | PASS | Secret scan of the new surface (planner + new tools + new sidecar code): no hardcoded credentials; PAT and sidecar token never logged on the new paths. `npm audit --omit=dev` → 0 vulns (no new npm deps). | ✅ Verified clean |

### Recommended fixes for HIGH/CRITICAL
None. There were **no CRITICAL or HIGH findings** on the new surface. The two MEDIUM defense-in-depth
items (PA-1, PA-3) and the LOW pin (PA-4) have since been **remediated** (see status column above):

- **PA-1** — `file_to_dataframe` now enforces a pre-read `MAX_FILE_BYTES` cap and a `max_rows` clamp
  mirroring `query_to_dataframe`; the `/datasource/from-file` route returns a clean 4xx on oversize.
- **PA-3** — `fastapi` bumped to `0.121.0` (pulls `starlette==0.49.3`), clearing all 8 advisories;
  `uv.lock` regenerated.
- **PA-4** — `openpyxl` pinned to `==3.1.5`.

> Not applicable: there is no blockchain / smart-contract / web3 component, so the
> `smart-contract-audit` skill was not used.

## Vibe-BI expansion surface (E0–E4) — added surface

**Audited:** 2026-07-17 · branch `feat/exec-dashboards` (slice `db658b2..HEAD`) · STRIDE +
dependency/secret/input review of the branch-new surface only. Baseline (F-01..F-12) and the
prompt-authoring add-on (PA-1..PA-8) are unchanged and still hold.
**Result:** 0 CRITICAL · 0 HIGH · 1 MEDIUM · 2 LOW · 9 PASS. Shippable; VB-02 remediated in-branch.

### New surface
- E0 Foundation: `src/rest/{errors,retry,vds}.ts`, `get_datasource_fields`.
- E1 Branding: `src/branding/{schema,load,builderBrand}.ts`, `validate_brand`, sidecar brand block.
- E2 Automation: `src/rest/{schedules,webhooks,credentials}.ts`, `create_live_datasource`,
  schedule/webhook tools, sidecar `/datasource/live` + `tds_builder.build_live_tds`, and the
  operator CLIs `scripts/{generate-cron,cronTemplates,refresh-local}.ts`.
- E3 Pulse: `src/rest/pulse.ts` + Pulse tools (JSON bodies, `/api/-/pulse/*`).
- E4 Stories: sidecar story models + `twb_builder` storyboard emit.
- New npm dep: `yaml@2.9.0` (only).

### STRIDE (expansion surface)
| Threat | Result |
|---|---|
| Spoofing | Mitigated — new sidecar routes behind the same global `token_guard` (127.0.0.1 + `hmac.compare_digest`); no opt-out. |
| Tampering | Mitigated — new `tsRequest` builders `xmlEscape`/enum/regex-bound all inputs; brand/story reach `.twb` via ElementTree + hex-validated colors. |
| Repudiation | Unchanged — Cloud records the acting user. |
| Information disclosure | Mitigated — embedded DB password never logged (request body never logged), never persisted, absent from the live `.tds`; new error paths redact body, never echo the token. |
| Denial of service | Mitigated — date-coercion + CSV sniff respect the 500 MB / 1 M-row caps; sniff reads only 64 KB. |
| Elevation of privilege | Mitigated — new deletes require `confirm=true`; live publish rejects empty/`Default` project; `overwrite` defaults false. |

### Findings & remediation status
| ID | Sev | Finding | Status |
|---|---|---|---|
| VB-01 | PASS | Embedded publish credentials never logged (incl. error + retry paths — only the response body hits stderr), `xmlEscape`d, never persisted, absent from the live `.tds`; `tests/secrets.test.ts` asserts the DB password is absent on success and on a 400. | ✅ Verified clean |
| VB-02 | MED | Shell injection in the generated crontab line: `buildCrontabLine` wrapped `file`/`name`/`project`/`persona` in double quotes only, which does not neutralize `$(…)`/backticks/`$VAR`/`"`. Operator-only CLI (not an MCP tool), generate-only, manual install required — not agent-reachable. | ✅ **Fixed in-branch** — all interpolations POSIX single-quote-escaped (`shellQuote` in `scripts/cronTemplates.ts`); adversarial-name tests added (`$(id)`, quote-breakout, embedded quotes). Launchd plist already safe (exec-array + `xmlEscape`). |
| VB-03 | LOW | DB credentials pass as `create_live_datasource` tool args, so they transit the agent/LLM context / client tool-call transcript (outside this server's control). Mitigated by env-var guidance in the tool description. | ⚠️ Accepted — inherent to the feature; consider a future env-var-name indirection. |
| VB-04 | PASS | Webhook HTTPS-only not bypassable: `startsWith("https://")` after `z.url()`; uppercase scheme and leading whitespace are rejected (fail-closed). | ✅ Verified clean |
| VB-05 | PASS | XML injection across all new `tsRequest` builders (schedules/webhooks/credentials/publish) — escaped or enum/regex-bound. Pulse/VDS use `JSON.stringify`. | ✅ Verified clean |
| VB-06 | PASS | `confirm=true` gate on `delete_refresh_schedule` / `delete_webhook` / `delete_pulse_definition`. | ✅ Verified clean |
| VB-07 | PASS | Error-body redaction + no token leakage on new REST/VDS/Pulse error paths. | ✅ Verified clean |
| VB-08 | PASS | `yaml@2.9.0` safe-by-default (no code exec); brand values hex-validated + ElementTree-escaped into XML. | ✅ Verified clean |
| VB-09 | LOW | `brandPath` arbitrary local-file read + YAML alias-expansion DoS. Read is by-design (matches F-10); alias DoS bounded by yaml's default `maxAliasCount`. | ⚠️ Accepted / documented |
| VB-10 | PASS | Ingest date coercion runs after the row clamp; CSV sniff reads only 64 KB; 500 MB pre-read cap enforced. No DoS regression vs PA-1. | ✅ Verified clean |
| VB-11 | PASS | Sidecar new routes behind the global constant-time token guard; Pydantic-validated; 400s echo only agent-supplied fields; live `.tds` carries no credentials. | ✅ Verified clean |
| VB-12 | PASS | `npm audit --omit=dev` → 0 vulnerabilities; `yaml@2.9.0`/`undici@7.28.0`/`zod@3.25.76`/SDK pinned & clean; no sidecar Python-dep changes. | ✅ Verified clean |

### Recommended fixes for HIGH/CRITICAL
None — no CRITICAL/HIGH on the expansion surface. VB-02 (MEDIUM) was remediated in-branch
before merge.

> Not applicable: no blockchain / smart-contract / web3 component, so the `smart-contract-audit`
> skill was not used.

## Design-excellence expansion surface (D0–D7) — added surface

Delta audit over `feat/design-excellence` (`5e4f2ff..41e1ea4`): design corpus miner, Tableau
Public reference downloads, runtime theme loading, themed XML emission (zone styles, chrome,
KPI BAN customized-labels via CDATA sentinels, palettes, actions).

### New surface
- `sidecar/design_miner.py` — offline dev-only CLI parsing untrusted downloaded `.twb`/`.twbx`.
- `design/references/` download workflow (public.tableau.com over HTTPS, fixed URL template).
- `src/design/loadThemes.ts` — runtime YAML read of `design/corpus/themes/*.yaml` (zod-validated).
- Theme/brand values and worksheet titles flowing into emitted workbook XML.
- Dashboard `<actions>` built from worksheet titles (D7).

### STRIDE (expansion surface)
- **Tampering/Info disclosure (miner inputs):** `.twbx` members validated against zip-slip
  (`_is_unsafe_member`: leading `/`\\, `..` segments) BEFORE any read; extraction is
  **in-memory only** — nothing is ever written to disk (`read_twb_bytes`). XXE/billion-laughs
  blocked (`resolve_entities=False, no_network=True, load_dtd=False`).
- **DoS (miner):** `huge_tree=True` removes lxml depth/size ceilings and there is no zip size
  cap — a crafted archive can exhaust memory. Accepted: offline, dev-extras-only tool run
  manually against operator-chosen files; never reachable from the MCP server (DX-01, LOW).
- **Injection (theme → XML):** theme colors/names and worksheet titles are emitted via
  ElementTree, which escapes attribute and text content; no path builds XML by string
  concatenation. The KPI CDATA sentinel content is restricted to slugged field refs
  (`_slug` → `[A-Za-z0-9_]`), so `]]>` breakout is impossible; a column literally named with
  the sentinel string could only truncate its own label cosmetically (DX-02, INFO).
- **Spoofing/Repudiation (downloads):** fixed `https://public.tableau.com/workbooks/<name>.twb`
  template, no user-controlled URL interpolation beyond the workbook slug; artifacts recorded
  with sha256 in `design/references/README.md` and corpus provenance.
- **Info disclosure (fail-soft):** theme-load failures write a one-line message to stderr
  (server log), never into MCP tool output; message contains at most a local path (DX-03, INFO).

### Findings & remediation status
| ID | Severity | Finding | Status |
|---|---|---|---|
| DX-01 | LOW | No size cap on miner zip/XML inputs (`huge_tree=True`); memory exhaustion possible from crafted archives | Accepted — offline dev-only CLI; document-only |
| DX-02 | INFO | CDATA sentinel collision via a column named with the literal marker string truncates that label cosmetically; no structural injection possible (slug-restricted content, ET escaping elsewhere) | Accepted |
| DX-03 | INFO | Fail-soft stderr messages may include local corpus paths | Accepted — stderr only, local server |
| DX-04 | INFO | Theme hex colors are not format-validated (any string becomes an ET-escaped attribute); malformed values degrade rendering only | Accepted — corpus is repo-reviewed; zod/Pydantic accept strings by design |

### Recommended fixes for HIGH/CRITICAL
None — no CRITICAL/HIGH on the design-excellence surface. 0 CRITICAL · 0 HIGH · 0 MEDIUM ·
1 LOW · 3 INFO.

> Process note: during the D4 render bisect, the builder agent was granted one-time bounded
> publish authority (12 publishes, two named probe workbooks only, creds via env, sign-out
> enforced); usage was fully reported (11+1 of 12) and is recorded in the session handoff.
