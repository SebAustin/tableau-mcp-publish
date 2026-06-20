import type { Config } from "../config.js";
import type { TableauRestClient } from "../restClient.js";
import type { AuthoringSidecar } from "../sidecar.js";

/** Shared dependencies handed to every tool registration function. */
export interface ToolContext {
  config: Config;
  rest: TableauRestClient;
  sidecar: AuthoringSidecar;
}

/** Build a tool result with a human-readable line plus machine-readable structured content. */
export function toolResult(
  text: string,
  structuredContent: Record<string, unknown>,
): { content: { type: "text"; text: string }[]; structuredContent: Record<string, unknown> } {
  return { content: [{ type: "text", text }], structuredContent };
}
