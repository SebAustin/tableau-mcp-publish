# ACCEPTANCE.md

Acceptance record for `tableau-mcp-publish` v0.1.

## Verification commands (all green locally + in CI)

| Layer | Command | Result |
|---|---|---|
| TypeScript | `npm run build` / `npm run lint` / `npm test` | build clean · lint 0 · **28 tests pass** |
| Python sidecar | `uv run ruff check .` / `mypy --strict .` / `pytest -q` | ruff clean · mypy clean · **18 tests pass** |
| CI | GitHub Actions matrix (Node 22.x/24.x/26.x, Python 3.12/3.13) | **green** — run [27881730517](https://github.com/SebAustin/tableau-mcp-publish/actions/runs/27881730517) |
| Security | prod `npm audit --omit=dev` | **0 vulnerabilities** |

## Success criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | build clean; lint + test pass | ✅ | `tsc` green, eslint 0, vitest 28/28 |
| 2 | sidecar ruff + mypy --strict + pytest pass | ✅ | all three clean, pytest 18/18 |
| 3 | single (≤64MB incl. exactly 64MB) vs chunked (>64MB) + mid-stream abort | ✅ | `tests/restClient.test.ts` — strategy boundary, 3-chunk split, "never finalizes" abort test |
| 4 | `.hyper` round-trip; `.tdsx` zip validity; `.twb` structural binding | ✅ | `sidecar/tests/test_hyper_builder.py`, `test_tds_builder.py`, `test_twb_builder.py` |
| 5 | 11 tools w/ descriptions+schemas; publish requires explicit non-Default project, overwrite=false; delete needs confirm; perms allowlist + elevated gate; **PAT never logged (asserted)** | ✅ | `tests/tools.test.ts` (incl. Default-delete refusal, elevated-capability gate), `tests/secrets.test.ts` (PAT log-capture), `resolveProjectId` rejects empty + "Default" |
| 6 | CI green on the version matrix | ✅ | run 27881730517 — 5/5 jobs success |
| 7 | live publish: datasource opens + workbook renders ≥1 mark (gated demo) | ⏳ pending | requires a Dev-site PAT; run `npm run demo`. Recorded below once executed. |

## Security

STRIDE + code/dependency review (`SECURITY.md`): **0 CRITICAL, 0 HIGH**. All 4 MEDIUM findings
remediated (error-body redaction, multipart header sanitization, SQL least-privilege documented,
elevated-capability gate). Quick LOWs fixed (constant-time token compare, psycopg keyword args,
log/`.npmignore` hygiene, csv regular-file check). `undici` bumped to 7.28.0.

## Live demo (criterion 7) — gated

Authorized by the owner; runs against a Tableau Developer Program site with a PAT supplied at run
time (never logged; `.env` gitignored):

```bash
export SERVER=… SITE_NAME=… PAT_NAME=… PAT_VALUE=… DEMO_PROJECT="<existing project>"
npm run demo -- examples/top_customers.csv
```

**Status: deferred by owner.** The owner chose to skip the live run during this build; the demo is
ready to execute against a Dev site at any time. Record the datasource URL, workbook URL, and a
screenshot confirming the revenue-by-region bar mark here once run.

## Built / deferred / next

- **Built:** 11 authoring + lifecycle tools; two-layer architecture; single + chunked publish;
  `.hyper`/`.tdsx`/`.twb` generation; 46 tests; CI matrix; full docs; SECURITY.md; 3 seeded issues.
- **Deferred (non-goals for v0.1):** read/query tools (use the official server); live-connection
  datasources (issue #2); metadata-driven sheet suggestions (issue #3); map marks (experimental).
- **Next:** run the gated live demo; flip the repo public; open the upstream discussion (issue #1);
  publish to npm (gated).
