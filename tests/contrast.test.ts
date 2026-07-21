import { describe, it, expect } from "vitest";
import { BrandFileSchema, DEFAULT_BRAND } from "../src/branding/schema.js";
import {
  checkBrandContrast,
  contrastRatio,
  relativeLuminance,
  DEFAULT_CANVAS_BACKGROUND,
} from "../src/branding/contrast.js";

// ---------------------------------------------------------------------------
// relativeLuminance / contrastRatio — pure WCAG 2.x math, known test vectors
// ---------------------------------------------------------------------------

describe("relativeLuminance", () => {
  it("returns 0 for pure black and 1 for pure white", () => {
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 6);
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 6);
  });

  it("expands 3-digit hex the same as its 6-digit equivalent", () => {
    expect(relativeLuminance("#5af")).toBeCloseTo(relativeLuminance("#55aaff"), 10);
  });
});

describe("contrastRatio", () => {
  it("returns exactly 21:1 for black vs. white (the maximum possible ratio)", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 6);
  });

  it("returns 1:1 for identical colors", () => {
    expect(contrastRatio("#59a14f", "#59a14f")).toBeCloseTo(1, 6);
  });

  it("is order-independent (foreground/background swap gives the same ratio)", () => {
    expect(contrastRatio("#4d4d4d", "#ffffff")).toBeCloseTo(contrastRatio("#ffffff", "#4d4d4d"), 10);
  });

  it("matches the well-known WebAIM AA-boundary gray (#767676 on white ~= 4.54:1)", () => {
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
  });

  it("matches a known below-AA-boundary gray (#949494 on white ~= 3.03:1)", () => {
    expect(contrastRatio("#949494", "#ffffff")).toBeCloseTo(3.03, 2);
  });

  it("computes a known mid-value pair (#59a14f default 'good' green on white ~= 3.16:1)", () => {
    expect(contrastRatio("#59a14f", "#ffffff")).toBeCloseTo(3.16, 2);
  });
});

// ---------------------------------------------------------------------------
// checkBrandContrast — structured {pair, ratio, required, pass} warnings
// ---------------------------------------------------------------------------

describe("checkBrandContrast", () => {
  it("checks title, body, and semantic good/bad against the default white canvas", () => {
    const results = checkBrandContrast(DEFAULT_BRAND);
    const pairs = results.map((r) => r.pair);
    expect(pairs).toEqual(
      expect.arrayContaining([
        "typography.title.color on background",
        "typography.body.color on background",
        "palette.semantic.good on background",
        "palette.semantic.bad on background",
      ]),
    );
    for (const result of results) {
      expect(result.background).toBe(DEFAULT_CANVAS_BACKGROUND);
      expect(typeof result.ratio).toBe("number");
      expect(typeof result.pass).toBe("boolean");
      expect([3, 4.5]).toContain(result.required);
    }
  });

  it("passes every pair for the shipped default brand (dark title/body text, saturated semantic colors on white)", () => {
    const results = checkBrandContrast(DEFAULT_BRAND);
    for (const result of results) {
      expect(result.pass).toBe(true);
    }
  });

  it("uses the large-text/UI 3:1 threshold for the default 24pt bold title", () => {
    const results = checkBrandContrast(DEFAULT_BRAND);
    const title = results.find((r) => r.pair === "typography.title.color on background");
    expect(title?.required).toBe(3);
  });

  it("uses the normal-text 4.5:1 threshold for body copy", () => {
    const results = checkBrandContrast(DEFAULT_BRAND);
    const body = results.find((r) => r.pair === "typography.body.color on background");
    expect(body?.required).toBe(4.5);
  });

  it("flags a deliberately low-contrast brand (light-gray title on a white canvas)", () => {
    const lowContrastBrand = BrandFileSchema.parse({
      typography: {
        title: { color: "#eeeeee" },
      },
    });
    const results = checkBrandContrast(lowContrastBrand);
    const title = results.find((r) => r.pair === "typography.title.color on background");
    expect(title?.pass).toBe(false);
    expect(title?.ratio).toBeLessThan(title?.required as number);
  });

  it("accepts an explicit background override instead of the white default", () => {
    const results = checkBrandContrast(DEFAULT_BRAND, "#000000");
    for (const result of results) {
      expect(result.background).toBe("#000000");
    }
    // The default dark title color (#1f1f1f) on a black background is a near-invisible pairing.
    const title = results.find((r) => r.pair === "typography.title.color on background");
    expect(title?.pass).toBe(false);
  });

  it("is a pure function: never throws, and does not mutate its input", () => {
    const snapshot = JSON.parse(JSON.stringify(DEFAULT_BRAND));
    expect(() => checkBrandContrast(DEFAULT_BRAND)).not.toThrow();
    expect(DEFAULT_BRAND).toEqual(snapshot);
  });
});
