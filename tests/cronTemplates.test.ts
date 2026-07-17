import { describe, it, expect } from "vitest";
import {
  buildCrontabLine,
  buildLaunchdPlist,
  isValidDailyTime,
  launchdLabel,
  slugify,
  type CronArtifactInput,
} from "../scripts/cronTemplates.js";

/** Minimal well-formedness check: every opening tag has a matching closing tag, properly nested. */
function assertWellFormedXml(xml: string): void {
  const tagRe = /<\/?([a-zA-Z][\w:-]*)(?:\s+[^<>]*)?(\/?)>/g;
  const stack: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = tagRe.exec(xml)) !== null) {
    const [full, name, selfClosing] = match;
    if (full.startsWith("</")) {
      expect(stack.pop()).toBe(name);
    } else if (!selfClosing) {
      stack.push(name as string);
    }
  }
  expect(stack).toEqual([]);
}

const dailyInput: CronArtifactInput = {
  schedule: { kind: "daily", time: "03:30" },
  invocation: { file: "/data/sales.csv", name: "Sales CSV", project: "Sales" },
  repoRoot: "/repo",
};

const hourlyInput: CronArtifactInput = {
  schedule: { kind: "hourly" },
  invocation: { file: "/data/sales.csv", name: "Sales CSV", project: "Sales", persona: "ceo" },
  repoRoot: "/repo",
};

describe("isValidDailyTime", () => {
  it("accepts valid 24-hour HH:MM", () => {
    expect(isValidDailyTime("00:00")).toBe(true);
    expect(isValidDailyTime("23:59")).toBe(true);
    expect(isValidDailyTime("03:30")).toBe(true);
  });

  it("rejects malformed or out-of-range values", () => {
    expect(isValidDailyTime("24:00")).toBe(false);
    expect(isValidDailyTime("12:60")).toBe(false);
    expect(isValidDailyTime("3:30")).toBe(false);
    expect(isValidDailyTime("not-a-time")).toBe(false);
  });
});

describe("slugify", () => {
  it("lowercases, replaces non-alphanumerics with hyphens, trims edge hyphens", () => {
    expect(slugify("Sales CSV")).toBe("sales-csv");
    expect(slugify("Superstore (exec demo)")).toBe("superstore-exec-demo");
    expect(slugify("---")).toBe("job"); // falls back when the slug would be empty
  });
});

describe("launchdLabel", () => {
  it("prefixes the reverse-DNS-style label", () => {
    expect(launchdLabel("sales-csv")).toBe("com.tableau-mcp-publish.refresh-local.sales-csv");
  });
});

describe("buildCrontabLine", () => {
  it("emits a daily 5-field cron expression at the given HH:MM", () => {
    const line = buildCrontabLine(dailyInput);
    expect(line.startsWith("30 3 * * * ")).toBe(true);
    expect(line).toContain('cd "/repo"');
    expect(line).toContain("npx tsx scripts/refresh-local.ts");
    expect(line).toContain('--file "/data/sales.csv"');
    expect(line).toContain('--name "Sales CSV"');
    expect(line).toContain('--project "Sales"');
    expect(line).toContain(">> \"/repo/scripts/cron/refresh-local.log\" 2>&1");
  });

  it("emits an hourly cron expression (minute 0 of every hour) and includes --persona", () => {
    const line = buildCrontabLine(hourlyInput);
    expect(line.startsWith("0 * * * * ")).toBe(true);
    expect(line).toContain('--persona "ceo"');
  });

  it("is a single line (no embedded newlines) — required for a valid crontab entry", () => {
    expect(buildCrontabLine(dailyInput)).not.toContain("\n");
    expect(buildCrontabLine(hourlyInput)).not.toContain("\n");
  });
});

describe("buildLaunchdPlist", () => {
  it("is well-formed XML with the expected structure for a daily schedule", () => {
    const label = launchdLabel(slugify(dailyInput.invocation.name));
    const plist = buildLaunchdPlist(dailyInput, label);

    expect(plist.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
    assertWellFormedXml(plist);

    expect(plist).toContain(`<string>${label}</string>`);
    expect(plist).toContain("<string>scripts/refresh-local.ts</string>");
    expect(plist).toContain("<string>--file</string>");
    expect(plist).toContain("<string>/data/sales.csv</string>");
    expect(plist).toContain("<key>Hour</key>\n        <integer>3</integer>");
    expect(plist).toContain("<key>Minute</key>\n        <integer>30</integer>");
    expect(plist).toContain("<string>/repo</string>"); // WorkingDirectory
    expect(plist).toContain("<false/>"); // RunAtLoad
  });

  it("uses a Minute-only StartCalendarInterval for hourly and includes --persona in ProgramArguments", () => {
    const label = launchdLabel(slugify(hourlyInput.invocation.name));
    const plist = buildLaunchdPlist(hourlyInput, label);

    assertWellFormedXml(plist);
    expect(plist).toContain("<key>Minute</key>\n        <integer>0</integer>");
    expect(plist).not.toContain("<key>Hour</key>");
    expect(plist).toContain("<string>--persona</string>");
    expect(plist).toContain("<string>ceo</string>");
  });

  it("xml-escapes special characters in the datasource name / paths", () => {
    const input: CronArtifactInput = {
      schedule: { kind: "hourly" },
      invocation: { file: "/data/a&b.csv", name: 'Sales "Q2" & <Co>', project: "Sales" },
      repoRoot: "/repo",
    };
    const plist = buildLaunchdPlist(input, "com.example.job");
    assertWellFormedXml(plist);
    expect(plist).toContain("a&amp;b.csv");
    expect(plist).toContain("&quot;Q2&quot;");
    expect(plist).toContain("&lt;Co&gt;");
    expect(plist).not.toContain('"Q2"'); // raw quote must not survive unescaped inside a <string>
  });
});
