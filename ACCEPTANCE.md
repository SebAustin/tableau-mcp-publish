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
| 7 | live publish: datasource opens + workbook renders ≥1 mark (gated demo) | ✅ | `npm run demo` succeeded 2026-06-24 — URLs below; manual mark screenshot still optional |

## Security

STRIDE + code/dependency review (`SECURITY.md`): **0 CRITICAL, 0 HIGH**. All 4 MEDIUM findings
remediated (error-body redaction, multipart header sanitization, SQL least-privilege documented,
elevated-capability gate). Quick LOWs fixed (constant-time token compare, psycopg keyword args,
log/`.npmignore` hygiene, csv regular-file check). `undici` bumped to 7.28.0.

## MCP integration test (2026-06-24)

Agency smoke-test of the **Cursor-configured** `tableau` and `tableau-publish` MCP servers.

| Check | Result | Notes |
|---|---|---|
| Cursor MCP servers connected | ❌ | Both `user-tableau` and `user-tableau-publish` report **errored**; neither appears in the agent tool registry this session. |
| `@tableau/mcp-server` package resolves | ✅ | `npx -y @tableau/mcp-server@latest` downloads v2.18.0; fails fast when `SERVER` is unset. |
| `tableau-mcp-publish` on npm | ❌ | **404 — not published yet.** Cursor config `npx -y tableau-mcp-publish@latest` cannot start until npm publish (gated in DEPLOYMENT.md). |
| Local `tableau-mcp-publish` startup | ✅ | `node dist/index.js` signs in, starts the Python sidecar, and serves stdio when credentials are valid; returns **401** with an invalid PAT (expected). |
| Unit + sidecar test suite | ✅ | `make ci` green locally (28 TS + 18 Python tests). |
| Live MCP tool calls | ⏳ blocked | No `.env` in repo; `PAT_VALUE` in `~/.cursor/mcp.json` is the literal placeholder `…`. |

**Remediation to get both MCP servers green in Cursor:**

1. **`tableau-publish`** — point at the local build until npm publish:
   ```json
   "tableau-publish": {
     "command": "node",
     "args": ["/Users/sebastienhenry/Documents/Projects/Tableau MCP Publish/dist/index.js"],
     "env": { "SERVER": "…", "SITE_NAME": "…", "PAT_NAME": "…", "PAT_VALUE": "<real PAT secret>" }
   }
   ```
   Run `npm install && npm run build && cd sidecar && uv sync` once so `dist/` and the sidecar venv exist.

2. **`tableau`** — keep `npx -y @tableau/mcp-server@latest`; replace `PAT_VALUE: "…"` with the real PAT secret.

3. **Restart** both servers in Cursor Settings → MCP, then run:
   ```bash
   cp .env.example .env   # fill in real values
   npm run test:mcp-smoke
   ```

**Verdict:** local codebase **SOLID**; live MCP integration **FIX** until config + credentials are corrected.

## Live demo (criterion 7)

Executed 2026-06-24 against site `sebaustin`, project `agentic-bi-copilot`:

```bash
# .env must include SERVER, SITE_NAME, PAT_NAME, PAT_VALUE, DEMO_PROJECT
npm run demo -- examples/top_customers.csv
```

- Datasource: https://10ax.online.tableau.com/#/site/sebaustin/datasources/25884038
- Workbook: https://10ax.online.tableau.com/#/site/sebaustin/workbooks/2414706

Both live in the **agentic-bi-copilot** project (not Default). LUIDs for API/MCP use:
`4b1d6b72-244b-47f2-b43d-e003280d2d9a` (datasource) and `b26bd287-1a1e-4346-a2e7-9fcc86449cfc` (workbook).

Open the workbook in Cloud to confirm the revenue-by-region bar mark renders.

## Built / deferred / next

- **Built:** 11 authoring + lifecycle tools; two-layer architecture; single + chunked publish;
  `.hyper`/`.tdsx`/`.twb` generation; 46 tests; CI matrix; full docs; SECURITY.md; 3 seeded issues.
- **Deferred (non-goals for v0.1):** read/query tools (use the official server); live-connection
  datasources (issue #2); metadata-driven sheet suggestions (issue #3); map marks (experimental).
- **Next:** flip the repo public; open the upstream discussion (issue #1); publish to npm (gated); restart Cursor MCP servers after `.env` is set.
