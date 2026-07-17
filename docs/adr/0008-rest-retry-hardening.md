# ADR-0008 — Shared, idempotency-aware bounded retry for REST/VDS/Pulse calls

**Status:** Accepted
**Date:** 2026-07-17
**Feature:** REST hardening (Phase E2, Foundation slice)

---

## Context

As the tool surface grew to include scheduling, webhooks, VDS field metadata, and Pulse — all of
which are more failure-prone in practice than the original sign-in/publish path (VDS/Pulse can be
disabled per-site; Cloud rate-limits aggressively under load) — bare `undici` calls with no retry
meant a single transient `429`/`503` would fail an entire tool call outright, even though the
underlying operation was often safe to retry.

Two constraints shaped the decision: (1) some operations are **not** safe to blindly retry (a POST
that creates a Pulse definition could double-create if the original request actually succeeded but
the response was lost), and (2) errors needed to be structured (`status`/`code`/`retriable`) so
callers and tests could branch on them instead of regexing a message string.

## Decision

**One shared `withRetry()` loop (`src/rest/retry.ts`), explicitly Tableau-agnostic, reused by
`restClient.ts`, `rest/vds.ts`, and `rest/pulse.ts`. Retry eligibility is a two-part gate: the
HTTP status must be in a small allowlist (429/502/503/504 — never 401, never any other 4xx), AND
the specific call must be marked `idempotent` by its caller.**

- `TableauApiError` (`src/rest/errors.ts`) replaces bare `Error` for every REST/VDS/Pulse failure,
  carrying `status`/`method`/`path`/`code`/`summary`/`detail`/`retriable`/`retryAfterMs`. The
  exception message never echoes the raw response body — only status/method/path/summary — so a
  logged or agent-surfaced error can never leak upstream internals.
- Backoff is "full jitter" exponential (AWS-recommended): `random() * min(base * 2^(n-1), cap)`,
  honoring an upstream `Retry-After` header verbatim when present. Deterministic in tests via
  injectable `sleep`/`jitterFn`/`now`.
- Idempotency is decided **per call site**, not per HTTP verb blindly: GET is always idempotent;
  POST/DELETE are marked idempotent only where the operation is genuinely safe to repeat (e.g.
  `read-metadata` is a POST with no side effects, so VDS marks it idempotent despite the verb).
  `rest/pulse.ts` takes an even more conservative stance than the rest of the codebase — it never
  retries POST or DELETE at all, because Pulse's own response shapes (especially delete) are
  themselves VERIFY-LIVE unknowns, so retrying blind is a bigger risk there than elsewhere.

## Alternatives considered

**A generic HTTP-client-level retry library (e.g. `undici`'s built-in retry, or `p-retry`):**
rejected — none of the evaluated options exposed both (a) per-call-site idempotency control and
(b) Tableau's specific error-body shape for classification, without wrapping in custom code anyway;
a small hand-rolled module was less code than gluing a generic library to Tableau semantics.

**Retry every POST unconditionally on 5xx (assume "the request probably failed cleanly"):**
rejected — a lost response after a successful server-side write (e.g. webhook or Pulse definition
created, but the `201` never arrived) would silently double-create the resource on retry. The
per-call idempotency flag makes this an explicit decision at each call site instead of a blanket
assumption.

**No retry at all; let the calling agent retry the whole tool call:** rejected — pushes transient-
failure handling up to every agent integration, with no shared backoff/jitter policy, and no way
to distinguish "definitely gone, don't retry" (401, most 4xx) from "try again" (429/5xx) without
duplicating Tableau's error-body parsing at the agent layer.

## Consequences

**Positive:**

- `get_datasource_fields`, `schedule_refresh`, `create_webhook`, and the Pulse tools all get
  automatic resilience to Cloud rate-limiting and transient 5xx without each tool reimplementing
  backoff.
- `tests/restRetry.test.ts`, `tests/retry.test.ts`, `tests/vds.test.ts`, `tests/pulse.test.ts`, and
  `tests/webhooks.test.ts` all assert retry behavior deterministically (fake clock, fixed jitter)
  with zero real waiting.
- Errors are typed end-to-end: `err.retriable` is a single source of truth instead of a duplicated
  status-code list scattered across call sites.

**Constraints this decision enforces:**

- Any new REST/VDS/Pulse module must classify its own idempotency per call site — there is no
  default that "does the right thing" automatically; getting this wrong for a non-idempotent
  mutation risks duplicate server-side effects on retry.
- `rest/pulse.ts`'s narrower policy (GET-only retry) must be revisited once Pulse's delete/create
  response shapes are confirmed against a live site — see ADR-0011.
