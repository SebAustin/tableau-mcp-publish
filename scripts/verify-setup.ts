/**
 * Pre-flight checks before starting Tableau MCP servers in Cursor.
 * Never prints PAT_VALUE.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import { AuthoringSidecar } from "../src/sidecar.js";

const PLACEHOLDER_PATS = new Set([
  "…",
  "...",
  "replace-me-never-commit-the-real-value",
  "replace-me",
  "your_pat_secret_here",
]);

function fail(msg: string): never {
  console.error(`❌ ${msg}`);
  process.exit(1);
}

function ok(msg: string): void {
  console.log(`✅ ${msg}`);
}

function warn(msg: string): void {
  console.log(`⚠️  ${msg}`);
}

async function main(): Promise<void> {
  const root = process.cwd();
  console.log("Tableau MCP setup verification\n");

  const dist = resolve(root, "dist/index.js");
  if (!existsSync(dist)) {
    fail("dist/index.js missing — run: npm install && npm run build");
  }
  ok("dist/index.js exists");

  const envPath = resolve(root, ".env");
  if (!existsSync(envPath)) {
    fail(
      ".env missing — run: cp .env.example .env\n" +
        "Then edit .env and set PAT_VALUE to your real Personal Access Token secret.",
    );
  }
  ok(".env exists");

  try {
    process.loadEnvFile(".env");
  } catch {
    fail(".env could not be loaded");
  }

  const pat = process.env.PAT_VALUE ?? "";
  if (!pat || PLACEHOLDER_PATS.has(pat) || pat.length < 8) {
    fail(
      "PAT_VALUE in .env is missing or still a placeholder.\n" +
        "  1. Open Tableau Cloud → Account Settings → Personal Access Tokens\n" +
        "  2. Create a token (copy the secret immediately — shown once)\n" +
        "  3. Set PAT_NAME and PAT_VALUE in .env\n" +
        "  4. Restart both MCP servers in Cursor Settings → MCP",
    );
  }
  ok("PAT_VALUE is set (not a placeholder)");

  let config;
  try {
    config = loadConfig();
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  }
  ok(`Config valid — ${config.server} site "${config.siteName}"`);

  console.log("\nTesting Tableau sign-in…");
  const rest = new TableauRestClient(config);
  try {
    await rest.signIn();
    ok("Tableau REST sign-in succeeded");
    await rest.signOut();
  } catch (err) {
    fail(
      `Tableau sign-in failed: ${err instanceof Error ? err.message : String(err)}\n` +
        "  Check SERVER, SITE_NAME, PAT_NAME, PAT_VALUE in .env match your Cloud site.",
    );
  }

  console.log("\nTesting Python sidecar…");
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);
  try {
    await sidecar.start();
    ok("Python sidecar started and healthy");
  } catch (err) {
    fail(
      `Sidecar failed: ${err instanceof Error ? err.message : String(err)}\n` +
        "  Run: cd sidecar && uv sync",
    );
  } finally {
    await sidecar.stop();
  }

  const globalMcp = resolve(process.env.HOME ?? "", ".cursor/mcp.json");
  if (existsSync(globalMcp)) {
    const raw = readFileSync(globalMcp, "utf8");
    if (raw.includes('"PAT_VALUE": "…"') || raw.includes('"PAT_VALUE":"…"')) {
      warn(
        "~/.cursor/mcp.json still has PAT_VALUE: \"…\" — remove the tableau entries there\n" +
          "  (this project's .cursor/mcp.json + .env is the source of truth when this folder is open).",
      );
    }
  }

  console.log("\nAll checks passed. Next:");
  console.log("  1. Cursor Settings → MCP → restart tableau + tableau-publish");
  console.log("  2. npm run test:mcp-smoke");
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
