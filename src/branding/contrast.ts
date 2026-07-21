/**
 * WCAG 2.x contrast-ratio checks for `brand.yaml` (external skill enhancement
 * backlog #3 — VizCritique-Pro's D5 rubric: 4.5:1 for normal text, 3:1 for
 * large text/UI components. See `docs/adr/0014-external-skill-analysis.md`).
 *
 * Pure and deterministic: relative-luminance + contrast-ratio math follows
 * the standard WCAG 2.x sRGB formula
 * (https://www.w3.org/TR/WCAG21/#dfn-relative-luminance). No I/O, no
 * randomness — same input always produces the same output.
 *
 * `checkBrandContrast()` NEVER throws and never fails a brand file: findings
 * are WARNINGS the caller (`validate_brand`) surfaces alongside the rest of
 * its report. A brand author may deliberately choose low-contrast colors
 * (e.g. a muted secondary accent) — this module surfaces the numbers, it
 * never blocks, mirroring `validate_brand`'s existing fail-soft philosophy
 * (see `src/tools/validateBrand.ts`).
 *
 * `BrandFileSchema` (`./schema.ts`) has no explicit "background" field —
 * every dashboard/KPI tile this codebase generates defaults to a white
 * canvas unless a design theme (a separate layer, `design/corpus/themes/
 * *.yaml`, resolved downstream of the brand) overrides it. `checkBrandContrast`
 * therefore checks every text/semantic color against an assumed white
 * (`#ffffff`) canvas by default; callers may pass an explicit `background`
 * to check against a themed canvas instead.
 */

import type { BrandFile, HexColor } from "./schema.js";

/** The assumed dashboard canvas background when the caller does not supply one — see module docstring. */
export const DEFAULT_CANVAS_BACKGROUND: HexColor = "#ffffff";

/** WCAG 2.x AA contrast ratio required for normal-size body text (< 18pt regular / < 14pt bold). */
const NORMAL_TEXT_RATIO = 4.5;
/** WCAG 2.x AA contrast ratio required for large text (>= 18pt regular / >= 14pt bold) or UI components. */
const LARGE_TEXT_OR_UI_RATIO = 3;

/** WCAG "large text" point-size floor for non-bold fonts. */
const LARGE_TEXT_MIN_SIZE_REGULAR_PT = 18;
/** WCAG "large text" point-size floor for bold fonts (e.g. "Tableau Bold"). */
const LARGE_TEXT_MIN_SIZE_BOLD_PT = 14;

/** A single checked foreground/background pair with its computed ratio and pass/fail verdict. */
export interface ContrastCheckResult {
  /** Human-readable label identifying what was checked, e.g. "typography.title.color on background". */
  pair: string;
  foreground: HexColor;
  background: HexColor;
  /** WCAG contrast ratio, rounded to 2 decimal places. Ranges from 1 (identical colors) to 21 (black vs. white). */
  ratio: number;
  /** The WCAG threshold this pair must meet: 4.5 (normal text) or 3 (large text/UI). */
  required: number;
  pass: boolean;
}

// ---------------------------------------------------------------------------
// Hex parsing
// ---------------------------------------------------------------------------

/** Expand a `#rgb` shorthand to `#rrggbb`; a 6-digit hex passes through unchanged. Returns the bare 6-char string (no `#`). */
function expandHex(hex: HexColor): string {
  const stripped = hex.slice(1);
  if (stripped.length === 3) {
    return stripped
      .split("")
      .map((channel) => channel + channel)
      .join("");
  }
  return stripped;
}

interface Rgb {
  r: number;
  g: number;
  b: number;
}

function hexToRgb(hex: HexColor): Rgb {
  const full = expandHex(hex);
  return {
    r: parseInt(full.slice(0, 2), 16),
    g: parseInt(full.slice(2, 4), 16),
    b: parseInt(full.slice(4, 6), 16),
  };
}

// ---------------------------------------------------------------------------
// WCAG 2.x relative luminance + contrast ratio
// ---------------------------------------------------------------------------

/** sRGB (0-255) -> linear-light channel conversion, per the WCAG 2.x relative-luminance formula. */
function linearizeChannel(channel8Bit: number): number {
  const c = channel8Bit / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG 2.x relative luminance for a hex color: 0 for pure black, 1 for pure white. */
export function relativeLuminance(hex: HexColor): number {
  const { r, g, b } = hexToRgb(hex);
  const linearR = linearizeChannel(r);
  const linearG = linearizeChannel(g);
  const linearB = linearizeChannel(b);
  return 0.2126 * linearR + 0.7152 * linearG + 0.0722 * linearB;
}

/**
 * WCAG 2.x contrast ratio between two colors, order-independent.
 * Ranges from 1:1 (identical colors) to 21:1 (pure black vs. pure white).
 */
export function contrastRatio(hexA: HexColor, hexB: HexColor): number {
  const luminanceA = relativeLuminance(hexA);
  const luminanceB = relativeLuminance(hexB);
  const lighter = Math.max(luminanceA, luminanceB);
  const darker = Math.min(luminanceA, luminanceB);
  return (lighter + 0.05) / (darker + 0.05);
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

// ---------------------------------------------------------------------------
// Large-text classification
// ---------------------------------------------------------------------------

/** Whether text at `sizePt` set in `fontName` qualifies as WCAG "large text" (lower threshold applies). */
function isLargeText(sizePt: number, fontName: string): boolean {
  const isBold = /bold/i.test(fontName);
  return isBold ? sizePt >= LARGE_TEXT_MIN_SIZE_BOLD_PT : sizePt >= LARGE_TEXT_MIN_SIZE_REGULAR_PT;
}

function checkPair(
  pair: string,
  foreground: HexColor,
  background: HexColor,
  required: number,
): ContrastCheckResult {
  const ratio = round2(contrastRatio(foreground, background));
  return { pair, foreground, background, ratio, required, pass: ratio >= required };
}

// ---------------------------------------------------------------------------
// checkBrandContrast
// ---------------------------------------------------------------------------

/**
 * Check a brand file's title/body text colors and semantic good/bad colors
 * against a canvas background (defaults to white — see module docstring).
 *
 * Returns every checked pair (pass AND fail), never throws:
 * - `typography.title.color` — uses the large-text/UI 3:1 threshold when its
 *   configured size/weight qualifies as WCAG "large text" (the shipped
 *   default, 24pt "Tableau Bold", does); otherwise the 4.5:1 normal-text
 *   threshold.
 * - `typography.body.color` — always the 4.5:1 normal-text threshold.
 * - `palette.semantic.good` / `palette.semantic.bad` — the 3:1 UI threshold
 *   (these color small delta arrows/status badges, not paragraph text).
 */
export function checkBrandContrast(
  brand: BrandFile,
  background: HexColor = DEFAULT_CANVAS_BACKGROUND,
): ContrastCheckResult[] {
  const { typography, palette } = brand;

  const titleRequired = isLargeText(typography.title.size, typography.title.font)
    ? LARGE_TEXT_OR_UI_RATIO
    : NORMAL_TEXT_RATIO;

  return [
    checkPair("typography.title.color on background", typography.title.color, background, titleRequired),
    checkPair("typography.body.color on background", typography.body.color, background, NORMAL_TEXT_RATIO),
    checkPair("palette.semantic.good on background", palette.semantic.good, background, LARGE_TEXT_OR_UI_RATIO),
    checkPair("palette.semantic.bad on background", palette.semantic.bad, background, LARGE_TEXT_OR_UI_RATIO),
  ];
}
