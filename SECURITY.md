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

## Top-100 corpus expansion surface (T0–T3) — added surface

Delta audit over `feat/design-excellence` (`f75f46b..6aba1fa`): the top-100 Tableau Public
corpus acquisition + mining pipeline. Read-only review; STRIDE-lite. All three input/output
files are **dev-only** — none is imported by `server.py`, `twb_builder.py`, or any MCP runtime
path. Prior E0–E4 (VB-*) and D0–D7 (DX-*) sections stand at 0 CRITICAL / 0 HIGH.

### New surface
- `scripts/fetch-top100.ts` — mass download from the unauthenticated public "Viz of the Day"
  BFF feed + `public.tableau.com/workbooks/<slug>.twb` endpoint (undici, no auth/cookies).
- `sidecar/design_dashboard_miner.py` — dashboard/story mining of untrusted `.twb`/`.twbx`,
  reusing `design_miner.safe_parser` (XXE-safe) + `read_twb_bytes` (zip-slip-guarded, in-memory).
- `sidecar/design_stats.py` — corpus aggregation to committed `design/corpus/stats/*.yaml`;
  reads `manifest.yaml` (`repoUrl` → on-disk path) and appends notable constructs to recipes.
- Committed artifacts: `design/references/top100/manifest.yaml` (provenance keyed by opaque
  `WB-NNN` identifiers post source-identity-redaction — see TC-03), `design/corpus/stats/*.yaml`,
  `design/corpus/GAPS.md`.
- Planner `src/planner/plan.ts` — title auto-shorten + v2 story-caption derivation.

### STRIDE (expansion surface)
- **Spoofing/Repudiation (downloads):** fixed HTTPS host `public.tableau.com`; no auth/cookies;
  download path segment is `encodeURIComponent(repoUrl)` so `/`, `?`, `#` cannot inject a host or
  extra path — SSRF via host injection is not possible. Body validated by magic bytes (PK zip /
  `<?xml`), never `content-type`. Each artifact recorded with sha256 in the committed manifest;
  stats output carries no wall-clock, so re-runs are byte-reproducible and auditable.
- **Tampering (feed → filesystem path):** the one real gap. `workbookRepoUrl` from the untrusted
  feed is interpolated verbatim into `resolve(OUT_DIR, `${repoUrl}.${ext}`)` → `writeFileSync`
  (write) and `top100_dir / f"{repo_url}.twbx"` → read+parse, with **no charset/containment
  check**. A `../`/absolute value would escape the target dir (TC-01). Zip-slip on the parse side
  IS guarded (`_is_unsafe_member`), and XXE is off (`resolve_entities/load_dtd=False`,
  `no_network=True`).
- **Info disclosure:** committed files limited to public "Viz of the Day" attribution (TC-03) +
  aggregated stats/citations (`source`/`sha256`/`xpath` only — TC-04). Raw `.twb`/`.twbx` inputs
  are gitignored (`*.twb`, `*.twbx`) and never committed. No secrets found on the new surface.
- **DoS:** fetcher enforces per-file 25MB (aborted mid-stream, nothing saved), 1.5GB run cap,
  60s header+body timeouts, 1.1s politeness between every request. Miner reuses `huge_tree=True`
  with no independent size cap (TC-02), bounded in practice by the fetcher's 25MB cap.
- **Elevation:** no `eval`/`exec`/`subprocess`/`os.system`/`pickle` in the new sidecar modules
  (verified); no credentials; YAML read via `yaml.safe_load` (Python) and the `yaml` package's
  non-executing `parse` (TS) throughout; deps pinned exact (`undici@7.28.0`, `yaml@2.9.0`,
  `zod@3.25.76`, `tsx@4.19.2`, dev-only).

### Findings & remediation status
| ID | Severity | Finding | Status |
|---|---|---|---|
| TC-01 | MEDIUM | Feed-controlled `workbookRepoUrl` reaches filesystem paths with no charset/containment guard — write sink `scripts/fetch-top100.ts` (`resolve(OUT_DIR, …)`→`writeFileSync`) allows traversal outside `OUT_DIR`; read sink `design_stats.py top100_paths` selects an out-of-tree file to read+parse. Mitigations: dev-only, never in the MCP runtime; HTTPS to Tableau's own BFF (MITM needs a cert break); output extension forced to `.twbx`/`.twb` and body must pass PK/`<?xml` magic; 25MB/1.5GB caps; read side is in-memory + XXE-safe + zip-slip-guarded; all 134 currently-committed `repoUrl`s are safe `[A-Za-z0-9._-]` slugs. | OPEN — recommend fix before next corpus regeneration; does not block runtime ship. Not fixed here (task scope: read-only + this SECURITY.md append). Fix: validate `workbookRepoUrl` against `^[A-Za-z0-9._-]+$` at the feed-parse boundary and assert the resolved write path stays under `OUT_DIR`; apply the same guard when reading `repoUrl` from the manifest. |
| TC-02 | LOW | `huge_tree=True` + no independent zip/XML size cap on the T1 dashboard/story miner (via reused `read_twb_bytes`/`safe_parser`); a crafted/oversized workbook can exhaust memory. Same root as DX-01, extended to the new miner. | Accepted — offline dev-only tool run against operator-chosen files; bounded by the fetcher's 25MB per-file cap; never reachable from the MCP server. |
| TC-03 | INFO | ~~Committed `manifest.yaml` embeds third-party author display names + public profile slugs (`author`/`profileName`), titles, view counts.~~ **RESOLVED** (source-identity redaction pass): `author`/`profileName`/`title` are no longer written to the committed manifest — every entry is keyed by an opaque, stable `WB-NNN` identifier instead of the real `repoUrl`/author metadata (see `design/corpus/SCHEMA.md`'s provenance section). `sha256`/`bytes`/`status`/`curatedAt`/`viewCount`/`favorites` remain (non-identifying, technical provenance). | Resolved — no third-party PII remains in the committed corpus; `sha256` is the sole re-identification-free integrity anchor. |
| TC-04 | INFO | Committed `design/corpus/stats/*.yaml` derive from untrusted workbooks. | Accepted — content is aggregated numbers + `source`/`sha256`/`xpath` citations only (no author PII, no raw workbook content); consumers use `yaml.safe_load` / `yaml` `parse`; raw inputs gitignored. No injection path into tests. |
| TC-05 | INFO | Planner title auto-shorten (`shortenDashboardTitle`/`truncateAtWordBoundary`) + v2 story captions produce question-derived strings flowing into `dashboardTitle`/`dashboardSubtitle`/`storyArc[].caption`. | Accepted — these reach the existing `xml.etree.ElementTree` `.text` sinks in `twb_builder.py` (auto-escapes `< > &`); no new raw-XML/string-concat sink (auto-shorten only splits one existing string across the existing title+subtitle fields). Escaping verified. |

### Recommended fixes for HIGH/CRITICAL
None — no CRITICAL/HIGH on the T0–T3 surface. 0 CRITICAL · 0 HIGH · 1 MEDIUM · 1 LOW · 3 INFO.
The single MEDIUM (TC-01) is dev-tooling defense-in-depth for the offline corpus-acquisition
script; it is not part of the shipped MCP-server attack surface. Recommend adding the `repoUrl`
charset+containment guard before the next corpus regeneration.

## N-phase surface (external skill assimilation) — added surface

Delta audit over `feat/design-excellence` (`6109a7d..75b45ef`): three new read-only MCP tools
(`critique_dashboard`, `generate_metric_dictionary`, `scan_governance`) plus one sidecar
number-format change (`twb_builder.py` N4). Read-only review; STRIDE-lite. Prior VB-*/DX-*/TC-*
sections stand at 0 CRITICAL / 0 HIGH; this surface holds that line.

### New surface
- `src/tools/critiqueDashboard.ts` + `src/planner/critique.ts` — reads the committed
  `design/corpus/stats/visual_norms.yaml` at the tool boundary, then deterministically scores a
  caller-supplied `plan` object (no LLM; A-01 preserved). Pure scorer.
- `src/tools/metricDictionary.ts` + `src/planner/metricDict.ts` — reuses the existing VDS field
  fetch (`ctx.rest.getDatasourceFields`) and derives role / aggregation / format / a definition
  template per field. Pure derivation; no new external call surface.
- `src/tools/scanGovernance.ts` + `src/governance/checks.ts` — read-only pass over the existing
  `listContent()` surface flagging stale / Default-project / naming issues. Caller may supply a
  `namingPattern` regex source; clock is injected (`nowIso`), scanner is a pure function.
- `sidecar/twb_builder.py` N4 — `_kpi_delta_arrow_format` gates a brand-semantic-color number
  format behind `delta_color_by_sign`; the bracketed-color construct was live-REFUTED and is now
  arrow-only (colors are hex from `brand.yaml`; no user-free-text sink).

### STRIDE (N-phase surface)
- **Spoofing/Repudiation:** no new auth, network, or identity surface. `metric_dictionary` reuses
  the already-audited VDS fetch; `scan_governance` reuses `listContent()`; `critique_dashboard`
  makes no network call (`ctx` is explicitly voided — norms come from the committed corpus).
- **Tampering:** `critique_dashboard` reads a **fixed repo-relative** path
  (`DEFAULT_VISUAL_NORMS_PATH`, resolved from `import.meta.url` like `DEFAULT_BRAND_PATH`) — no
  path segment is taken from caller input, so no path traversal. The YAML is parsed with the
  `yaml` package's non-executing `parse` (v2.9.0, pinned) — not a code-exec loader. The scored
  `plan` is zod-validated (`.passthrough()`) and only structurally read; every use is a property
  read or a template-literal into a JSON output field — no `eval`, no `Function`, no dynamic
  require, no XML/HTML/SQL/shell sink.
- **Info disclosure:** outputs are structured JSON derived from the caller's own plan, the
  datasource's own field metadata, or the site's own content list. Field names and content names
  flow only into structured output strings/templates (`definitionTemplate`, finding `detail`) —
  no injection sink and no secret exposure. No secrets on the new surface.
- **DoS:** the one live question — see NX-01. `critique`/`metricDict` are O(n) over
  caller/datasource-sized arrays, bounded by normal MCP message limits. The sidecar change removes
  a construct; it adds no loop.
- **Elevation:** no `eval`/`exec`/`subprocess`/dynamic import; no filesystem write; the sidecar
  path computes but deliberately discards `_nearest_tableau_format_color` and emits a constant.

### Findings & remediation status
| ID | Severity | Finding | Status |
|---|---|---|---|
| NX-01 | LOW | `scan_governance` compiles a caller-supplied `namingPattern` via `new RegExp(namingPattern)` and runs `.test(it.name)` over site content names — a catastrophic-backtracking pattern (e.g. `(a+)+$`) is a ReDoS vector. Realistic assessment: this is a **read-only dev tool**; the *same caller* supplies both the regex and triggers the scan, so a bad pattern is a **self-inflicted stall** of that caller's single-threaded Node event loop, not a cross-tenant DoS. The match input (`it.name`) is site-controlled but length-bounded by Tableau's ~255-char name limit. A genuine two-party exploit needs an attacker who both publishes a name engineered to backtrack *and* gets a benign-looking-but-vulnerable regex used against it — contrived. The default pattern (`/^\s\|\s$\|\s{2,}/`) is linear-safe. | OPEN — **does NOT block ship**. Not fixed here (task scope: read-only + this append). Recommended cheap hardening before promoting this tool beyond dev use: cap `namingPattern` length (e.g. ≤200 chars) in the zod schema, and/or run the match under a bounded budget (worker/`RegExp` timeout or a linear-time engine such as `re2`). Sub-note: an *invalid* pattern throws `SyntaxError` synchronously in the handler → surfaces as a normal rejected tool call, not a server crash — acceptable as-is. |
| NX-02 | INFO | `critique_dashboard` reads `visual_norms.yaml` at the tool boundary. | Accepted — path is fixed/repo-relative (no caller input in the path; `loadBusinessNorms`'s optional `path` arg is never passed by the tool), parse is the non-executing `yaml.parse`, and the file is a committed repo artifact, not runtime-attacker-controlled. |
| NX-03 | INFO | Caller-supplied `plan` object scored by `critiqueDashboardPlan`. | Accepted — zod-validated then only structurally read; template-literal interpolations (`Layout "${archetype}"`, title casing, etc.) land in JSON output fields returned to the caller, not in any executable/markup/query sink. No `eval`. DoS is linear and message-size-bounded. |
| NX-04 | INFO | Datasource field names/captions (external, from VDS) flow into `generate_metric_dictionary` output (`name`, `caption`, `definitionTemplate`). | Accepted — they reach structured JSON output and template strings only; no injection sink. The static `CURRENCY_HINT`/`PERCENT_HINT` regexes are constants (not from input) so carry no ReDoS. Reuses the existing `getDatasourceFields` call — no new external surface. |
| NX-05 | INFO | Sidecar N4 brand-semantic-color → number-format string. | Accepted/refuted — colors are hex from `brand.yaml` (no user free-text), and the risky bracketed-color construct is live-REFUTED and no longer emitted (arrow-only constant). No sink. |

### Recommended fixes for HIGH/CRITICAL
None — no CRITICAL/HIGH on the N-phase surface. **0 CRITICAL · 0 HIGH · 1 LOW · 4 INFO.**
The single LOW (NX-01, `namingPattern` ReDoS) is a self-DoS on a read-only dev tool and is **not a
ship blocker**; add the pattern-length cap (and optionally a bounded-time match) before exposing
`scan_governance` to untrusted or multi-tenant callers.
