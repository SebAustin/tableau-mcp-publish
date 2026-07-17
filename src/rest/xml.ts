/**
 * Small XML helpers shared across `restClient.ts` and the `rest/*` request-body
 * builders (`schedules.ts`, `webhooks.ts`, …). Kept dependency-free and
 * Tableau-agnostic so any REST module can reuse them without a circular import
 * back through `restClient.ts`.
 */

/** Escape the five XML predefined entities so any string is safe as attribute or text content. */
export function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Tableau's XML→JSON mapping collapses a single repeated child to a bare
 * object instead of a 1-element array (only 2+ children become an array).
 * Normalize both shapes to an array so callers never special-case the
 * single-item response.
 */
export function asArray<T>(value: T | T[] | undefined | null): T[] {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}
