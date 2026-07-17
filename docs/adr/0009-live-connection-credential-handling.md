# ADR-0009 — Live connections: no credentials in the `.tds`, embedded only at publish time

**Status:** Accepted
**Date:** 2026-07-17
**Feature:** Live Cloud connections (Phase E2 slice C) — supersedes ADR-0004's "extract-only" scope

---

## Context

ADR-0004 deferred live-connection datasources for v0.1 in favor of extract-only publishing. Phase
E2 needed to add live Snowflake/Presto connections (no locally materialized extract) while keeping
the same security posture the rest of the project holds: credentials must never be written to
disk, logged, or embedded in a document that could be inspected/leaked independently of Tableau's
own access controls.

Two design questions: (1) where do database credentials live between the tool call and the
publish, and (2) how does the system handle the one authentication mode (Snowflake key-pair) that
is fundamentally impossible to configure over the REST publish API.

## Decision

**The `.tds` file the sidecar builds carries only connection *topology* — server, warehouse,
schema, database, table. Credentials never touch it.** `create_live_datasource`'s
`credentials.username`/`credentials.password` are passed straight through the TypeScript tool call
into `TableauRestClient.publishDatasource`'s `credentials` option, which
`rest/credentials.ts`'s `buildConnectionCredentialsXml()` renders as an in-memory
`<connectionCredentials name="…" password="…" embed="true" oAuth="…" />` XML fragment, nested
inside the same multipart publish request as the `.tds` bytes — and nowhere else. Every attribute
is `xmlEscape`d; `restClient.ts` only ever logs the *response* body of a failed call, never the
outgoing request body, so a failed publish can never leak the password to stderr (asserted
standingly in `tests/secrets.test.ts` and `tests/credentials.test.ts`).

**Snowflake key-pair authentication is explicitly rejected, not silently attempted.** Tableau's
Publish Datasource REST API only supports embedding username/password or OAuth credentials —
key-pair auth requires Tableau Desktop. `create_live_datasource` and the sidecar's
`/datasource/live` route both check the requested `authentication` mode and throw/400 with an
actionable message ("configure key-pair auth in Desktop and publish from there instead") before any
network call, rather than emitting XML that would build successfully but never actually
authenticate.

## Alternatives considered

**Write credentials into a temp `.tds` file, then delete it after publish:** rejected — even a
short-lived credential-bearing file on disk is an unnecessary exposure window (backup tools,
crash-before-cleanup, other processes) that the in-memory-XML-fragment approach avoids entirely.

**Accept environment-variable-only credentials (server reads `SNOWFLAKE_USER`/`SNOWFLAKE_PASSWORD`
itself):** rejected for this phase — `config.ts`'s env surface is deliberately identical to the
official `@tableau/mcp-server` (`SERVER`/`SITE_NAME`/`PAT_NAME`/`PAT_VALUE` only); adding
connector-specific env vars would mean a fixed set of env names could not represent an arbitrary
number of different live connections a single server instance might publish across a session. The
tool description explicitly recommends the *calling agent* source credentials from its own
environment and pass them as call arguments, keeping the MCP server's own env surface unchanged.

**Silently downgrade an unsupported Snowflake auth mode to username/password:** rejected — would
either fail confusingly downstream or, worse, appear to succeed while never actually connecting.
An explicit, actionable error at the tool boundary is safer than a silent, wrong fallback.

## Consequences

**Positive:**

- The security posture matches the rest of the project: no new credential-at-rest surface was
  introduced by this feature.
- The key-pair rejection is a genuinely honest capability boundary, not a documented-but-broken
  feature — an agent gets a clear "not possible this way" instead of a confusing runtime failure.
- Two independent checks (TypeScript tool layer, Python sidecar route) both reject key-pair auth,
  so a caller bypassing the TS tool (e.g. a direct sidecar call in testing) still gets the guard.

**Constraints this decision enforces:**

- `LiveConnectionSpec`/`LiveDatasourceRequest` must never grow a credentials field — this is
  enforced by convention (documented in both `src/sidecar.ts` and `sidecar/server.py`), not by a
  runtime check, so a future edit must preserve this boundary deliberately.
- The exact XML attribute spelling for Snowflake/Presto connector classes
  (`sidecar/tds_builder.py`'s `SNOWFLAKE_ATTRS`/`PRESTO_ATTRS`) remains VERIFY-LIVE — this decision
  covers *where credentials live*, not *whether the connection XML is byte-perfect* — until
  confirmed against a Desktop-exported `.tds`.
- Presto/Trino's `useRemoteQueryAgent` default (`true`) assumes Tableau Bridge is required unless
  explicitly told otherwise — a deliberately conservative default given Cloud generally cannot
  reach a private-network Presto cluster directly.
