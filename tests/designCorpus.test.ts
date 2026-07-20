/**
 * Tests for the design-excellence corpus (D0 — ADR-0013; extended by Slice T1's
 * top-100-corpus notable-construct append).
 *
 * Validates the *committed* corpus under `design/corpus/` — not code, data —
 * so this file reads real files from disk rather than constructing fixtures.
 *
 * Covers:
 *  1. Every `recipes/*.yaml` file zod-validates against its documented shape
 *     (see `design/corpus/SCHEMA.md`).
 *  2. Every `themes/*.yaml` file zod-validates against the theme schema.
 *  3. Every recipe entry's `source`/`source_file` and every theme's
 *     provenance `source` is drawn from the allowlist (the D0 10-workbook
 *     set + the T1 top-100-corpus sources actually cited by
 *     `sidecar/design_stats.py`'s notable-construct append — see
 *     `T1_NOTABLE_CONSTRUCT_ALLOWLIST` below).
 *  4. "theme-cites-recipe": every hex color literal in every theme file is
 *     present somewhere in the mined recipes (the corpus's "never invent a
 *     value" discipline, machine-enforced).
 *  5. Exactly the 4 expected theme names exist.
 *  6. Every audience (exec/analyst/operational/mixed) resolves to at least
 *     one theme via `tags.audiences`.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { describe, it, expect } from "vitest";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const CORPUS_DIR = join(REPO_ROOT, "design", "corpus");
const RECIPES_DIR = join(CORPUS_DIR, "recipes");
const THEMES_DIR = join(CORPUS_DIR, "themes");

// ---------------------------------------------------------------------------
// The D0 10-workbook allowlist (design/references/README.md +
// design/corpus/SCHEMA.md), extended in Slice T1 with the top-100-corpus
// source workbooks `sidecar/design_stats.py`'s `append_notable_constructs`
// step actually cited (see design/corpus/SCHEMA.md's "notable-construct
// append" section). Every `source`/`source_file` in the corpus must be drawn
// from this set.
// ---------------------------------------------------------------------------

const D0_WORKBOOK_ALLOWLIST = [
  "WB-117.twbx",
  "WB-118.twbx",
  "WB-114.twbx",
  "WB-015",
  "WB-093",
  "WB-095",
  "WB-133",
  "WB-058",
  "WB-062",
  "WB-063",
];

// Slice T1: 14 top-100-corpus sources cited by `append_notable_constructs`'s
// zone_styles.yaml (+3 entries) / chrome_rules.yaml (+22 entries) append —
// every one independently observed as a NEW, high-frequency (>=8 distinct
// source files) construct not already in the D0 corpus.
const T1_NOTABLE_CONSTRUCT_ALLOWLIST = [
  "WB-001.twbx",
  "WB-004.twbx",
  "WB-005.twbx",
  "WB-009.twbx",
  "WB-012.twbx",
  "WB-017.twbx",
  "WB-018.twbx",
  "WB-021.twbx",
  "WB-023.twbx",
  "WB-027.twbx",
  "WB-028.twbx",
  "WB-029.twbx",
  "WB-033.twbx",
  "WB-039.twbx",
];

const WORKBOOK_ALLOWLIST = new Set([...D0_WORKBOOK_ALLOWLIST, ...T1_NOTABLE_CONSTRUCT_ALLOWLIST]);

const SHA256_RE = /^[0-9a-f]{64}$/;
const XPATH_RE = /^\/workbook\//;

// ---------------------------------------------------------------------------
// recipes/*.yaml schemas
// ---------------------------------------------------------------------------

const ZoneStyleEntrySchema = z.object({
  zone_type: z.string().min(1),
  formats: z.record(z.string(), z.string()),
  source: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  xpath: z.string().regex(XPATH_RE),
});

const ChromeRuleFormatSchema = z.object({
  attr: z.string().min(1),
  value: z.string(),
  scope: z.string().optional(),
});

const ChromeRuleEntrySchema = z.object({
  scope: z.enum(["workbook", "worksheet", "dashboard"]),
  element: z.string(),
  formats: z.array(ChromeRuleFormatSchema).min(1),
  source: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  xpath: z.string().regex(XPATH_RE),
});

const ActionActivationSchema = z.object({
  type: z.string(),
  auto_clear: z.string().optional(),
});

const ActionSourceSchema = z.object({
  type: z.string(),
  worksheet: z.string(),
  dashboard: z.string().optional(),
});

const ActionCommandSchema = z.object({
  command: z.string().min(1),
  params: z.record(z.string(), z.string()),
});

const ActionEditParameterSchema = z.object({
  agg_type: z.string().optional(),
  clear_option: z.object({ type: z.string(), value: z.string().optional() }).optional(),
  params: z.record(z.string(), z.string()),
});

const ActionEntrySchema = z.object({
  kind: z.enum(["filter", "brush", "edit-parameter", "url"]),
  caption: z.string(),
  activation: ActionActivationSchema,
  source: ActionSourceSchema,
  command: ActionCommandSchema.optional(),
  link: z.record(z.string(), z.string()).optional(),
  edit_parameter: ActionEditParameterSchema.optional(),
  source_file: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  xpath: z.string().regex(XPATH_RE),
});

const PaletteEntrySchema = z.object({
  name: z.string(),
  type: z.string().min(1),
  colors: z.array(z.string()).min(1),
  source: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  xpath: z.string().regex(XPATH_RE),
});

const TextZoneRunSchema = z.object({
  bold: z.string().optional(),
  italic: z.string().optional(),
  fontcolor: z.string().optional(),
  fontname: z.string().optional(),
  fontsize: z.string().optional(),
  text: z.string(),
});

const TextZoneEntrySchema = z.object({
  runs: z.array(TextZoneRunSchema).min(1),
  zone_role_hint: z.enum(["title", "subtitle", "body"]),
  source: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  xpath: z.string().regex(XPATH_RE),
});

const RECIPE_SCHEMAS: Record<string, z.ZodTypeAny> = {
  zone_styles: z.array(ZoneStyleEntrySchema),
  chrome_rules: z.array(ChromeRuleEntrySchema),
  actions: z.array(ActionEntrySchema),
  palettes: z.array(PaletteEntrySchema),
  text_zones: z.array(TextZoneEntrySchema),
};

/** Recipe entries key their provenance basename under `source_file` (actions) or `source`. */
function provenanceSource(entry: Record<string, unknown>): string {
  return typeof entry.source_file === "string" ? entry.source_file : (entry.source as string);
}

// ---------------------------------------------------------------------------
// themes/*.yaml schema
// ---------------------------------------------------------------------------

const AudienceEnum = z.enum(["exec", "analyst", "operational", "mixed"]);
const ArtifactEnum = z.enum(["dashboard", "story"]);

const ThemeTagsSchema = z.object({
  audiences: z.array(AudienceEnum).min(1),
  personas: z.array(z.string()),
  artifacts: z.array(ArtifactEnum).min(1),
  mood: z.enum(["dark", "light"]),
});

const ProvenanceEntrySchema = z.object({
  source: z.string().min(1),
  sha256: z.string().regex(SHA256_RE),
  construct: z.string().min(1),
});

const SpacingSchema = z.object({
  outerMargin: z.string().optional(),
  gutter: z.string().optional(),
});

const ChartCardSchema = z.object({
  background: z.string().optional(),
  borderColor: z.string().optional(),
  borderStyle: z.string().optional(),
  borderWidth: z.string().optional(),
  padding: z.string().optional(),
  margin: z.string().optional(),
  cornerRadius: z.string().optional(),
});

const KpiTileSchema = z.object({
  background: z.string().optional(),
  banColor: z.string().optional(),
  padding: z.string().optional(),
  useSemanticDeltaColors: z.boolean().optional(),
});

const HeaderSchema = z.object({
  background: z.string().optional(),
  titleColor: z.string().optional(),
  subtitleColor: z.string().optional(),
});

const DatalabelSchema = z.object({
  fontSize: z.string().optional(),
  fontWeight: z.string().optional(),
  colorMode: z.string().optional(),
});

const ChromeSchema = z.object({
  hideGridlines: z.boolean().optional(),
  hideZeroline: z.boolean().optional(),
  hideAxisTicks: z.boolean().optional(),
  showMarkLabels: z.boolean().optional(),
  datalabel: DatalabelSchema.optional(),
});

const ThemeSchema = z.object({
  name: z.string().min(1),
  tags: ThemeTagsSchema,
  provenance: z.array(ProvenanceEntrySchema).min(1),
  dashboardBackground: z.string().optional(),
  spacing: SpacingSchema.optional(),
  chartCard: ChartCardSchema.optional(),
  kpiTile: KpiTileSchema.optional(),
  header: HeaderSchema.optional(),
  chrome: ChromeSchema.optional(),
});

type Theme = z.infer<typeof ThemeSchema>;

const EXPECTED_THEME_NAMES = [
  "analyst_clean",
  "executive_dark",
  "executive_light",
  "operational_plain",
].sort();

// ---------------------------------------------------------------------------
// Load helpers
// ---------------------------------------------------------------------------

function loadYaml(path: string): unknown {
  return parseYaml(readFileSync(path, "utf8"));
}

function recipeFileNames(): string[] {
  return readdirSync(RECIPES_DIR)
    .filter((f) => f.endsWith(".yaml"))
    .sort();
}

function themeFileNames(): string[] {
  return readdirSync(THEMES_DIR)
    .filter((f) => f.endsWith(".yaml"))
    .sort();
}

const HEX_LITERAL_RE = /#[0-9a-fA-F]{3,8}\b/g;

function loadAllRecipesRawText(): string {
  return recipeFileNames()
    .map((f) => readFileSync(join(RECIPES_DIR, f), "utf8"))
    .join("\n");
}

function loadThemes(): Theme[] {
  return themeFileNames().map((f) => ThemeSchema.parse(loadYaml(join(THEMES_DIR, f))));
}

// ---------------------------------------------------------------------------
// 1 & 3 — recipes/*.yaml: zod-valid + provenance allowlist
// ---------------------------------------------------------------------------

describe("design/corpus/recipes/*.yaml", () => {
  it("has exactly the 5 documented recipe files", () => {
    expect(recipeFileNames()).toEqual(
      ["actions.yaml", "chrome_rules.yaml", "palettes.yaml", "text_zones.yaml", "zone_styles.yaml"].sort(),
    );
  });

  for (const stem of Object.keys(RECIPE_SCHEMAS)) {
    describe(`${stem}.yaml`, () => {
      const filePath = join(RECIPES_DIR, `${stem}.yaml`);
      const data = loadYaml(filePath);
      const schema = RECIPE_SCHEMAS[stem];

      it("zod-validates against the documented recipe schema", () => {
        const result = schema.safeParse(data);
        if (!result.success) {
          throw new Error(
            `${stem}.yaml failed schema validation: ${JSON.stringify(result.error.issues.slice(0, 5), null, 2)}`,
          );
        }
      });

      it("is non-empty (the miner found at least one real construct)", () => {
        const parsed = data as Record<string, unknown>[];
        expect(Array.isArray(parsed)).toBe(true);
        expect(parsed.length).toBeGreaterThan(0);
      });

      it("every entry's provenance source is in the 10-workbook allowlist", () => {
        const parsed = data as Record<string, unknown>[];
        for (const entry of parsed) {
          const source = provenanceSource(entry);
          expect(WORKBOOK_ALLOWLIST.has(source), `unexpected source "${source}" in ${stem}.yaml`).toBe(
            true,
          );
        }
      });
    });
  }
});

// ---------------------------------------------------------------------------
// 2 & 3 & 5 — themes/*.yaml: zod-valid, provenance allowlist, exactly 4 names
// ---------------------------------------------------------------------------

describe("design/corpus/themes/*.yaml", () => {
  it("has exactly 4 theme files", () => {
    expect(themeFileNames()).toEqual([
      "analyst_clean.yaml",
      "executive_dark.yaml",
      "executive_light.yaml",
      "operational_plain.yaml",
    ]);
  });

  for (const fileName of [
    "analyst_clean.yaml",
    "executive_dark.yaml",
    "executive_light.yaml",
    "operational_plain.yaml",
  ]) {
    describe(fileName, () => {
      const filePath = join(THEMES_DIR, fileName);
      const data = loadYaml(filePath);

      it("zod-validates against the theme schema", () => {
        const result = ThemeSchema.safeParse(data);
        if (!result.success) {
          throw new Error(
            `${fileName} failed schema validation: ${JSON.stringify(result.error.issues, null, 2)}`,
          );
        }
      });

      it("every provenance source is in the 10-workbook allowlist", () => {
        const theme = ThemeSchema.parse(data);
        for (const entry of theme.provenance) {
          expect(
            WORKBOOK_ALLOWLIST.has(entry.source),
            `unexpected provenance source "${entry.source}" in ${fileName}`,
          ).toBe(true);
        }
      });

      it("has at least one provenance entry per hex literal it cites (non-empty provenance)", () => {
        const theme = ThemeSchema.parse(data);
        expect(theme.provenance.length).toBeGreaterThan(0);
      });
    });
  }

  it("exposes exactly the 4 expected theme names (name field, not just file count)", () => {
    const names = loadThemes()
      .map((t) => t.name)
      .sort();
    expect(names).toEqual(EXPECTED_THEME_NAMES);
  });

  it("analyst_clean is tagged as the default fallback direction (light, analyst audience)", () => {
    const theme = loadThemes().find((t) => t.name === "analyst_clean");
    expect(theme).toBeDefined();
    expect(theme?.tags.mood).toBe("light");
    expect(theme?.tags.audiences).toContain("analyst");
  });
});

// ---------------------------------------------------------------------------
// 4 — theme-cites-recipe: every hex literal in every theme exists in recipes/
// ---------------------------------------------------------------------------

describe("theme-cites-recipe (no invented literals)", () => {
  const recipesText = loadAllRecipesRawText().toLowerCase();

  for (const fileName of themeFileNames()) {
    it(`every hex color literal in ${fileName} appears in some recipes/*.yaml file`, () => {
      const themeText = readFileSync(join(THEMES_DIR, fileName), "utf8");
      const hexLiterals = [...new Set(themeText.match(HEX_LITERAL_RE) ?? [])];

      expect(hexLiterals.length, `${fileName} cites no hex literals at all`).toBeGreaterThan(0);

      const missing = hexLiterals.filter((hex) => !recipesText.includes(hex.toLowerCase()));
      expect(missing, `${fileName} cites hex literal(s) not found in any recipes/*.yaml file`).toEqual(
        [],
      );
    });
  }
});

// ---------------------------------------------------------------------------
// 6 — audience coverage: every audience resolves to >=1 theme
// ---------------------------------------------------------------------------

describe("audience -> theme coverage", () => {
  const themes = loadThemes();

  for (const audience of AudienceEnum.options) {
    it(`"${audience}" audience resolves to at least one theme by tags.audiences`, () => {
      const matches = themes.filter((t) => t.tags.audiences.includes(audience));
      expect(
        matches.length,
        `no theme declares audiences including "${audience}"`,
      ).toBeGreaterThan(0);
    });
  }
});
