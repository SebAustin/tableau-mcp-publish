/**
 * cronTemplates.ts — pure template builders for `generate-cron.ts` (Phase E2 slice C).
 *
 * Split out from the CLI entry point so the crontab-line / launchd-plist shape
 * is unit-testable without spawning a process or touching the filesystem.
 * Nothing in this module performs I/O.
 */

export type CronSchedule = { kind: "hourly" } | { kind: "daily"; time: string };

export interface RefreshLocalInvocation {
  file: string;
  name: string;
  project: string;
  persona?: string;
}

export interface CronArtifactInput {
  schedule: CronSchedule;
  invocation: RefreshLocalInvocation;
  /** Absolute path to the repo root — baked into both artifacts so cron/launchd can `cd` there. */
  repoRoot: string;
}

const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** Validates a `--daily HH:MM` value (24-hour clock). */
export function isValidDailyTime(value: string): boolean {
  return TIME_RE.test(value);
}

/** Turns an arbitrary display name into a filesystem/launchd-label-safe slug. */
export function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
  return slug || "job";
}

/** Escapes the five XML predefined entities (local copy — cron templates should not depend on `src/`). */
function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** Builds the `--file/--name/--project/--persona` argument string passed to refresh-local.ts. */
function refreshLocalArgString(invocation: RefreshLocalInvocation): string {
  const parts = [
    `--file "${invocation.file}"`,
    `--name "${invocation.name}"`,
    `--project "${invocation.project}"`,
  ];
  if (invocation.persona) parts.push(`--persona "${invocation.persona}"`);
  return parts.join(" ");
}

/** launchd label for a given datasource-name slug. */
export function launchdLabel(nameSlug: string): string {
  return `com.tableau-mcp-publish.refresh-local.${nameSlug}`;
}

/**
 * Builds a single crontab line. Hourly runs at minute 0 of every hour;
 * daily runs at the given 24-hour `HH:MM`. Output redirects to a fixed log
 * file under `scripts/cron/` (gitignored) so cron mail stays quiet.
 */
export function buildCrontabLine(input: CronArtifactInput): string {
  const cronTime =
    input.schedule.kind === "hourly"
      ? "0 * * * *"
      : (() => {
          const [hh, mm] = input.schedule.time.split(":");
          return `${Number(mm)} ${Number(hh)} * * *`;
        })();
  const logPath = `${input.repoRoot}/scripts/cron/refresh-local.log`;
  return (
    `${cronTime} cd "${input.repoRoot}" && ` +
    `/usr/bin/env PATH="/usr/local/bin:/usr/bin:/bin:$PATH" npx tsx scripts/refresh-local.ts ` +
    `${refreshLocalArgString(input.invocation)} >> "${logPath}" 2>&1`
  );
}

/**
 * Builds a macOS launchd agent plist (XML) equivalent to `buildCrontabLine`'s
 * schedule. `ProgramArguments` invokes `npx tsx scripts/refresh-local.ts`
 * directly (no shell), so each argument is a separate `<string>` — no shell
 * quoting/escaping pitfalls.
 */
export function buildLaunchdPlist(input: CronArtifactInput, label: string): string {
  const programArgs = [
    "npx",
    "tsx",
    "scripts/refresh-local.ts",
    "--file",
    input.invocation.file,
    "--name",
    input.invocation.name,
    "--project",
    input.invocation.project,
  ];
  if (input.invocation.persona) programArgs.push("--persona", input.invocation.persona);

  const programArgsXml = programArgs
    .map((a) => `        <string>${xmlEscape(a)}</string>`)
    .join("\n");

  const scheduleXml =
    input.schedule.kind === "hourly"
      ? "    <key>StartCalendarInterval</key>\n    <dict>\n" +
        "        <key>Minute</key>\n        <integer>0</integer>\n    </dict>"
      : (() => {
          const [hh, mm] = input.schedule.time.split(":").map(Number);
          return (
            "    <key>StartCalendarInterval</key>\n    <dict>\n" +
            `        <key>Hour</key>\n        <integer>${hh}</integer>\n` +
            `        <key>Minute</key>\n        <integer>${mm}</integer>\n    </dict>`
          );
        })();

  const logPath = `${input.repoRoot}/scripts/cron/refresh-local.log`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${xmlEscape(label)}</string>
    <key>ProgramArguments</key>
    <array>
${programArgsXml}
    </array>
    <key>WorkingDirectory</key>
    <string>${xmlEscape(input.repoRoot)}</string>
${scheduleXml}
    <key>StandardOutPath</key>
    <string>${xmlEscape(logPath)}</string>
    <key>StandardErrorPath</key>
    <string>${xmlEscape(logPath)}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
`;
}
