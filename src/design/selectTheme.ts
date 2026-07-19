/**
 * Deterministic design-theme retrieval (Design Excellence, Slice D5).
 *
 * `selectTheme()` is the "retrieval" half of the corpus's deterministic,
 * tag-based "RAG" (see `docs/adr/0013-design-corpus-and-theme-layer.md` and
 * `design/corpus/SCHEMA.md`): a PURE function over each theme's `tags` — no
 * I/O, no randomness, same discipline as `src/planner/audience.ts`'s
 * `applyAudienceClamps`.
 *
 * Precedence (highest to lowest):
 *   1. persona-tag match  — `personaName` appears in `theme.tags.personas`
 *      AND `artifact` appears in `theme.tags.artifacts`.
 *   2. audience-tag match — `audience` appears in `theme.tags.audiences`
 *      AND `artifact` appears in `theme.tags.artifacts`.
 *   3. the `analyst_clean` fallback, by `name`, regardless of `artifact`
 *      (the corpus's documented default direction — see
 *      `design/corpus/SCHEMA.md`'s "Retrieval" section).
 *   4. alphabetical-first among whatever themes were supplied.
 *
 * Within each tier, ties are broken alphabetically by theme `name` — themes
 * are sorted once up front, so `Array.prototype.find` naturally returns the
 * alphabetically-first match for that tier.
 */

import type { Audience } from "../planner/schema.js";
import type { Artifact, ThemeFile } from "./schema.js";

export interface SelectThemeInput {
  audience: Audience;
  personaName?: string;
  artifact: Artifact;
}

function sortByName(themes: readonly ThemeFile[]): ThemeFile[] {
  return [...themes].sort((a, b) => a.name.localeCompare(b.name));
}

function findByPersona(
  themes: readonly ThemeFile[],
  personaName: string,
  artifact: Artifact,
): ThemeFile | undefined {
  const normalized = personaName.trim().toLowerCase();
  return themes.find(
    (t) =>
      t.tags.artifacts.includes(artifact) &&
      t.tags.personas.some((p) => p.toLowerCase() === normalized),
  );
}

function findByAudience(
  themes: readonly ThemeFile[],
  audience: Audience,
  artifact: Artifact,
): ThemeFile | undefined {
  return themes.find(
    (t) => t.tags.artifacts.includes(artifact) && t.tags.audiences.includes(audience),
  );
}

function findFallback(themes: readonly ThemeFile[]): ThemeFile | undefined {
  return themes.find((t) => t.name === "analyst_clean");
}

/**
 * Select a theme from *themes* for the given resolution input.
 *
 * @throws if *themes* is empty — callers (`designDashboard.ts`) only invoke
 *   this after confirming `loadThemes()` returned at least one theme.
 */
export function selectTheme(themes: readonly ThemeFile[], input: SelectThemeInput): ThemeFile {
  if (themes.length === 0) {
    throw new Error("selectTheme: no themes available to select from.");
  }

  const sorted = sortByName(themes);

  if (input.personaName) {
    const personaMatch = findByPersona(sorted, input.personaName, input.artifact);
    if (personaMatch) return personaMatch;
  }

  const audienceMatch = findByAudience(sorted, input.audience, input.artifact);
  if (audienceMatch) return audienceMatch;

  const fallback = findFallback(sorted);
  if (fallback) return fallback;

  const [first] = sorted;
  if (!first) {
    // Unreachable (the length check above guarantees at least one entry),
    // but keeps this function honest under `noUncheckedIndexedAccess`.
    throw new Error("selectTheme: no themes available to select from.");
  }
  return first;
}
