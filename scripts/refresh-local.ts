#!/usr/bin/env -S npx tsx
/**
 * refresh-local.ts — Local-file cron re-publish automation (Phase E2 slice C).
 *
 * Tableau Cloud cannot itself refresh a datasource published from a local file
 * (CSV/Excel/local Hyper extract) — no Bridge, no way to re-query the source
 * server-side. This script is the confirmed design-around: re-ingest the local
 * file through the sidecar (encoding/delimiter/type auto-sniffed, same as
 * `create_datasource_from_file`) and re-publish with `overwrite=true`, on
 * whatever cadence a cron job or launchd agent invokes it — see
 * `generate-cron.ts` for the schedule artifacts.
 *
 * Usage:
 *   npx tsx scripts/refresh-local.ts --file <path> --name <datasource name> --project <project> [--persona <name>]
 *   npm run refresh:local -- --file <path> --name <datasource name> --project <project>
 *
 * `--persona <name>` is optional and does not rebuild a dashboard (this
 * script only republishes the datasource) — it is validated against
 * brand.yaml (fail loud on an unknown name, the same "validate at the
 * boundary" discipline as everywhere else in this project) and included in
 * the summary log line for provenance/traceability.
 *
 * Exit codes: 0 on success, 1 on any failure (bad args, missing config,
 * sidecar or publish error) — this MUST be a clean process exit so cron/
 * launchd can alert on failure. Logs exactly ONE summary line on success and
 * ONE on failure, to keep cron mail / log aggregation readable at scale.
 *
 * NOT run automatically by this task: this file is generated/reviewed only —
 * nothing here schedules or installs itself (see generate-cron.ts).
 */
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import { AuthoringSidecar } from "../src/sidecar.js";
import { loadBrand, resolvePersona } from "../src/branding/load.js";

export interface RefreshLocalArgs {
  file: string;
  name: string;
  project: string;
  persona?: string;
}

/** Parses `--file <path> --name <name> --project <project> [--persona <name>]`. Throws on missing/malformed args. */
export function parseArgs(argv: string[]): RefreshLocalArgs {
  const rest = argv.slice(2);
  const values: Record<string, string> = {};
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i] ?? "";
    if (!arg.startsWith("--")) continue;
    const key = arg.slice(2);
    const next = rest[i + 1];
    if (next === undefined || next.startsWith("--")) {
      throw new Error(`Missing value for --${key}.`);
    }
    values[key] = next;
    i += 1;
  }

  const required = ["file", "name", "project"] as const;
  const missing = required.filter((k) => !values[k]);
  if (missing.length > 0) {
    throw new Error(
      `Missing required argument(s): ${missing.map((m) => `--${m}`).join(", ")}. ` +
        "Usage: refresh-local.ts --file <path> --name <datasource name> --project <project> [--persona <name>]",
    );
  }

  return {
    file: values["file"] as string,
    name: values["name"] as string,
    project: values["project"] as string,
    ...(values["persona"] !== undefined ? { persona: values["persona"] } : {}),
  };
}

async function main(): Promise<void> {
  try {
    process.loadEnvFile(".env");
  } catch {
    // No .env file — fall back to the ambient environment (cron/launchd typically set env directly).
  }

  const args = parseArgs(process.argv);

  if (args.persona) {
    // Fail loud on an unknown persona: this script only republishes the raw
    // datasource (no dashboard rebuild), but a typo'd persona name usually
    // means the operator would want to know immediately, not silently on
    // every cron run.
    const { brand } = loadBrand();
    resolvePersona(brand, args.persona);
  }

  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);

  const startedAt = Date.now();
  try {
    await rest.signIn();
    await sidecar.start();

    const { tdsxPath, columns } = await sidecar.buildDatasourceFromFile({
      name: args.name,
      filePath: resolve(args.file),
      // fileType/encoding/delimiter omitted — auto-sniffed by the sidecar
      // (same inference `create_datasource_from_file` performs).
    });

    const projectId = await rest.resolveProjectId(args.project);
    const result = await rest.publishDatasource(tdsxPath, args.name, projectId, true);

    const elapsedMs = Date.now() - startedAt;
    console.log(
      `[refresh-local] OK name="${args.name}" file="${args.file}" project="${args.project}" ` +
        `${args.persona ? `persona="${args.persona}" ` : ""}columns=${columns.length} ` +
        `datasourceLuid=${result.id} elapsedMs=${elapsedMs} url=${result.url}`,
    );
  } finally {
    await sidecar.stop();
    await rest.signOut().catch(() => undefined);
  }
}

// Only auto-run when executed directly (tsx/node), never when imported by tests.
const isEntrypoint =
  !!process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isEntrypoint) {
  main().catch((err) => {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[refresh-local] FAILED: ${message}`);
    process.exit(1);
  });
}
