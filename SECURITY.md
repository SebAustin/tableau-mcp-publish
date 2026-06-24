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
