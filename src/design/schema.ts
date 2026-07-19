/**
 * Zod schema for a `design/corpus/themes/*.yaml` theme file (Design
 * Excellence, Slice D5).
 *
 * The corpus stores every numeric literal (padding, margin, font size, ...)
 * as a YAML **string** (e.g. `padding: "25"`) — see
 * `design/corpus/SCHEMA.md`'s `themes/*.yaml` shape — because every value is
 * copied verbatim from a mined `<format attr='...' value='...'/>` XML
 * attribute, which is itself always a string. `ThemeFileSchema` coerces
 * those numeric-looking strings to real numbers with `z.coerce.number()` so
 * `toDesignTheme()` can hand them directly to the wire `DesignThemeSchema`
 * (`../planner/schema.js`), which wants numbers (it is consumed by
 * `sidecar/server.py`'s Pydantic `int | None` fields).
 *
 * This module performs NO I/O — `loadThemes.ts` is the only place in this
 * layer that touches the filesystem (mirrors `src/branding/schema.ts` +
 * `src/branding/load.ts`'s split).
 */

import { z } from "zod";
import { AudienceEnum, type DesignTheme } from "../planner/schema.js";

// ---------------------------------------------------------------------------
// tags
// ---------------------------------------------------------------------------

export const ArtifactEnum = z.enum(["dashboard", "story"]);
export type Artifact = z.infer<typeof ArtifactEnum>;

export const ThemeTagsSchema = z.object({
  audiences: z.array(AudienceEnum).min(1),
  personas: z.array(z.string()),
  artifacts: z.array(ArtifactEnum).min(1),
  mood: z.enum(["dark", "light"]),
});
export type ThemeTags = z.infer<typeof ThemeTagsSchema>;

// ---------------------------------------------------------------------------
// provenance
// ---------------------------------------------------------------------------

export const ThemeProvenanceEntrySchema = z.object({
  source: z.string().min(1),
  sha256: z.string().min(1),
  construct: z.string().min(1),
});
export type ThemeProvenanceEntry = z.infer<typeof ThemeProvenanceEntrySchema>;

// ---------------------------------------------------------------------------
// style blocks (numeric-as-string YAML values, coerced to numbers)
// ---------------------------------------------------------------------------

const ThemeSpacingFileSchema = z.object({
  outerMargin: z.coerce.number().optional(),
  gutter: z.coerce.number().optional(),
});

/**
 * `chartCard`'s flat `borderColor`/`borderStyle`/`borderWidth` keys (the
 * corpus's own YAML shape — see `design/corpus/SCHEMA.md`) are nested into
 * the wire `ThemeChartCardSchema`'s `border: {color, style, width}` object
 * by `toDesignTheme()` below.
 */
const ThemeChartCardFileSchema = z.object({
  background: z.string().optional(),
  borderColor: z.string().optional(),
  borderStyle: z.string().optional(),
  borderWidth: z.coerce.number().optional(),
  padding: z.coerce.number().optional(),
  margin: z.coerce.number().optional(),
  cornerRadius: z.coerce.number().optional(),
});

const ThemeKpiTileFileSchema = z.object({
  background: z.string().optional(),
  banColor: z.string().optional(),
  padding: z.coerce.number().optional(),
  useSemanticDeltaColors: z.boolean().optional(),
});

const ThemeHeaderFileSchema = z.object({
  background: z.string().optional(),
  titleColor: z.string().optional(),
  subtitleColor: z.string().optional(),
});

const ThemeDatalabelFileSchema = z.object({
  fontSize: z.coerce.number().optional(),
  fontWeight: z.string().optional(),
  colorMode: z.string().optional(),
});

const ThemeChromeFileSchema = z.object({
  hideGridlines: z.boolean().optional(),
  hideZeroline: z.boolean().optional(),
  hideAxisTicks: z.boolean().optional(),
  showMarkLabels: z.boolean().optional(),
  datalabel: ThemeDatalabelFileSchema.optional(),
});

// ---------------------------------------------------------------------------
// ThemeFileSchema
// ---------------------------------------------------------------------------

export const ThemeFileSchema = z.object({
  name: z.string().min(1),
  tags: ThemeTagsSchema,
  provenance: z.array(ThemeProvenanceEntrySchema).min(1),
  dashboardBackground: z.string().optional(),
  spacing: ThemeSpacingFileSchema.optional(),
  chartCard: ThemeChartCardFileSchema.optional(),
  kpiTile: ThemeKpiTileFileSchema.optional(),
  header: ThemeHeaderFileSchema.optional(),
  chrome: ThemeChromeFileSchema.optional(),
});
export type ThemeFile = z.infer<typeof ThemeFileSchema>;

// ---------------------------------------------------------------------------
// toDesignTheme — ThemeFile (corpus YAML shape) -> DesignTheme (wire shape)
// ---------------------------------------------------------------------------

/**
 * Map a parsed `ThemeFile` (corpus YAML shape, numeric fields already
 * coerced to numbers) onto the wire `DesignTheme` block
 * (`src/planner/schema.ts`'s `DesignThemeSchema`) that `DashboardPlan`
 * carries and the sidecar's `DesignThemeModel` consumes.
 *
 * The only structural change is `chartCard`'s flat `borderColor`/
 * `borderStyle`/`borderWidth` keys nesting into a single `border` object —
 * every other field maps 1:1 by name. Fields the theme file omits are
 * omitted from the result entirely (never written as `undefined`-valued
 * keys or invented placeholder values).
 */
export function toDesignTheme(themeFile: ThemeFile): DesignTheme {
  const { chartCard, kpiTile } = themeFile;

  const hasBorder =
    chartCard?.borderColor !== undefined ||
    chartCard?.borderStyle !== undefined ||
    chartCard?.borderWidth !== undefined;

  return {
    name: themeFile.name,
    ...(themeFile.dashboardBackground !== undefined
      ? { dashboardBackground: themeFile.dashboardBackground }
      : {}),
    ...(themeFile.spacing !== undefined ? { spacing: themeFile.spacing } : {}),
    ...(chartCard !== undefined
      ? {
          chartCard: {
            ...(chartCard.background !== undefined ? { background: chartCard.background } : {}),
            ...(hasBorder
              ? {
                  border: {
                    ...(chartCard.borderColor !== undefined
                      ? { color: chartCard.borderColor }
                      : {}),
                    ...(chartCard.borderStyle !== undefined
                      ? { style: chartCard.borderStyle }
                      : {}),
                    ...(chartCard.borderWidth !== undefined
                      ? { width: chartCard.borderWidth }
                      : {}),
                  },
                }
              : {}),
            ...(chartCard.padding !== undefined ? { padding: chartCard.padding } : {}),
            ...(chartCard.margin !== undefined ? { margin: chartCard.margin } : {}),
            ...(chartCard.cornerRadius !== undefined
              ? { cornerRadius: chartCard.cornerRadius }
              : {}),
          },
        }
      : {}),
    ...(kpiTile !== undefined
      ? {
          kpiTile: {
            ...(kpiTile.background !== undefined ? { background: kpiTile.background } : {}),
            ...(kpiTile.banColor !== undefined ? { banColor: kpiTile.banColor } : {}),
            ...(kpiTile.padding !== undefined ? { padding: kpiTile.padding } : {}),
            ...(kpiTile.useSemanticDeltaColors !== undefined
              ? { useSemanticDeltaColors: kpiTile.useSemanticDeltaColors }
              : {}),
          },
        }
      : {}),
    ...(themeFile.header !== undefined ? { header: themeFile.header } : {}),
    ...(themeFile.chrome !== undefined ? { chrome: themeFile.chrome } : {}),
  };
}
