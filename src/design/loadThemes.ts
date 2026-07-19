/**
 * Design-theme corpus loading (Design Excellence, Slice D5).
 *
 * `loadThemes()` is the ONLY place in the design layer that performs I/O —
 * `selectTheme()` (pure retrieval) and `toDesignTheme()` (pure mapping,
 * `./schema.js`) never touch the filesystem. Mirrors
 * `src/branding/load.ts`'s "only I/O here" discipline exactly.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { ThemeFileSchema, type ThemeFile } from "./schema.js";

// ---------------------------------------------------------------------------
// Default path resolution
// ---------------------------------------------------------------------------

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * Repo-root `design/corpus/themes/`, resolved relative to this module's own
 * location so it works identically whether running from `src/` (tsx) or
 * `dist/` (compiled) — both are two directories below the repo root, same
 * reasoning as `src/branding/load.ts`'s `DEFAULT_BRAND_PATH`.
 */
export const DEFAULT_THEMES_DIR = resolve(MODULE_DIR, "..", "..", "design", "corpus", "themes");

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function isDirNotFoundError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as NodeJS.ErrnoException).code === "ENOENT"
  );
}

// ---------------------------------------------------------------------------
// loadThemes
// ---------------------------------------------------------------------------

/**
 * Read, parse, and validate every `*.yaml` file in *dirPath* against
 * `ThemeFileSchema`.
 *
 * Unlike `loadBrand()` (which soft-falls-back to `DEFAULT_BRAND` on a
 * missing file), a missing/unreadable/invalid themes directory always
 * throws here with an actionable message — this function is a strict I/O +
 * validation boundary. Callers that want the corpus to be an *optional*
 * enhancement (design theming should never block a `design_dashboard` call)
 * catch the error at the call site and proceed without a theme — see
 * `src/tools/designDashboard.ts`.
 *
 * @param dirPath - Directory containing theme YAML files. Defaults to the
 *   repo-root `design/corpus/themes/`.
 */
export function loadThemes(dirPath: string = DEFAULT_THEMES_DIR): ThemeFile[] {
  let fileNames: string[];
  try {
    fileNames = readdirSync(dirPath).filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));
  } catch (err: unknown) {
    if (isDirNotFoundError(err)) {
      throw new Error(
        `Design theme directory not found at "${dirPath}". ` +
          "This ships as part of the package (design/corpus/themes/) — a missing directory " +
          "usually means a broken or stripped-down install.",
      );
    }
    throw new Error(`Failed to read design theme directory "${dirPath}": ${getErrorMessage(err)}`);
  }

  fileNames.sort();

  return fileNames.map((fileName) => {
    const filePath = join(dirPath, fileName);

    let raw: string;
    try {
      raw = readFileSync(filePath, "utf8");
    } catch (err: unknown) {
      throw new Error(`Failed to read theme file "${filePath}": ${getErrorMessage(err)}`);
    }

    let parsed: unknown;
    try {
      parsed = parseYaml(raw);
    } catch (err: unknown) {
      throw new Error(`Malformed YAML in theme file "${filePath}": ${getErrorMessage(err)}`);
    }

    const result = ThemeFileSchema.safeParse(parsed);
    if (!result.success) {
      const issues = result.error.issues
        .map((issue) => `  - ${issue.path.join(".") || "(root)"}: ${issue.message}`)
        .join("\n");
      throw new Error(`Invalid theme file "${filePath}":\n${issues}`);
    }

    return result.data;
  });
}
