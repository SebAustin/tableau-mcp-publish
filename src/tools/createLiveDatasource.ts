/**
 * create_live_datasource — Phase E2 slice C (data connectivity).
 *
 * Publishes a LIVE Cloud connection (Snowflake or Presto/Trino) — no extract,
 * no locally materialized data. The sidecar builds a `.tds` that carries only
 * connection topology (server/warehouse/schema/database/table); credentials
 * are embedded separately at publish time via Tableau's
 * `<connectionCredentials>` element (`rest/credentials.ts`) and are never
 * written into the `.tds` file or logged.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import type { LiveConnectionSpec } from "../sidecar.js";

const KNOWN_IMPOSSIBLE_SNOWFLAKE_AUTH = new Set(["key-pair", "keypair", "key_pair", "jwt"]);
const SUPPORTED_SNOWFLAKE_AUTH = new Set(["username-password", "oauth"]);

const snowflakeConnectionSchema = z.object({
  type: z.literal("snowflake"),
  server: z.string().min(1).describe('Account host, e.g. "myaccount.snowflakecomputing.com".'),
  schema: z.string().min(1).describe("Snowflake schema name."),
  table: z.string().min(1).describe("Table this datasource binds to."),
  warehouse: z.string().min(1).describe("Snowflake virtual warehouse name."),
  dbname: z.string().min(1).describe("Snowflake database name."),
  authentication: z
    .string()
    .default("username-password")
    .describe(
      '"username-password" (default) or "oauth". Key-pair auth is IMPOSSIBLE over REST — ' +
        "this tool throws a clear error rather than silently failing; configure it in Tableau " +
        "Desktop and publish from there instead.",
    ),
  role: z.string().optional().describe("Optional Snowflake role to assume."),
});

const prestoConnectionSchema = z.object({
  type: z.literal("presto"),
  server: z.string().min(1).describe("Presto/Trino coordinator host."),
  schema: z.string().min(1).describe("Presto schema name."),
  table: z.string().min(1).describe("Table this datasource binds to."),
  port: z.number().int().positive().default(8080),
  catalog: z.string().min(1).describe("Presto catalog name."),
  ssl: z.boolean().default(true),
  useRemoteQueryAgent: z
    .boolean()
    .default(true)
    .describe(
      "Presto/Trino is generally Tableau-Bridge-dependent on Cloud (Cloud usually cannot reach " +
        "a private-network Presto cluster directly). Defaults to true; set false only when this " +
        "endpoint is internet-reachable and allowlisted.",
    ),
});

const connectionSchema = z.discriminatedUnion("type", [
  snowflakeConnectionSchema,
  prestoConnectionSchema,
]);

/** Validates the Snowflake authentication mode, throwing a clean, actionable error for the impossible cases. */
function assertSnowflakeAuthIsPublishable(authentication: string): void {
  const normalized = authentication.trim().toLowerCase().replace(/[\s_]+/g, "-");
  if (KNOWN_IMPOSSIBLE_SNOWFLAKE_AUTH.has(normalized)) {
    throw new Error(
      `Snowflake key-pair authentication ("${authentication}") is not REST-publishable: ` +
        "Tableau's Publish Datasource API only supports embedding username/password or OAuth " +
        "credentials. Configure key-pair auth in Tableau Desktop and publish from there instead.",
    );
  }
  if (!SUPPORTED_SNOWFLAKE_AUTH.has(normalized)) {
    throw new Error(
      `Unsupported Snowflake authentication "${authentication}". Use "username-password" or "oauth".`,
    );
  }
}

export function registerCreateLiveDatasource(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_live_datasource",
    {
      title: "Create a live-connection Cloud datasource (Snowflake / Presto)",
      description:
        "Publish a LIVE Cloud connection (Snowflake or Presto/Trino) as a Tableau datasource — " +
        "no extract, no locally materialized data: the .tds carries only connection topology " +
        "(server/warehouse/schema/database/table). Credentials are embedded separately at " +
        "publish time via Tableau's <connectionCredentials> element and are NEVER written into " +
        "the .tds file or logged — source `credentials.username`/`credentials.password` from " +
        "your environment, never hardcode them in a prompt or plan. " +
        "VERIFY-LIVE: the exact XML attribute spelling for both connector classes (see " +
        "sidecar/tds_builder.py SNOWFLAKE_ATTRS/PRESTO_ATTRS) is this project's best-documented " +
        "mapping and has not yet been confirmed against a Desktop-exported .tds — treat as best " +
        "effort until verified against a live site. " +
        "IMPOSSIBLE: Snowflake key-pair authentication cannot be published over REST (Tableau's " +
        "Publish Datasource API only accepts embedded username/password or OAuth credentials); " +
        "this tool throws a clear error instead of silently failing — configure key-pair auth in " +
        "Tableau Desktop and publish from there instead. " +
        "CAVEAT: Presto/Trino is generally Tableau-Bridge-dependent on Cloud (Cloud usually " +
        "cannot reach a private-network Presto cluster directly) — connection.useRemoteQueryAgent " +
        "defaults to true; set it false only when the endpoint is internet-reachable and " +
        "allowlisted. The returned `note` always restates the relevant caveat for the connection " +
        "type used. " +
        "Returns { datasourceLuid, url, note }.",
      inputSchema: {
        name: z.string().min(1).describe("Datasource display name in Tableau."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        connection: connectionSchema.describe(
          "Typed connection topology — discriminated by `type` (snowflake | presto).",
        ),
        credentials: z.object({
          username: z
            .string()
            .min(1)
            .describe("Database username. Recommended: source from environment, never hardcode."),
          password: z
            .string()
            .min(1)
            .describe(
              "Database password/secret. Recommended: source from environment, never hardcode or log.",
            ),
        }),
        overwrite: z.boolean().default(false),
      },
      outputSchema: {
        datasourceLuid: z.string(),
        url: z.string(),
        note: z.string(),
      },
    },
    async ({ name, projectName, connection, credentials, overwrite }) => {
      // `useRemoteQueryAgent` (presto only) is a publish-time REST attribute, not
      // part of the .tds connection topology — build the sidecar payload without it
      // so the sidecar/tds_builder boundary stays clean (VDS-facing spec only).
      const sidecarConnection: LiveConnectionSpec =
        connection.type === "snowflake"
          ? {
              type: "snowflake",
              server: connection.server,
              schema: connection.schema,
              table: connection.table,
              warehouse: connection.warehouse,
              dbname: connection.dbname,
              authentication: connection.authentication,
              ...(connection.role !== undefined ? { role: connection.role } : {}),
            }
          : {
              type: "presto",
              server: connection.server,
              schema: connection.schema,
              table: connection.table,
              port: connection.port,
              catalog: connection.catalog,
              ssl: connection.ssl,
            };

      if (connection.type === "snowflake") {
        assertSnowflakeAuthIsPublishable(connection.authentication);
      }

      const { tdsPath } = await ctx.sidecar.buildLiveDatasource({
        name,
        connection: sidecarConnection,
      });

      const projectId = await ctx.rest.resolveProjectId(projectName);
      const isOauth = connection.type === "snowflake" && connection.authentication === "oauth";
      const { id, url } = await ctx.rest.publishDatasource(tdsPath, name, projectId, overwrite, {
        credentials: {
          username: credentials.username,
          password: credentials.password,
          embed: true,
          oauth: isOauth,
        },
        ...(connection.type === "presto"
          ? { useRemoteQueryAgent: connection.useRemoteQueryAgent }
          : {}),
      });

      const note =
        connection.type === "snowflake"
          ? "Snowflake connections are cloud-reachable with embedded credentials: schedule_refresh " +
            "can run this datasource's recurring refresh directly on Tableau Cloud."
          : "Presto/Trino connections are generally Bridge-dependent on Tableau Cloud: " +
            "schedule_refresh will accept a schedule, but Cloud can only execute it if this " +
            `endpoint is reachable and allowlisted, or Tableau Bridge is configured ` +
            `(useRemoteQueryAgent=${String(connection.useRemoteQueryAgent)} was set on this ` +
            "publish). Verify Bridge connectivity before relying on scheduled runs.";

      return toolResult(`Published live datasource "${name}" (${connection.type}) → ${url}`, {
        datasourceLuid: id,
        url,
        note,
      });
    },
  );
}
