import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerCreateDatasourceFromQuery(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_datasource_from_query",
    {
      title: "Create published datasource from a SQL query",
      description:
        "Run a SQL query against a connection (snowflake, postgres, or a local csv), materialize the result as a Hyper extract, package it as a .tdsx, and publish it to a Tableau Cloud project. One call: SQL in, governed published datasource out. Returns { datasourceLuid, url }.",
      inputSchema: {
        connection: z
          .object({ type: z.enum(["snowflake", "postgres", "csv"]) })
          .passthrough()
          .describe(
            "Connection descriptor. For snowflake/postgres include host/account, user, password/token, database, schema, warehouse as needed. For csv include { type: 'csv', path }.",
          ),
        sql: z.string().min(1).describe("SQL to execute (ignored for csv connections)."),
        datasourceName: z.string().min(1).describe("Name for the published datasource."),
        projectName: z.string().min(1).describe("Target project name (must already exist)."),
        overwrite: z.boolean().default(false).describe("Overwrite an existing datasource of the same name."),
        maxRows: z.number().int().positive().optional().describe("Row cap for the query (default 1,000,000)."),
      },
      outputSchema: { datasourceLuid: z.string(), url: z.string() },
    },
    async ({ connection, sql, datasourceName, projectName, overwrite, maxRows }) => {
      const { tdsxPath } = await ctx.sidecar.buildDatasourceFromQuery({
        connection,
        sql,
        name: datasourceName,
        maxRows,
      });
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, url } = await ctx.rest.publishDatasource(
        tdsxPath,
        datasourceName,
        projectId,
        overwrite,
      );
      return toolResult(`Published datasource "${datasourceName}" → ${url}`, {
        datasourceLuid: id,
        url,
      });
    },
  );
}
