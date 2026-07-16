import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect, afterEach } from "vitest";
import {
  BrandFileSchema,
  DEFAULT_BRAND,
  HexColorSchema,
} from "../src/branding/schema.js";
import { loadBrand, resolvePersona } from "../src/branding/load.js";

// ---------------------------------------------------------------------------
// Temp-file helpers
// ---------------------------------------------------------------------------

let tmpDir: string | undefined;

function writeTempYaml(contents: string): string {
  tmpDir = mkdtempSync(join(tmpdir(), "brand-test-"));
  const filePath = join(tmpDir, "brand.yaml");
  writeFileSync(filePath, contents, "utf8");
  return filePath;
}

afterEach(() => {
  if (tmpDir) {
    rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = undefined;
  }
});

// ---------------------------------------------------------------------------
// HexColorSchema
// ---------------------------------------------------------------------------

describe("HexColorSchema", () => {
  it("accepts 6-digit and 3-digit hex colors", () => {
    expect(HexColorSchema.safeParse("#59a14f").success).toBe(true);
    expect(HexColorSchema.safeParse("#5af").success).toBe(true);
  });

  it("rejects non-hex strings", () => {
    expect(HexColorSchema.safeParse("green").success).toBe(false);
    expect(HexColorSchema.safeParse("59a14f").success).toBe(false); // missing '#'
    expect(HexColorSchema.safeParse("#zzzzzz").success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// BrandFileSchema
// ---------------------------------------------------------------------------

describe("BrandFileSchema", () => {
  it("produces a fully-defaulted brand from an empty object", () => {
    const result = BrandFileSchema.parse({});
    expect(result.brand.name).toBe("My Company");
    expect(result.palette.semantic.good).toBe("#59a14f");
    expect(result.palette.semantic.bad).toBe("#e15759");
    expect(result.typography.title.font).toBe("Tableau Bold");
    expect(result.typography.body.font).toBe("Tableau Book");
    expect(result.formats.currency).toBe("$#,##0");
    expect(Object.keys(result.personas)).toContain("ceo");
  });

  it("DEFAULT_BRAND matches BrandFileSchema.parse({})", () => {
    expect(DEFAULT_BRAND).toEqual(BrandFileSchema.parse({}));
  });

  it("accepts a partial override and defaults every omitted sibling field", () => {
    const result = BrandFileSchema.parse({
      palette: { semantic: { good: "#00ff00" } },
    });
    expect(result.palette.semantic.good).toBe("#00ff00");
    // Siblings fall back to defaults
    expect(result.palette.semantic.bad).toBe("#e15759");
    expect(result.palette.categorical.length).toBeGreaterThan(0);
    expect(result.palette.sequential.length).toBeGreaterThanOrEqual(2);
  });

  it("rejects an invalid hex color with a field-path-scoped error", () => {
    const result = BrandFileSchema.safeParse({
      palette: { semantic: { good: "not-a-color" } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const paths = result.error.issues.map((i) => i.path.join("."));
      expect(paths).toContain("palette.semantic.good");
    }
  });

  it("rejects a persona with an invalid base audience", () => {
    const result = BrandFileSchema.safeParse({
      personas: { ceo: { base: "not-an-audience" } },
    });
    expect(result.success).toBe(false);
  });

  it("rejects a diverging palette that isn't exactly 3 stops", () => {
    const result = BrandFileSchema.safeParse({
      palette: { diverging: ["#e15759", "#59a14f"] },
    });
    expect(result.success).toBe(false);
  });

  it("accepts a fully custom persona with all optional overrides set", () => {
    const result = BrandFileSchema.parse({
      personas: {
        vip: {
          base: "exec",
          maxSheets: 2,
          kpiEmphasis: "high",
          preferredArtifact: "pulse",
          tone: "concise",
          chartDeny: ["pie", "scatter"],
          notes: "Handle with care.",
        },
      },
    });
    expect(result.personas["vip"]).toEqual({
      base: "exec",
      maxSheets: 2,
      kpiEmphasis: "high",
      preferredArtifact: "pulse",
      tone: "concise",
      chartDeny: ["pie", "scatter"],
      notes: "Handle with care.",
    });
  });
});

// ---------------------------------------------------------------------------
// loadBrand
// ---------------------------------------------------------------------------

describe("loadBrand", () => {
  it("returns DEFAULT_BRAND + a warning when the file is absent", () => {
    const missingPath = join(mkdtempSync(join(tmpdir(), "brand-missing-")), "does-not-exist.yaml");
    const { brand, warnings } = loadBrand(missingPath);
    expect(brand).toEqual(DEFAULT_BRAND);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toMatch(/not found/i);
  });

  it("loads and merges a valid partial YAML file onto the defaults", () => {
    const filePath = writeTempYaml(`
brand:
  name: "Acme Corp"
palette:
  semantic:
    good: "#00c853"
personas:
  ceo:
    base: exec
    maxSheets: 2
`);
    const { brand, warnings } = loadBrand(filePath);
    expect(warnings).toHaveLength(0);
    expect(brand.brand.name).toBe("Acme Corp");
    expect(brand.palette.semantic.good).toBe("#00c853");
    // Untouched sibling defaults still present
    expect(brand.palette.semantic.bad).toBe("#e15759");
    expect(brand.typography.title.font).toBe("Tableau Bold");
    expect(brand.personas["ceo"]).toEqual({ base: "exec", maxSheets: 2 });
  });

  it("throws an actionable error on malformed YAML syntax", () => {
    const filePath = writeTempYaml(`
brand:
  name: "Acme Corp
  logoPath: [unterminated
`);
    expect(() => loadBrand(filePath)).toThrow(/Malformed YAML/i);
  });

  it("throws an actionable, field-scoped error on a schema violation", () => {
    const filePath = writeTempYaml(`
palette:
  semantic:
    good: "not-a-hex-color"
`);
    expect(() => loadBrand(filePath)).toThrow(/palette\.semantic\.good/);
  });

  it("throws an actionable error when a persona has an invalid base audience", () => {
    const filePath = writeTempYaml(`
personas:
  ceo:
    base: not-a-real-audience
`);
    expect(() => loadBrand(filePath)).toThrow(/personas\.ceo\.base/);
  });

  it("treats an empty YAML file as {} and returns defaults with no warning", () => {
    const filePath = writeTempYaml("");
    const { brand, warnings } = loadBrand(filePath);
    expect(brand).toEqual(DEFAULT_BRAND);
    expect(warnings).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// resolvePersona
// ---------------------------------------------------------------------------

describe("resolvePersona", () => {
  it("resolves a known persona to its base audience + overrides", () => {
    const resolved = resolvePersona(DEFAULT_BRAND, "ceo");
    expect(resolved.audience).toBe("exec");
    expect(resolved.overrides.base).toBe("exec");
  });

  it("is case-insensitive", () => {
    const resolved = resolvePersona(DEFAULT_BRAND, "CEO");
    expect(resolved.audience).toBe("exec");
    const resolvedMixed = resolvePersona(DEFAULT_BRAND, "Analyst");
    expect(resolvedMixed.audience).toBe("analyst");
  });

  it("throws a clear error listing available persona names for an unknown persona", () => {
    expect(() => resolvePersona(DEFAULT_BRAND, "nonexistent")).toThrow(/Unknown persona "nonexistent"/);

    let message = "";
    try {
      resolvePersona(DEFAULT_BRAND, "nonexistent");
    } catch (err) {
      message = err instanceof Error ? err.message : String(err);
    }
    expect(message).toMatch(/ceo/);
    expect(message).toMatch(/analyst/);
  });
});
