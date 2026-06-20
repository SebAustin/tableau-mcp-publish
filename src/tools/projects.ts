import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";

export function registerProjectTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "list_projects",
    {
      title: "List projects",
      description:
        "List every project on the Tableau site with its name and LUID. Use this to discover the project to publish into. Returns { projects: [{ id, name }] }.",
      inputSchema: {},
      outputSchema: {
        projects: z.array(z.object({ id: z.string(), name: z.string() })),
      },
    },
    async () => {
      const projects = await ctx.rest.listProjects();
      return toolResult(`Found ${projects.length} project(s).`, { projects });
    },
  );

  server.registerTool(
    "create_project",
    {
      title: "Create project",
      description:
        "Create a new project on the Tableau site, optionally with a description. Returns { id, name }.",
      inputSchema: {
        name: z.string().min(1).describe("Project name."),
        description: z.string().optional().describe("Optional project description."),
      },
      outputSchema: { id: z.string(), name: z.string() },
    },
    async ({ name, description }) => {
      const project = await ctx.rest.createProject(name, description);
      return toolResult(`Created project "${project.name}" (${project.id}).`, { ...project });
    },
  );
}
