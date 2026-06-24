# Tableau Official Ecosystem Review (2026-06-24)

Review of [Tableau's GitHub org](https://github.com/orgs/tableau/repositories) and the
specifically-named [`tableau/tableau-ui`](https://github.com/tableau/tableau-ui), to decide where
`tableau-mcp-publish` should **adopt**, **align**, **document**, or **ignore** — and to drive the
adaptations that follow. Researched via the GitHub API (`gh`) as `SebAustin`. Every factual claim is
cited to a repo + path / release tag.

## Verdict table

| Repo | What it is | State | Verdict | Value |
|---|---|---|---|---|
| `tableau/tableau-mcp` | Official MCP server (read/query + Desktop-local authoring + admin-gated lifecycle) | v2.18.x, very active (release 2026-06-24) | **DOCUMENT** — reframe positioning; do **not** re-architect | H |
| `tableau/tableau-document-schemas` | Official **TWB XSD** (`twb_2026.1.0.xsd`), agent-targeted, machine-validatable | active (push 2026-06-21) | **VALIDATE-AGAINST** — wire into pytest; highest-value hardening | H |
| `tableau/server-client-python` (TSC) | Canonical Python REST client (publish/auth semantics) | active; REST 2.4 auto-negotiated; 64 MB single-publish limit | **ALIGN** (one boundary fix) + DOCUMENT divergences | H |
| `tableau/document-api-python` | Library to **modify** existing `.twb`/`.tds` (not create) | v0.11 (2022), "As-Is" tier | **IGNORE** — cannot create from scratch or author dashboards | — |
| `tableau/tableau-ui` (`@tableau/tableau-ui`) | React (16-only) components with Tableau look-and-feel | v3.14.0 (2026-05-15) | **FUTURE-ONLY** — irrelevant to a headless MCP server | — |
| `tableau/hyper-api-samples` | Hyper extract creation samples | active | **ALIGN** — already on the recommended pantab path; no change | L |
| `tableau/tableau_langchain` | LangChain/LangGraph read/query integration (VizQL Data Service) | `langchain-tableau` on PyPI | **DOCUMENT** — ecosystem context; read-only, no overlap | L |
| `tableau/VizQL-Data-Service` | OpenAPI + SDK for the read/query layer the official MCP uses | active | **DOCUMENT** — substantiates "they read via VDS, we publish via REST+Hyper" | L |
| `tableau/rest-api-samples` | Python REST samples (publish/permissions/auth) | active, 434★ | **DOCUMENT** — canonical reference; our XML/endpoints already match | L |
| tabcmd · tableau-migration-sdk · connector-plugin-sdk · tableau-postman · hyper-db · tableau-data-dictionary · tableau-server-in-kubernetes | CLI / migration / connector / docs / infra | — | **IGNORE** — out of scope for a write-side authoring server | — |

## Key findings

### 1. Our "official = read-only, zero overlap" positioning is now partly false (DOCUMENT)

`tableau/tableau-mcp` at v2.18.x ships a **web** variant (~35 tools) and a **desktop** variant (6 tools).
The web variant added **admin-gated mutation** tools (`delete-datasource`, `delete-workbook`,
`delete-extract-refresh-task`, `update-cloud-extract-refresh-task` — behind a site-admin gate +
`ADMIN_TOOLS_ENABLED` flag + confirmation token; src `src/tools/web/adminGate.ts`,
`src/tools/web/workbooks/deleteWorkbook.ts`). The desktop variant adds a **workbook-authoring loop**:
`get-workbook-xml` + **`apply-workbook`** (`src/tools/desktop/workbook/applyWorkbook.ts`,
`readOnlyHint:false`, `destructiveHint:true`) + `list-worksheets`/`list-dashboards`.

**Crucial differentiator (our niche survives):** the official authoring path targets a **locally running
Tableau Desktop instance** via a local executor (`list-instances` → session → `apply-workbook`). It does
**not** build a `.hyper` extract, package a `.tdsx`/`.twbx`, or **publish to Tableau Cloud**. Our value —
headless **SQL/file → governed Hyper extract → published Cloud datasource + workbook**, no Desktop — has
no official equivalent. So: reposition from *"they only read, zero overlap"* to *"official adds
Desktop-local editing + admin-gated lifecycle; we are the headless, server-side, publish-to-Cloud
authoring path."*

Stale specifics to fix in our docs: official now supports **stdio _and_ http** transports
(`src/transports.ts`); multiple auth modes (`AUTH` ∈ pat/uat/direct-trust/oauth, `src/config.ts`) though
the shared PAT env-var path still works; Node floor **≥ 22.7.5** (`package.json`). Our
`docs/relationship_to_official.md` also still lists the **old 11-tool** surface (missing
`create_datasource_from_file`, `design_dashboard`, `build_from_plan`).

### 2. Official TWB XSD catches 4 real defects in our output (VALIDATE-AGAINST → fix)

`tableau/tableau-document-schemas` provides `schemas/2026_1/twb_2026.1.0.xsd` (W3C XSD, agent-targeted,
permissive on connection internals via `processContents="skip"` — so our `sqlproxy`/Hyper connection
attrs pass through). Validating our actual `build_twb_xml()` output (bar+line sheets + one dashboard)
against it with `lxml` **fails** on:

1. **`<dashboards>` position** — schema sequence is `Worksheets → Dashboards → Windows`; we append
   `<dashboards>` *after* `<windows>`. (`sidecar/twb_builder.py`)
2. **`<worksheet>` missing required `<simple-id>` child.**
3. **`<table>`/`<view>` child ordering** — `<rows>`/`<cols>` placed where `<style>` is expected; `<view>`
   children (incl. `datasource-dependencies`) out of order.
4. **`<window>` missing required `cards`/`viewpoints` children.**

These are the R-1 "parses but won't render" class and are invisible to our current structural tests
(which only check our own expected elements exist). Wiring the official XSD into `sidecar/tests/` (vendor
the XSD + two tiny import stubs for the unresolved `xml:`/`user:` imports; add `lxml` as a sidecar dev
dep) gives an official fidelity gate; fixing 1–4 makes our output schema-valid. **TWBX (the zip) is out
of XSD scope** — validate the inner `.twb` XML string from `build_twb_xml()`, not the package.

### 3. restClient chunk boundary diverges from official TSC by one byte (ALIGN → fix)

TSC chunks when `file_size >= FILESIZE_LIMIT_MB * BYTES_PER_MB` (exactly 64 MB **chunks**;
`tableauserverclient/server/endpoint/datasources_endpoint.py`). Ours chunks on `> limit`, so exactly
64 MB takes the **single** path (`src/restClient.ts` `selectPublishStrategy`, and PLAN.md criterion 3).
At exactly 64 MB a single multipart request exceeds the 64 MB cap once boundary overhead is added, so the
official `>=` is the safer, correct boundary. Fix `>` → `>=`, update the comment, the boundary test, and
PLAN.md criterion 3, citing TSC as the authority. Other TSC divergences (64 MB chunk size vs TSC's 50;
pinned REST `3.28` vs auto-negotiate; abandon-unfinalized-session abort posture — which actually *matches*
TSC) are acceptable and only warrant a DOCUMENT note.

### 4. document-api-python and tableau-ui — documented non-adoptions

- **document-api-python:** its own README states it "doesn't support creating files from scratch";
  `Workbook.__init__` only opens existing files; `_prepare_dashboards()` returns names only (no zone
  writer); worksheets are name stubs (`# TODO: A real worksheet object`). It cannot author what we emit.
  Keep hand-rolling; record the rationale so it isn't re-evaluated.
- **tableau-ui:** a React-16 browser component library; a headless stdio MCP server has no DOM/React
  attachment point. Relevant only in the single future scenario of a separate **web admin console**
  (job/permission GUI) — and even then it carries a React-16-only constraint. FUTURE-ONLY.

## Adaptation backlog (drives the plan loop)

| # | Bucket | Change | File(s) | Value | Effort |
|---|---|---|---|---|---|
| A1 | Code | Add official-XSD validation pytest; vendor `twb_2026.1.0.xsd` + import stubs; add `lxml` dev dep | `sidecar/tests/test_twb_schema_validation.py`, `sidecar/tests/schemas/`, `sidecar/pyproject.toml` | H | M |
| A2 | Code | Fix the 4 structural defects so output is schema-valid (dashboards-before-windows; `<simple-id>`; table/view ordering; window children) | `sidecar/twb_builder.py` (+ update affected `test_twb_builder.py`/`test_twb_dashboard.py` baselines) | H | M |
| A3 | Code | Chunk boundary `>` → `>=` (align to TSC); update comment, test, and PLAN.md criterion 3 | `src/restClient.ts`, `tests/restClient.test.ts`, `PLAN.md` | H | L |
| A4 | Docs | Reframe positioning + refresh to 14 tools + fix transport/auth/Node claims + add langchain/VDS context | `docs/relationship_to_official.md`, `README.md` | H | M |
| A5 | Docs | 11→14 tools; drop "read-only" official; document TSC divergences + non-adoption rationale (document-api-python, tableau-ui) | `CODEBASE.md` | M | L |

Out of scope / deferred (YAGNI): `TABLEAU_CHUNK_SIZE_MB` env knob; Hyper-native `COPY ... FORMAT
PARQUET` for very large Parquet; emitting workbook `version="26.1"` (only if needed for XSD validation —
the schema's version pattern already accepts our current string).
