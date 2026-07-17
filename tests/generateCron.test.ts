import { describe, it, expect } from "vitest";
import { parseArgs, renderOutput } from "../scripts/generate-cron.js";

const ARGV_BASE = ["node", "generate-cron.ts"];

describe("generate-cron parseArgs", () => {
  it("parses --daily HH:MM plus the required file/name/project", () => {
    const args = parseArgs([
      ...ARGV_BASE,
      "--daily",
      "03:30",
      "--file",
      "/data/sales.csv",
      "--name",
      "Sales CSV",
      "--project",
      "Sales",
    ]);
    expect(args).toEqual({
      schedule: { kind: "daily", time: "03:30" },
      file: "/data/sales.csv",
      name: "Sales CSV",
      project: "Sales",
      write: false,
    });
  });

  it("parses --hourly, --persona, and --write", () => {
    const args = parseArgs([
      ...ARGV_BASE,
      "--hourly",
      "--file",
      "/data/sales.csv",
      "--name",
      "Sales CSV",
      "--project",
      "Sales",
      "--persona",
      "ceo",
      "--write",
    ]);
    expect(args).toEqual({
      schedule: { kind: "hourly" },
      file: "/data/sales.csv",
      name: "Sales CSV",
      project: "Sales",
      persona: "ceo",
      write: true,
    });
  });

  it("throws when neither --daily nor --hourly is given", () => {
    expect(() =>
      parseArgs([...ARGV_BASE, "--file", "f", "--name", "n", "--project", "p"]),
    ).toThrow(/Specify exactly one/);
  });

  it("throws when both --daily and --hourly are given", () => {
    expect(() =>
      parseArgs([
        ...ARGV_BASE,
        "--daily",
        "03:30",
        "--hourly",
        "--file",
        "f",
        "--name",
        "n",
        "--project",
        "p",
      ]),
    ).toThrow(/Specify exactly one/);
  });

  it("throws on a malformed --daily time", () => {
    expect(() =>
      parseArgs([...ARGV_BASE, "--daily", "25:99", "--file", "f", "--name", "n", "--project", "p"]),
    ).toThrow(/24-hour/);
  });

  it("throws listing every missing required argument", () => {
    expect(() => parseArgs([...ARGV_BASE, "--hourly"])).toThrow(/--file, --name, --project/);
  });
});

describe("generate-cron renderOutput", () => {
  it("includes install instructions for both crontab and launchd, and never installs anything itself", () => {
    const args = parseArgs([
      ...ARGV_BASE,
      "--daily",
      "03:30",
      "--file",
      "/data/sales.csv",
      "--name",
      "Sales CSV",
      "--project",
      "Sales",
    ]);
    const { text, crontabLine, plist, label } = renderOutput(args, "/repo");

    expect(text).toContain("crontab -l 2>/dev/null; echo");
    expect(text).toContain("launchctl load");
    expect(text).toContain(crontabLine);
    expect(text).toContain(plist);
    expect(label).toBe("com.tableau-mcp-publish.refresh-local.sales-csv");
    // The install commands appear only inside `#`-prefixed comment lines — this
    // function never shells out to `crontab`/`launchctl` itself (generate only).
    const installLines = text.split("\n").filter((l) => l.includes("crontab -") || l.includes("launchctl load"));
    expect(installLines.length).toBeGreaterThan(0);
    expect(installLines.every((l) => l.trimStart().startsWith("#"))).toBe(true);
  });
});
