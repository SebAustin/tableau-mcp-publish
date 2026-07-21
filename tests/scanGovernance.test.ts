import { describe, it, expect } from "vitest";
import {
  scanContent,
  DEFAULT_STALE_DAYS,
  DEFAULT_NAMING_PATTERN,
  type ContentRef,
} from "../src/governance/checks.js";

const NOW = "2026-07-21T00:00:00.000Z";

function ds(overrides: Partial<ContentRef>): ContentRef {
  return { id: "id", name: "Clean Name", type: "datasource", ...overrides };
}

describe("scanContent — governance-lite checks", () => {
  it("flags stale content past the threshold using the injected clock", () => {
    const old = "2025-01-01T00:00:00.000Z"; // ~571 days before NOW
    const r = scanContent([ds({ updatedAt: old })], { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    expect(r.findings).toHaveLength(1);
    expect(r.findings[0]?.check).toBe("stale");
    expect(r.findings[0]?.severity).toBe("warn");
    expect(r.summary.byCheck.stale).toBe(1);
  });

  it("does NOT flag fresh content", () => {
    const fresh = "2026-07-01T00:00:00.000Z"; // 20 days before NOW
    const r = scanContent([ds({ updatedAt: fresh })], { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    expect(r.findings).toHaveLength(0);
  });

  it("counts missing updatedAt as staleUndetermined, not stale", () => {
    const r = scanContent([ds({})], { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    expect(r.summary.staleUndetermined).toBe(1);
    expect(r.summary.byCheck.stale).toBe(0);
    expect(r.findings).toHaveLength(0);
  });

  it("flags content in the Default project (case-insensitive)", () => {
    const r = scanContent([ds({ projectName: "Default", updatedAt: NOW })], {
      nowIso: NOW,
      staleDays: DEFAULT_STALE_DAYS,
    });
    expect(r.findings.map((f) => f.check)).toEqual(["default_project"]);
  });

  it("flags naming issues via the default whitespace pattern", () => {
    const r = scanContent(
      [
        ds({ name: " Leading space", updatedAt: NOW }),
        ds({ name: "Double  space", updatedAt: NOW }),
        ds({ name: "Trailing ", updatedAt: NOW }),
      ],
      { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS },
    );
    expect(r.summary.byCheck.naming).toBe(3);
    expect(r.findings.every((f) => f.check === "naming" && f.severity === "info")).toBe(true);
  });

  it("respects a custom naming pattern", () => {
    const r = scanContent([ds({ name: "lowercase_start", updatedAt: NOW })], {
      nowIso: NOW,
      staleDays: DEFAULT_STALE_DAYS,
      namingPattern: /^[a-z]/,
    });
    expect(r.findings.map((f) => f.check)).toEqual(["naming"]);
  });

  it("returns zero findings for a clean list", () => {
    const r = scanContent(
      [ds({ name: "Sales Q4", projectName: "Finance", updatedAt: NOW })],
      { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS },
    );
    expect(r.findings).toHaveLength(0);
    expect(r.summary.scanned).toBe(1);
  });

  it("emits multiple checks per item in stable stale→default→naming order", () => {
    const r = scanContent(
      [ds({ name: " Bad ", projectName: "default", updatedAt: "2024-01-01T00:00:00.000Z" })],
      { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS },
    );
    expect(r.findings.map((f) => f.check)).toEqual(["stale", "default_project", "naming"]);
  });

  it("is deterministic — identical inputs yield identical output", () => {
    const items = [
      ds({ name: " x ", projectName: "Default", updatedAt: "2024-01-01T00:00:00.000Z" }),
      ds({ id: "b", name: "Fine", updatedAt: NOW }),
    ];
    const a = scanContent(items, { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    const b = scanContent(items, { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    expect(a).toEqual(b);
  });

  it("handles an empty list", () => {
    const r = scanContent([], { nowIso: NOW, staleDays: DEFAULT_STALE_DAYS });
    expect(r).toEqual({
      findings: [],
      summary: { scanned: 0, staleUndetermined: 0, byCheck: { stale: 0, default_project: 0, naming: 0 } },
    });
  });

  it("default naming pattern is exported and matches expected shapes", () => {
    expect(DEFAULT_NAMING_PATTERN.test("ok name")).toBe(false);
    expect(DEFAULT_NAMING_PATTERN.test("bad  name")).toBe(true);
  });
});
