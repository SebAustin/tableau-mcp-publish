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
| Denial of service | Partially mitigated — `file_to_dataframe` reads the whole file into memory with **no row/size cap and no zip-bomb guard** (PA-1). Loopback+token-gated, so the attacker is a trusted/injected agent. |
| Elevation of privilege | Mitigated — `build_from_plan` still routes publishing through `resolveProjectId`, which rejects empty and `Default` projects; `overwrite` defaults `false`; PAT/sidecar token never logged. `design_dashboard` is pure (no sidecar/REST calls). |

### Findings & remediation status (new surface)

| ID | Sev | Finding | Status |
|---|---|---|---|
| PA-1 | MED | `file_to_dataframe` (xlsx/parquet/json/csv) loads the entire file into memory with no pre-read size cap, no `max_rows` cap (unlike `query_to_dataframe`), and no decompression-bomb guard for xlsx (zip) — a hostile/huge file an injected agent points at can exhaust sidecar memory. | ⚠️ **Needs-fix (recommended)** — loopback + token gate the blast radius to the local agent, so not shippable-blocking, but cheap to harden. See fix below. |
| PA-2 | LOW | Untrusted-file parsing via `openpyxl` / `pyarrow` / `pandas.read_json`. No known RCE/unsafe-deser path at the pinned versions; openpyxl reads xlsx as data (no macro execution), pyarrow parquet read is data-only. Residual risk is the DoS already tracked as PA-1. | ⚠️ Accepted — versions current; revisit if a parser CVE lands. |
| PA-3 | MED | Transitive `starlette==0.41.3` (via `fastapi==0.115.6`) carries 8 advisories (PYSEC-2026-161, CVE-2025-54121, CVE-2025-62727, CVE-2026-48818, CVE-2026-48817, CVE-2026-54283, CVE-2026-54282). **None is reachable here:** the sidecar uses no `StaticFiles`/`FileResponse`/`HTTPEndpoint`/`request.url`/form-urlencoded parsing, and the token guard keys off the `X-Sidecar-Token` header (not `request.url.path`), so the host/path-confusion auth-bypass does not bypass it. Bind is loopback-only, defeating the "unauthenticated remote attacker" precondition. | ⚠️ **Needs-fix (hygiene)** — bump `fastapi` to pull patched starlette (≥ 0.49.1 covers the reachable-by-class items; latest covers all). Not exploitable in this deployment; not shippable-blocking. |
| PA-4 | LOW | `openpyxl>=3.1.5` is the only non-`==` pin in `pyproject.toml`; the lock resolves to 3.1.5, so reproducibility holds today, but the floor allows drift on the next `uv lock`. | ⚠️ Accepted — pin to `==3.1.5` for parity with the rest of the manifest when convenient. |
| PA-5 | PASS | XML injection into `.twb`/`.tds`: field names, sheet/dashboard titles, and the `<rows>`/`<cols>` text nodes are all ElementTree-escaped (verified: injected `<script>`, `">`, `&` round-trip as `&lt;`/`&quot;`/`&amp;`, output parses). | ✅ Verified clean |
| PA-6 | PASS | Guardrail preservation: `build_from_plan` and `create_datasource_from_file` both publish through `resolveProjectId` (rejects empty + `Default`); `overwrite` defaults `false` in all three new tool schemas; `design_dashboard` performs no sidecar/REST calls. | ✅ Verified clean |
| PA-7 | PASS | Token guard on new routes: `token_guard` is global FastAPI middleware applied to every request incl. `/datasource/from-file` and `/workbook/dashboard`; 127.0.0.1 bind; constant-time token compare. No new route bypasses it. | ✅ Verified clean |
| PA-8 | PASS | Secret scan of the new surface (planner + new tools + new sidecar code): no hardcoded credentials; PAT and sidecar token never logged on the new paths. `npm audit --omit=dev` → 0 vulns (no new npm deps). | ✅ Verified clean |

### Recommended fixes for HIGH/CRITICAL
None. There are **no CRITICAL or HIGH findings** on the new surface. The two `Needs-fix` items are MEDIUM
defense-in-depth and are not shippable-blocking given the loopback + per-spawn-token boundary.

Suggested hardening (for the builder, not applied here):
- **PA-1** — in `sidecar/hyper_builder.py::file_to_dataframe`, add a pre-read size guard
  (`Path(path).stat().st_size` vs a configurable byte cap) and a post-read
  `df = df.head(DEFAULT_MAX_ROWS)` clamp mirroring `query_to_dataframe`; for csv/json, pass
  `nrows` / chunk where the reader supports it.
- **PA-3** — bump `fastapi` in `sidecar/pyproject.toml` to a release that resolves
  `starlette >= 0.49.1` (latest fastapi pulls a fully-patched starlette), then `uv lock` and re-run
  `pip-audit`.
- **PA-4** — change `openpyxl>=3.1.5` to `openpyxl==3.1.5` for manifest parity.

> Not applicable: there is no blockchain / smart-contract / web3 component, so the
> `smart-contract-audit` skill was not used.
