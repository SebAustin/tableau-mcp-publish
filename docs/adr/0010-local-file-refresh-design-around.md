# ADR-0010 — Local-file datasources: cron/launchd design-around instead of a Bridge dependency

**Status:** Accepted
**Date:** 2026-07-17
**Feature:** Local-file refresh automation (Phase E2 slice C)

---

## Context

`schedule_refresh` lets an agent create a recurring Cloud extract-refresh task. But Tableau Cloud
can only **execute** that schedule for a connection it can reach and re-query itself — a live
Snowflake/Presto connection with embedded credentials, for example. A datasource published from a
local file (CSV/Excel/local Hyper extract, e.g. anything from `create_datasource_from_file`) has no
server-side source Cloud can go back to; refreshing it requires either **Tableau Bridge** (a
customer-installed agent that proxies Cloud's refresh requests to an on-prem/local data source) or
re-publishing the file from wherever it lives. Bridge is a separate, licensed, installed product
that this MCP server cannot configure or assume is present.

The `schedule_refresh` REST call itself does not fail for a file-based datasource — Cloud accepts
the schedule and creates the task; only the *scheduled run* fails, and that failure is not visible
at schedule-creation time. This makes the gap easy to hit silently.

## Decision

**Ship a self-contained, local re-ingest-and-republish path (`scripts/refresh-local.ts`) plus a
pure template generator (`scripts/generate-cron.ts` / `cronTemplates.ts`) that emits a crontab
line and a macOS launchd plist an operator reviews and installs manually.** Nothing in this project
installs a cron job or loads a launchd agent itself.

- `refresh-local.ts` re-runs the same file-ingest path `create_datasource_from_file` uses
  (encoding/delimiter/type auto-sniffed), then republishes with `overwrite=true`. It exits 0/1
  cleanly and logs exactly one summary line per outcome, so cron/launchd's own failure-alerting
  (mail, log aggregation) works without extra plumbing.
- `generate-cron.ts` only **prints** (or, with `--write`, saves under the gitignored
  `scripts/cron/`) the crontab line and plist text — it never runs `crontab -e` or
  `launchctl load` itself, so the operator always makes the final "yes, install this" decision.
- `schedule_refresh`'s tool description and returned `note` field always state the Bridge caveat
  unconditionally (not just when a failure is detected) — the REST API gives no reliable signal
  that would let the server distinguish "this will actually run" from "this will silently fail
  every time," so honesty requires stating the caveat every time rather than only when detectable.

## Alternatives considered

**Implement or bundle a Tableau Bridge client:** rejected — Bridge is a separate, licensed Tableau
product with its own installation and site-configuration requirements; reimplementing or bundling
it is out of scope for an MCP authoring server and would require infrastructure this project has no
way to provision or test against.

**Silently fail `schedule_refresh` for anything that isn't obviously a live connection:** rejected
— the REST API response gives no reliable signal to detect "this datasource has no cloud-reachable
source" at schedule-creation time (a datasource published from `create_live_datasource` vs.
`create_datasource_from_file` looks similar enough in the response that a heuristic would be
fragile); an honest, always-included caveat is more defensible than an unreliable auto-detection
that could produce false confidence either way.

**Auto-install the cron/launchd job as part of `generate-cron.ts`:** rejected — modifying a user's
crontab or LaunchAgents directory without an explicit, separate confirmation step is the kind of
system-level side effect this project avoids elsewhere (compare `delete_content`'s `confirm: true`
gate); a generated, reviewable artifact respects the same principle.

## Consequences

**Positive:**

- Operators get a genuinely usable path to keep a file-based datasource fresh without needing
  Bridge, proven live (`npm run refresh:local` republished the Superstore datasource in 4.7s
  against a real Cloud site — see `ACCEPTANCE.md`).
- The generated artifacts are pure functions of their inputs (`cronTemplates.ts` has no I/O),
  making them fully unit-testable (`tests/cronTemplates.test.ts`, `tests/generateCron.test.ts`)
  without touching a real crontab or LaunchAgents directory in CI.
- `schedule_refresh`'s honesty-by-default caveat means an agent presenting the tool's output to a
  user always surfaces the Bridge risk, rather than only after a scheduled run has already failed
  silently on the customer's site.

**Constraints this decision enforces:**

- `refresh-local.ts` and `generate-cron.ts` must remain scripts (`npm run refresh:local` /
  `npm run cron:generate`), not MCP tools — they are operator-run automation, not agent-callable
  actions, since installing a recurring local job is a machine-level decision outside a single
  conversational tool-call's blast radius.
- Any datasource name, file path, or project name interpolated into the generated crontab line
  must be shell-escaped (POSIX single-quoting, `cronTemplates.ts`'s `shellQuote()`) — an
  agent-supplied name is untrusted input from the crontab's perspective, and double-quoting alone
  does not neutralize `$(...)`/backtick/`$VAR` substitution (see `SECURITY.md`'s VB-02 finding,
  remediated in the same commit line as this ADR).
