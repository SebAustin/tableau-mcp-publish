# Architecture Decision Records

Short records of the decisions that shaped this build. Full rationale and version evidence are in
`ASSUMPTIONS.md`.

## ADR-001 — Pin a current MCP SDK and use the `registerTool` API (not the spec's `1.6.0`)

**Context.** The original build spec pinned `@modelcontextprotocol/sdk@1.6.0` and the low-level
`new Server() + setRequestHandler()` API. That version is long stale; the official `tableau-mcp`
uses `^1.12.1` and the current line is `1.29.x`.

**Decision.** Pin `@modelcontextprotocol/sdk@1.29.0` and use the higher-level
`McpServer.registerTool(name, { inputSchema, outputSchema }, handler)` API, returning `content`
plus validated `structuredContent`. Schemas are zod **ZodRawShape** objects. zod stays on **v3**
(v4 breaks the SDK).

**Consequences.** Tools get structured output and input validation for free; the server matches the
official server's modern conventions. zod is pinned to a current 3.x.

## ADR-002 — Hand-build the `.tdsx` and `.twb`/`.twbx` document XML

**Context.** There is no maintained Python "Document API" library that *creates* a `.tdsx` (the
existing ones are read-oriented or abandoned).

**Decision.** Build the `.tds` and `.twb` XML directly with `xml.etree.ElementTree` and zip the
packages by hand. The `.tds` describes a `hyper` connection over the embedded extract; the `.twb`
references a *published* datasource via `class='sqlproxy'` + a `repository-location` (keyed by the
server-assigned contentUrl + site).

**Consequences.** Full control over the output and no dependency on an unmaintained library.
Workbook XML is schema-sensitive, so it is asserted structurally in CI and proven to render once
against a live site (the gated demo). Mark types `bar`/`line`/`text` are supported; `map` is
experimental.

## ADR-003 — Python authoring sidecar over loopback, spawned by the TS server

**Context.** MCP + the official server are TypeScript, but the Hyper API and document tooling are
Python-first.

**Decision.** Keep the MCP server in TypeScript (REST auth + publishing) and put all file authoring
in a Python FastAPI sidecar that the TS server spawns and calls over `127.0.0.1`. A per-spawn
random token (`X-Sidecar-Token`) gates every request; the child's stdout is kept off the MCP stdio
channel.

**Consequences.** Best tool for each job, no reimplementation, no external network dependency. Adds
a process-lifecycle concern, handled with a health probe + timeout and shutdown kill.

## ADR-004 — Extract-only datasources for v0.1; database drivers are optional extras

**Context.** Governed enterprise datasources are often live connections, but extracts are simpler
and self-contained.

**Decision.** v0.1 publishes Hyper *extracts*. Live-connection datasources are deferred (tracked as
a follow-up issue). Snowflake/Postgres drivers are optional `connectors` extras so CI exercises the
authoring path without DB drivers; CSV always works.

**Consequences.** Smaller, reliable surface for v0.1; CI stays fast and driver-free; live
connections are a clean future addition.
