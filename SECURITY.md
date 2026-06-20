# SECURITY.md

**Audit date:** 2026-06-20 · **Method:** STRIDE decomposition + manual code/dependency review.
**Result:** 0 CRITICAL · 0 HIGH · 4 MEDIUM · 7 LOW · 1 PASS. The core safety controls (PAT never
logged, stderr-only logging, loopback+token sidecar, confirm gate on delete, capability allowlist,
no silent overwrite / no Default-project publish) are present and effective.

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
| F-08 | LOW | `hyperd.log` written into the source tree, shipped in npm `files` | ✅ Fixed — removed + `.npmignore` excludes logs/tests/caches |
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

> Not applicable: there is no blockchain / smart-contract / web3 component, so the
> `smart-contract-audit` skill was not used.
