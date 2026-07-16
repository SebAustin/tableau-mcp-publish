/**
 * Brand file loading + persona resolution (Phase E1, Slice A).
 *
 * `loadBrand()` is the ONLY place in the branding layer that performs I/O —
 * everything else (schema validation, persona resolution) is pure. Callers
 * (MCP tools) own the decision of *when* to read the file; the planner never
 * calls this module directly (PLAN.md: "planner stays pure: pass resolved
 * values in").
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { BrandFileSchema, DEFAULT_BRAND, type BrandFile, type PersonaOverrides } from "./schema.js";
import type { Audience } from "../planner/schema.js";

// ---------------------------------------------------------------------------
// Default path resolution
// ---------------------------------------------------------------------------

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * Repo-root `brand.yaml`, resolved relative to this module's own location so
 * it works identically whether running from `src/` (tsx) or `dist/`
 * (compiled) — both are two directories below the repo root.
 */
export const DEFAULT_BRAND_PATH = resolve(MODULE_DIR, "..", "..", "brand.yaml");

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function isFileNotFoundError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as NodeJS.ErrnoException).code === "ENOENT"
  );
}

// ---------------------------------------------------------------------------
// loadBrand
// ---------------------------------------------------------------------------

export interface LoadBrandResult {
  brand: BrandFile;
  warnings: string[];
}

/**
 * Read, parse, and validate `brand.yaml`.
 *
 * - Absent file → `DEFAULT_BRAND` + a warning (not an error).
 * - Malformed YAML or a schema violation → throws with an actionable message
 *   (field path + expected shape) rather than silently falling back.
 * - Any field/section the file omits falls back to its schema default, which
 *   is equivalent to "merge onto DEFAULT_BRAND" since `DEFAULT_BRAND` is
 *   itself `BrandFileSchema.parse({})`.
 *
 * @param filePath - Path to brand.yaml. Defaults to the repo-root brand.yaml.
 */
export function loadBrand(filePath: string = DEFAULT_BRAND_PATH): LoadBrandResult {
  let raw: string;
  try {
    raw = readFileSync(filePath, "utf8");
  } catch (err: unknown) {
    if (isFileNotFoundError(err)) {
      return {
        brand: DEFAULT_BRAND,
        warnings: [`Brand file not found at "${filePath}"; using built-in defaults.`],
      };
    }
    throw new Error(`Failed to read brand file "${filePath}": ${getErrorMessage(err)}`);
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err: unknown) {
    throw new Error(
      `Malformed YAML in brand file "${filePath}": ${getErrorMessage(err)}. ` +
        "Check indentation and quoting, then try again.",
    );
  }

  // An empty or comment-only YAML file parses to `undefined`/`null` — treat as {}.
  const candidate = parsed ?? {};

  const result = BrandFileSchema.safeParse(candidate);
  if (!result.success) {
    const issues = result.error.issues
      .map((issue) => `  - ${issue.path.join(".") || "(root)"}: ${issue.message}`)
      .join("\n");
    throw new Error(
      `Invalid brand file "${filePath}":\n${issues}\n` +
        "Fix the listed field(s) — see the inline comments in brand.yaml for the expected shape.",
    );
  }

  return { brand: result.data, warnings: [] };
}

// ---------------------------------------------------------------------------
// resolvePersona
// ---------------------------------------------------------------------------

export interface ResolvedPersona {
  audience: Audience;
  overrides: PersonaOverrides;
}

/**
 * Resolve a named persona to its base audience + overrides.
 *
 * Lookup is case-insensitive. Throws a clear error listing every available
 * persona name when `personaName` doesn't match any entry.
 */
export function resolvePersona(brand: BrandFile, personaName: string): ResolvedPersona {
  const normalized = personaName.trim().toLowerCase();
  const entries = Object.entries(brand.personas);
  const match = entries.find(([name]) => name.toLowerCase() === normalized);

  if (!match) {
    const available = entries.map(([name]) => name).sort();
    throw new Error(
      `Unknown persona "${personaName}". ` +
        (available.length > 0
          ? `Available personas: ${available.join(", ")}.`
          : "No personas are configured in brand.yaml.") +
        " Add it to the personas: section of brand.yaml, or use one of the names above.",
    );
  }

  const [, overrides] = match;
  return { audience: overrides.base, overrides };
}
