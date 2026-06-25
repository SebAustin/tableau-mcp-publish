import { z } from "zod";

/**
 * Runtime configuration, sourced entirely from environment variables.
 *
 * The first four variables are deliberately identical to the official
 * `@tableau/mcp-server` (SERVER, SITE_NAME, PAT_NAME, PAT_VALUE) so a single MCP
 * client config can run both servers side by side with one Personal Access Token.
 */
const ConfigSchema = z.object({
  /** Full Tableau Cloud/Server URL, e.g. https://10ax.online.tableau.com */
  server: z.string().url(),
  /** Site content URL. Empty string targets the Default site. */
  siteName: z.string(),
  /** Personal Access Token name. */
  patName: z.string().min(1),
  /** Personal Access Token secret. Never logged. */
  patValue: z.string().min(1),
  /** Tableau REST API version. */
  apiVersion: z.string().default("3.28"),
  /** Loopback host for the Python authoring sidecar. */
  sidecarHost: z.string().default("127.0.0.1"),
  /**
   * Loopback port for the Python authoring sidecar.
   * When absent the sidecar picks a free ephemeral port automatically.
   */
  sidecarPort: z.coerce.number().int().positive().optional(),
});

export type Config = z.infer<typeof ConfigSchema>;

/**
 * Load and validate configuration. Throws a readable error (without leaking the
 * PAT) if a required variable is missing or malformed.
 */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const result = ConfigSchema.safeParse({
    server: env.SERVER,
    siteName: env.SITE_NAME,
    patName: env.PAT_NAME,
    patValue: env.PAT_VALUE,
    apiVersion: env.TABLEAU_API_VERSION,
    sidecarHost: env.SIDECAR_HOST,
    sidecarPort: env.SIDECAR_PORT,
  });

  if (!result.success) {
    const issues = result.error.issues
      // Never echo the offending value — only the path and message — so a
      // malformed PAT can't leak into logs.
      .map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("\n");
    throw new Error(
      `Invalid configuration. Set SERVER, SITE_NAME, PAT_NAME, PAT_VALUE in the environment.\n${issues}`,
    );
  }

  return result.data;
}
