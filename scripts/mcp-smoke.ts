/**
 * Smoke-test both Tableau MCP servers over stdio: handshake, list tools, call one
 * safe read-only tool per server.
 *
 * Requires SERVER, SITE_NAME, PAT_NAME, PAT_VALUE in the environment (or a local
 * .env file). Never logs PAT_VALUE.
 *
 * Usage:
 *   npm run test:mcp-smoke
 *   npm run test:mcp-smoke -- --publish-only
 *   npm run test:mcp-smoke -- --official-only
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

type Target = "official" | "publish";

interface ServerSpec {
  id: Target;
  label: string;
  command: string;
  args: string[];
  probeTool: string;
  probeArgs: Record<string, unknown>;
}

const SERVERS: ServerSpec[] = [
  {
    id: "official",
    label: "@tableau/mcp-server",
    command: "npx",
    args: ["-y", "@tableau/mcp-server@latest"],
    probeTool: "list-datasources",
    probeArgs: {},
  },
  {
    id: "publish",
    label: "tableau-mcp-publish",
    command: "node",
    args: ["dist/index.js"],
    probeTool: "list_projects",
    probeArgs: {},
  },
];

function hasAuthEnv(): boolean {
  return Boolean(
    process.env.SERVER &&
      process.env.SITE_NAME !== undefined &&
      process.env.PAT_NAME &&
      process.env.PAT_VALUE &&
      process.env.PAT_VALUE !== "…" &&
      process.env.PAT_VALUE !== "replace-me-never-commit-the-real-value",
  );
}

function envForServer(): Record<string, string> {
  const base = { ...process.env } as Record<string, string>;
  for (const key of ["SERVER", "SITE_NAME", "PAT_NAME", "PAT_VALUE"] as const) {
    const value = process.env[key];
    if (value !== undefined) base[key] = value;
  }
  return base;
}

async function smokeOne(spec: ServerSpec): Promise<{ ok: boolean; detail: string }> {
  const transport = new StdioClientTransport({
    command: spec.command,
    args: spec.args,
    env: envForServer(),
    cwd: process.cwd(),
    stderr: "pipe",
  });

  const client = new Client({ name: "tableau-mcp-smoke", version: "0.1.0" });
  const stderrChunks: string[] = [];
  transport.stderr?.on("data", (chunk: Buffer) => {
    const line = chunk.toString("utf8");
    if (!line.includes(process.env.PAT_VALUE ?? "\0")) {
      stderrChunks.push(line);
    }
  });

  try {
    await client.connect(transport, { timeout: 60_000 });
    const tools = await client.listTools();
    const names = tools.tools.map((t) => t.name);
    if (!names.includes(spec.probeTool)) {
      return {
        ok: false,
        detail: `connected but probe tool "${spec.probeTool}" missing (${names.length} tools listed)`,
      };
    }

    const result = await client.callTool({
      name: spec.probeTool,
      arguments: spec.probeArgs,
    });
    if (result.isError) {
      return { ok: false, detail: `tool ${spec.probeTool} returned isError=true` };
    }

    const preview = JSON.stringify(result.structuredContent ?? result.content).slice(0, 240);
    return {
      ok: true,
      detail: `${names.length} tools; ${spec.probeTool} ok → ${preview}${preview.length >= 240 ? "…" : ""}`,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const stderr = stderrChunks.join("").trim();
    return {
      ok: false,
      detail: stderr ? `${msg}\nstderr: ${stderr.slice(0, 400)}` : msg,
    };
  } finally {
    await client.close().catch(() => undefined);
    await transport.close().catch(() => undefined);
  }
}

async function main(): Promise<void> {
  try {
    process.loadEnvFile(".env");
  } catch {
    // optional .env
  }

  const args = new Set(process.argv.slice(2));
  let targets = SERVERS;
  if (args.has("--official-only")) targets = targets.filter((s) => s.id === "official");
  if (args.has("--publish-only")) targets = targets.filter((s) => s.id === "publish");

  if (!hasAuthEnv()) {
    console.log("MCP smoke test skipped — set SERVER, SITE_NAME, PAT_NAME, PAT_VALUE (real PAT, not placeholder).");
    process.exit(0);
  }

  console.log(`Tableau MCP smoke test → ${process.env.SERVER} (site "${process.env.SITE_NAME}")`);
  let failed = 0;

  for (const spec of targets) {
    process.stdout.write(`  ${spec.label} … `);
    const res = await smokeOne(spec);
    console.log(res.ok ? "PASS" : "FAIL");
    console.log(`    ${res.detail}`);
    if (!res.ok) failed += 1;
  }

  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
