/**
 * Zod schema for `brand.yaml` (Phase E1, Slice A — BI_DESIGN Pillar B).
 *
 * Every field in the brand file is OPTIONAL: `BrandFileSchema.parse({})`
 * produces a fully-populated, valid `BrandFile` (see `DEFAULT_BRAND` below).
 * This lets `loadBrand()` treat "file absent" and "file present but sparse"
 * identically — missing sections/fields fall back to these defaults, which
 * mirror the color choices already audited in the reference `.twbx` files
 * (`good`/`bad` KPI delta colors, Tableau's built-in "Tableau Bold"/"Tableau
 * Book" fonts).
 *
 * `personas` is a free-form map so brand-file authors can add named personas
 * (beyond the five scaffolded ones) without any code change — each persona's
 * `base` must resolve to one of the planner's existing audience profiles
 * (`AudienceEnum`, imported from `../planner/schema.js`), which keeps this
 * module downstream of — and never mutating — the planner's contract.
 */

import { z } from "zod";
import { AudienceEnum } from "../planner/schema.js";

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** A `#rgb` or `#rrggbb` hex color string. */
export const HexColorSchema = z
  .string()
  .regex(HEX_COLOR_RE, "must be a hex color like #59a14f or #5af");
export type HexColor = z.infer<typeof HexColorSchema>;

/** Build a font-spec schema with per-field defaults baked in. */
function fontSpecSchema(defaultFont: string, defaultSize: number, defaultColor: HexColor) {
  return z.object({
    font: z.string().min(1).default(defaultFont),
    size: z.number().positive().default(defaultSize),
    color: HexColorSchema.default(defaultColor),
  });
}
export type FontSpec = z.infer<ReturnType<typeof fontSpecSchema>>;

/** Build a BAN (Big Number, KPI hero figure) font-spec schema. */
function banFontSpecSchema(defaultFont: string, defaultSize: number) {
  return z.object({
    font: z.string().min(1).default(defaultFont),
    size: z.number().positive().default(defaultSize),
  });
}
export type BanFontSpec = z.infer<ReturnType<typeof banFontSpecSchema>>;

// ---------------------------------------------------------------------------
// brand: — company/team identity
// ---------------------------------------------------------------------------

export const BrandInfoSchema = z
  .object({
    name: z.string().min(1).default("My Company"),
    logoPath: z.string().optional(),
    logoUrl: z.string().optional(),
  })
  .default({});
export type BrandInfo = z.infer<typeof BrandInfoSchema>;

// ---------------------------------------------------------------------------
// palette: — categorical / sequential / diverging / semantic colors
// ---------------------------------------------------------------------------

/** Tableau 10 — the built-in classic categorical palette (includes good=#59a14f, bad=#e15759). */
const DEFAULT_CATEGORICAL: HexColor[] = [
  "#4e79a7",
  "#f28e2b",
  "#e15759",
  "#76b7b2",
  "#59a14f",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#bab0ac",
];

const DEFAULT_SEQUENTIAL: HexColor[] = ["#c6dbef", "#6baed6", "#08519c"];
const DEFAULT_DIVERGING: HexColor[] = ["#e15759", "#f2f2f2", "#59a14f"];

/** KPI-delta "good" green, matched against the audited reference workbook run colors. */
export const DEFAULT_GOOD: HexColor = "#59a14f";
/** KPI-delta "bad" red, matched against the audited reference workbook run colors. */
export const DEFAULT_BAD: HexColor = "#e15759";
/** Neutral gray for flat/unchanged deltas and secondary label text. */
export const DEFAULT_NEUTRAL: HexColor = "#898989";

export const SemanticPaletteSchema = z
  .object({
    good: HexColorSchema.default(DEFAULT_GOOD),
    bad: HexColorSchema.default(DEFAULT_BAD),
    neutral: HexColorSchema.default(DEFAULT_NEUTRAL),
  })
  .default({});
export type SemanticPalette = z.infer<typeof SemanticPaletteSchema>;

export const PaletteSchema = z
  .object({
    /** Distinguishes different categories (regions, segments, ...). Any length ≥ 1. */
    categorical: z.array(HexColorSchema).min(1).default(DEFAULT_CATEGORICAL),
    /** Low-to-high ramp for a single continuous measure. 2-3 stops. */
    sequential: z.array(HexColorSchema).min(2).max(3).default(DEFAULT_SEQUENTIAL),
    /** [negative, midpoint, positive] ramp for values that move around a center. Exactly 3 stops. */
    diverging: z.array(HexColorSchema).length(3).default(DEFAULT_DIVERGING),
    semantic: SemanticPaletteSchema,
  })
  .default({});
export type Palette = z.infer<typeof PaletteSchema>;

// ---------------------------------------------------------------------------
// typography: — title / body / BAN font specs
// ---------------------------------------------------------------------------

export const TypographySchema = z
  .object({
    title: fontSpecSchema("Tableau Bold", 24, "#1f1f1f").default({}),
    body: fontSpecSchema("Tableau Book", 11, "#4d4d4d").default({}),
    /** BAN = "Big Number", the large hero figure inside a KPI tile. */
    ban: banFontSpecSchema("Tableau Bold", 36).default({}),
  })
  .default({});
export type Typography = z.infer<typeof TypographySchema>;

// ---------------------------------------------------------------------------
// formats: — number/currency/percent display formats
// ---------------------------------------------------------------------------

export const FormatsSchema = z
  .object({
    currency: z.string().min(1).default("$#,##0"),
    percent: z.string().min(1).default("0.0%"),
    number: z.string().min(1).default("#,##0"),
    /** Abbreviate large numbers (1.2M instead of 1,234,567) in KPI tiles/axis labels. */
    compact: z.boolean().default(true),
  })
  .default({});
export type Formats = z.infer<typeof FormatsSchema>;

// ---------------------------------------------------------------------------
// rules: — free-text design do's and don'ts (documentational only)
// ---------------------------------------------------------------------------

const DEFAULT_RULES: string[] = [
  "Do lead every executive dashboard with a KPI band of 3-4 metrics.",
  "Do use direct labeling on bars instead of a legend when there are 5 or fewer categories.",
  "Do use the sequential palette for magnitude, never the categorical palette.",
  "Don't use pie charts — prefer bar charts or 100% stacked bars.",
  "Don't use gauges — prefer bullet graphs (Stephen Few).",
];

export const RulesSchema = z.array(z.string()).default(DEFAULT_RULES);

// ---------------------------------------------------------------------------
// personas: — named audience profiles, extensible without code changes
// ---------------------------------------------------------------------------

export const PersonaOverridesSchema = z.object({
  /** The underlying planner audience profile this persona builds on. */
  base: AudienceEnum,
  /** Override the audience's default max chart-sheet count. */
  maxSheets: z.number().int().positive().max(50).optional(),
  /** How prominent the KPI band should be (documentational hint). */
  kpiEmphasis: z.enum(["high", "medium", "low"]).optional(),
  /** Artifact type this persona prefers when the agent has a choice. */
  preferredArtifact: z.enum(["dashboard", "story", "pulse"]).optional(),
  /** Wording style for generated titles/summaries. */
  tone: z.enum(["concise", "detailed"]).optional(),
  /** Chart/mark types this persona never wants to see. */
  chartDeny: z.array(z.string()).optional(),
  /** Free-text reminder for anyone designing for this persona. */
  notes: z.string().optional(),
});
export type PersonaOverrides = z.infer<typeof PersonaOverridesSchema>;

/** Minimal built-in personas used only when the whole `personas:` section is absent. */
const DEFAULT_PERSONAS: Record<string, PersonaOverrides> = {
  ceo: { base: "exec" },
  cto: { base: "exec" },
  slt_manager: { base: "mixed" },
  analyst: { base: "analyst" },
  client: { base: "operational" },
};

export const PersonasSchema = z.record(z.string(), PersonaOverridesSchema).default(DEFAULT_PERSONAS);
export type Personas = z.infer<typeof PersonasSchema>;

// ---------------------------------------------------------------------------
// BrandFile — the whole document
// ---------------------------------------------------------------------------

export const BrandFileSchema = z.object({
  brand: BrandInfoSchema,
  palette: PaletteSchema,
  typography: TypographySchema,
  formats: FormatsSchema,
  rules: RulesSchema,
  personas: PersonasSchema,
});
export type BrandFile = z.infer<typeof BrandFileSchema>;

/** Fully-defaulted brand used whenever no brand.yaml is found or a field is omitted. */
export const DEFAULT_BRAND: BrandFile = BrandFileSchema.parse({});
