/**
 * Maps the branding-layer `BrandFile` (nested, zod-validated) to the flat
 * wire shape the sidecar's `DashboardWorkbookRequest.brand` submodel expects
 * (Phase E1, Slice B — "Builder applies branding").
 *
 * This is a pure projection: no I/O, no defaults beyond what `BrandFileSchema`
 * already guarantees (every `BrandFile` field is populated, even when
 * `brand.yaml` is absent — see `schema.ts`'s `DEFAULT_BRAND`).
 */

import type { BrandFile } from "./schema.js";

export interface BuilderBrandPalette {
  categorical: string[];
  sequential: string[];
  diverging: string[];
  good: string;
  bad: string;
  neutral: string;
}

export interface BuilderBrandFontSpec {
  font: string;
  size: number;
  color: string;
}

/** BAN (Big Number / KPI hero figure) font spec — no color field. */
export interface BuilderBrandBanFontSpec {
  font: string;
  size: number;
}

export interface BuilderBrandTypography {
  title: BuilderBrandFontSpec;
  body: BuilderBrandFontSpec;
  ban: BuilderBrandBanFontSpec;
}

export interface BuilderBrandFormats {
  currency: string;
  percent: string;
  number: string;
}

/**
 * The flat brand shape sent to the sidecar (matches `server.BrandModel`,
 * camelCase on the wire via `sidecar.ts`'s `buildDashboardWorkbook`).
 */
export interface BuilderBrand {
  palette: BuilderBrandPalette;
  typography: BuilderBrandTypography;
  formats: BuilderBrandFormats;
  brandName: string;
}

/** Project a resolved `BrandFile` down to the wire shape the builder consumes. */
export function toBuilderBrand(brand: BrandFile): BuilderBrand {
  return {
    palette: {
      categorical: brand.palette.categorical,
      sequential: brand.palette.sequential,
      diverging: brand.palette.diverging,
      good: brand.palette.semantic.good,
      bad: brand.palette.semantic.bad,
      neutral: brand.palette.semantic.neutral,
    },
    typography: {
      title: {
        font: brand.typography.title.font,
        size: brand.typography.title.size,
        color: brand.typography.title.color,
      },
      body: {
        font: brand.typography.body.font,
        size: brand.typography.body.size,
        color: brand.typography.body.color,
      },
      ban: {
        font: brand.typography.ban.font,
        size: brand.typography.ban.size,
      },
    },
    formats: {
      currency: brand.formats.currency,
      percent: brand.formats.percent,
      number: brand.formats.number,
    },
    brandName: brand.brand.name,
  };
}
