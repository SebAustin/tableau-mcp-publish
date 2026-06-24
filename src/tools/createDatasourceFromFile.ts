/**
 * create_datasource_from_file — M2
 *
 * Reads a local file (CSV, JSON, JSONL, Excel, Parquet) into a Hyper extract
 * via the Python sidecar, then publishes the resulting .tdsx to Tableau Cloud.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerCreateDatasourceFromFile(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "create_datasource_from_file",
    {
      title: "Create a datasource from a local file",
      description:
        "Read a local file (CSV, JSON, JSONL, Excel .xlsx/.xls, or Parquet) into a " +
        "Hyper extract and publish it to a Tableau Cloud project as a .tdsx datasource. " +
        "The file must be accessible on the machine running the MCP server. " +
        "Returns { datasourceLuid, contentUrl, url }.",
      inputSchema: {
        name: z
          .string()
          .min(1)
          .describe("Logical datasource name shown in Tableau."),
        filePath: z
          .string()
          .min(1)
          .describe("Absolute path to the source file on disk."),
        fileType: z
          .enum(["csv", "json", "jsonl", "xlsx", "xls", "parquet"])
          .optional()
          .describe(
            "Explicit file type. When omitted, inferred from the file extension.",
          ),
        excelSheet: z
          .union([z.string(), z.number().int().nonnegative()])
          .optional()
          .describe("Excel only: sheet name or 0-based sheet index (default: first sheet)."),
        jsonPath: z
          .string()
          .optional()
          .describe(
            "JSON/JSONL only: simple JSONPath selector to extract an array from the document " +
              "(e.g. '$.data'). Only a single-level key is supported.",
          ),
        projectName: z
          .string()
          .min(1)
          .describe("Target project name (must already exist)."),
        overwrite: z
          .boolean()
          .default(false)
          .describe("Overwrite an existing datasource with the same name."),
      },
      outputSchema: {
        datasourceLuid: z.string(),
        contentUrl: z.string(),
        url: z.string(),
      },
    },
    async ({ name, filePath, fileType, excelSheet, jsonPath, projectName, overwrite }) => {
      // PA-2: infer file type from extension when not supplied; reject unsupported extensions
      // before any sidecar call so zero sidecar calls happen on a bad extension.
      const SUPPORTED_TYPES = ["csv", "json", "jsonl", "xlsx", "xls", "parquet"] as const;
      type SupportedType = (typeof SUPPORTED_TYPES)[number];
      const resolvedType: SupportedType | undefined = fileType as SupportedType | undefined ?? (() => {
        const ext = filePath.includes(".")
          ? filePath.split(".").pop()?.toLowerCase()
          : undefined;
        if (!ext || !(SUPPORTED_TYPES as readonly string[]).includes(ext)) {
          throw new Error(
            `Unsupported file extension "${ext ?? "none"}". ` +
              `Supported formats: ${SUPPORTED_TYPES.join(", ")}.`,
          );
        }
        return ext as SupportedType;
      })();

      // Build the Hyper extract via sidecar
      const { tdsxPath } = await ctx.sidecar.buildDatasourceFromFile({
        name,
        filePath,
        fileType: resolvedType,
        excelSheet,
        jsonPath,
      });

      // Publish to Tableau Cloud
      const projectId = await ctx.rest.resolveProjectId(projectName);
      const { id, contentUrl = "", url } = await ctx.rest.publishDatasource(
        tdsxPath,
        name,
        projectId,
        overwrite,
      );

      return toolResult(`Published datasource "${name}" → ${url}`, {
        datasourceLuid: id,
        contentUrl,
        url,
      });
    },
  );
}
