import { describe, it, expect } from "vitest";
import { BrandFileSchema } from "../src/branding/schema.js";
import { toBuilderBrand } from "../src/branding/builderBrand.js";

// ---------------------------------------------------------------------------
// toBuilderBrand — pure projection from BrandFile (nested) to the flat wire
// shape the sidecar's DashboardWorkbookRequest.brand submodel expects.
// ---------------------------------------------------------------------------

describe("toBuilderBrand", () => {
  it("flattens palette.semantic.{good,bad,neutral} onto the top-level palette", () => {
    const brand = BrandFileSchema.parse({});
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand.palette.good).toBe(brand.palette.semantic.good);
    expect(builderBrand.palette.bad).toBe(brand.palette.semantic.bad);
    expect(builderBrand.palette.neutral).toBe(brand.palette.semantic.neutral);
    expect(builderBrand.palette.categorical).toEqual(brand.palette.categorical);
    expect(builderBrand.palette.sequential).toEqual(brand.palette.sequential);
    expect(builderBrand.palette.diverging).toEqual(brand.palette.diverging);
  });

  it("carries title/body font specs (font, size, color) through unchanged", () => {
    const brand = BrandFileSchema.parse({});
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand.typography.title).toEqual({
      font: brand.typography.title.font,
      size: brand.typography.title.size,
      color: brand.typography.title.color,
    });
    expect(builderBrand.typography.body).toEqual({
      font: brand.typography.body.font,
      size: brand.typography.body.size,
      color: brand.typography.body.color,
    });
  });

  it("carries the BAN font spec through without a color field", () => {
    const brand = BrandFileSchema.parse({});
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand.typography.ban).toEqual({
      font: brand.typography.ban.font,
      size: brand.typography.ban.size,
    });
    expect(builderBrand.typography.ban).not.toHaveProperty("color");
  });

  it("carries formats.{currency,percent,number} through unchanged (drops formats.compact)", () => {
    const brand = BrandFileSchema.parse({});
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand.formats).toEqual({
      currency: brand.formats.currency,
      percent: brand.formats.percent,
      number: brand.formats.number,
    });
    expect(builderBrand.formats).not.toHaveProperty("compact");
  });

  it("maps brand.brand.name to the flat brandName", () => {
    const brand = BrandFileSchema.parse({ brand: { name: "Acme Corp" } });
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand.brandName).toBe("Acme Corp");
  });

  it("reflects a fully custom brand end-to-end", () => {
    const brand = BrandFileSchema.parse({
      brand: { name: "Custom Corp" },
      palette: {
        categorical: ["#111111", "#222222"],
        semantic: { good: "#00ff00", bad: "#ff0000", neutral: "#888888" },
      },
      typography: {
        title: { font: "Custom Sans", size: 30, color: "#010101" },
      },
      formats: { currency: "€#,##0" },
    });
    const builderBrand = toBuilderBrand(brand);
    expect(builderBrand).toEqual({
      brandName: "Custom Corp",
      palette: {
        categorical: ["#111111", "#222222"],
        sequential: brand.palette.sequential,
        diverging: brand.palette.diverging,
        good: "#00ff00",
        bad: "#ff0000",
        neutral: "#888888",
      },
      typography: {
        title: { font: "Custom Sans", size: 30, color: "#010101" },
        body: {
          font: brand.typography.body.font,
          size: brand.typography.body.size,
          color: brand.typography.body.color,
        },
        ban: { font: brand.typography.ban.font, size: brand.typography.ban.size },
      },
      formats: {
        currency: "€#,##0",
        percent: brand.formats.percent,
        number: brand.formats.number,
      },
    });
  });
});
