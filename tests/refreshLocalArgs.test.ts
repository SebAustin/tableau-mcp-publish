import { describe, it, expect } from "vitest";
import { parseArgs } from "../scripts/refresh-local.js";

const ARGV_BASE = ["node", "refresh-local.ts"];

describe("refresh-local parseArgs", () => {
  it("parses --file/--name/--project", () => {
    const args = parseArgs([
      ...ARGV_BASE,
      "--file",
      "/data/sales.csv",
      "--name",
      "Sales CSV",
      "--project",
      "Sales",
    ]);
    expect(args).toEqual({ file: "/data/sales.csv", name: "Sales CSV", project: "Sales" });
  });

  it("parses the optional --persona flag", () => {
    const args = parseArgs([
      ...ARGV_BASE,
      "--file",
      "/data/sales.csv",
      "--name",
      "Sales CSV",
      "--project",
      "Sales",
      "--persona",
      "ceo",
    ]);
    expect(args.persona).toBe("ceo");
  });

  it("throws listing every missing required argument", () => {
    expect(() => parseArgs([...ARGV_BASE, "--file", "/data/sales.csv"])).toThrow(
      /Missing required argument\(s\): --name, --project/,
    );
  });

  it("throws when a flag is missing its value", () => {
    expect(() => parseArgs([...ARGV_BASE, "--file"])).toThrow(/Missing value for --file/);
  });
});
