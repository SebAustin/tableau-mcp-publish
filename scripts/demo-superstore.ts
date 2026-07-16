/**
 * Executive-grade dashboard demo ("Super Sample Superstore"): ingest the
 * Superstore file, design an exec dashboard (KPI strip with period deltas, a
 * color-encoded bar, a filled US-state map) and publish it to Tableau Cloud.
 *
 * Exercises the full Phase-1 rich path: UTF-16/TSV ingest + numeric coercion →
 * deterministic exec plan (kpi_tile / color / geo / layoutGrammar) → embedded
 * extract workbook → publish. Prints both Cloud URLs.
 *
 * Phase E1 (Slice B): optionally resolves a named persona from brand.yaml and
 * applies its brand (palette, typography, number formats) to the generated
 * workbook — the same propose→build path `design_dashboard` +
 * `build_from_plan` uses, in one script. Pass `--persona <name>` (or set
 * `PERSONA` in the environment) to brand the demo:
 *
 *   npm run demo:superstore -- --persona ceo
 *   npm run demo:superstore -- "/path/to/file.csv" --persona ceo
 *
 * Guarded: runs only when SERVER/SITE_NAME/PAT_NAME/PAT_VALUE and DEMO_PROJECT
 * are set. The PAT is never logged. Usage:
 *
 *   npm run demo:superstore -- "/path/to/Sample - Superstore_Migrated Data.csv"
 */
import { resolve } from "node:path";
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import {
  AuthoringSidecar,
  type SheetSpec as SidecarSheet,
  type WorkbookBrand,
} from "../src/sidecar.js";
import { generatePlan } from "../src/planner/plan.js";
import { buildProposal } from "../src/planner/proposal.js";
import { AUDIENCE_CONSTRAINTS } from "../src/planner/audience.js";
import type { AudienceConstraintOverrides } from "../src/planner/audience.js";
import type { Audience } from "../src/planner/schema.js";
import { loadBrand, resolvePersona } from "../src/branding/load.js";
import { toBuilderBrand } from "../src/branding/builderBrand.js";

const DEFAULT_FILE = `${process.env.HOME}/Downloads/Sample - Superstore_Migrated Data.csv`;
const DEFAULT_AUDIENCE: Audience = "exec";

interface DemoArgs {
  filePath?: string;
  persona?: string;
}

/** Parses `--persona <name>` / `--persona=<name>` and one positional filePath arg. */
function parseArgs(argv: string[]): DemoArgs {
  const rest = argv.slice(2);
  let persona = process.env.PERSONA;
  let filePath: string | undefined;
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i] ?? "";
    if (arg === "--persona") {
      persona = rest[i + 1];
      i += 1;
    } else if (arg.startsWith("--persona=")) {
      persona = arg.slice("--persona=".length);
    } else if (arg.length > 0 && filePath === undefined) {
      filePath = arg;
    }
  }
  return { filePath, persona };
}

function ready(): boolean {
  return Boolean(
    process.env.SERVER &&
      process.env.SITE_NAME !== undefined &&
      process.env.PAT_NAME &&
      process.env.PAT_VALUE &&
      process.env.DEMO_PROJECT,
  );
}

async function main(): Promise<void> {
  try {
    process.loadEnvFile(".env");
  } catch {
    // No .env file — fall back to the ambient environment.
  }

  if (!ready()) {
    console.log(
      "Demo skipped. Set SERVER, SITE_NAME, PAT_NAME, PAT_VALUE and DEMO_PROJECT to run it.",
    );
    return;
  }

  const { filePath: filePathArg, persona } = parseArgs(process.argv);
  const filePath = resolve(filePathArg ?? DEFAULT_FILE);
  const projectName = process.env.DEMO_PROJECT as string;
  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);

  const datasourceName = "Superstore (exec demo)";

  // Phase E1 (Slice B): resolve the persona (if any) against brand.yaml. This
  // is the only I/O in the demo — mirrors design_dashboard's own persona
  // resolution (loadBrand/resolvePersona), reused here so `npm run
  // demo:superstore -- --persona ceo` is the one-command E1 live proof.
  let audience: Audience = DEFAULT_AUDIENCE;
  let personaName: string | undefined;
  let brandName: string | undefined;
  let constraintOverrides: AudienceConstraintOverrides | undefined;
  let brand: WorkbookBrand | undefined;

  if (persona) {
    console.log(`Resolving persona "${persona}" from brand.yaml…`);
    const { brand: brandFile } = loadBrand();
    const resolved = resolvePersona(brandFile, persona);
    audience = resolved.audience;
    personaName = persona;
    brandName = brandFile.brand.name;
    brand = toBuilderBrand(brandFile);
    if (resolved.overrides.maxSheets !== undefined) {
      constraintOverrides = { maxSheets: resolved.overrides.maxSheets };
    }
    console.log(`  → audience "${audience}", brand "${brandName}".`);
  }

  try {
    console.log(`Signing in to ${config.server} (site "${config.siteName}")…`);
    await rest.signIn();
    console.log("Starting the Python authoring sidecar…");
    await sidecar.start();

    // 1. Ingest the (UTF-16/TSV) Superstore file → governed published datasource.
    console.log(`Ingesting ${filePath}…`);
    const { tdsxPath, hyperPath, columns } = await sidecar.buildDatasourceFromFile({
      name: datasourceName,
      filePath,
      fileType: "csv", // encoding/delimiter auto-sniffed (UTF-16 / tab)
    });
    console.log(`Ingested ${columns.length} columns.`);
    const projectId = await rest.resolveProjectId(projectName);
    const ds = await rest.publishDatasource(tdsxPath, datasourceName, projectId, true);
    console.log(`Datasource published → ${ds.url}`);

    // 2. Design the exec dashboard deterministically from the real columns.
    const plan = generatePlan({
      mode: "autonomous",
      audience,
      businessQuestion:
        "How is Superstore performing on sales and profit by region, category, and state?",
      fieldHints: columns,
      datasourceLuid: ds.id,
      datasourceName,
      projectName,
      workbookName: "Superstore Executive Dashboard (demo)",
      constraintOverrides,
      personaName,
      brandName,
    });

    // The proposal is what an agent would present to the user for confirmation.
    const proposal = buildProposal(plan);
    console.log(`\nProposed: ${proposal.summary}`);
    console.log("KPI strip:");
    for (const k of proposal.kpiStrip) {
      console.log(
        `  • ${k.label}${k.deltaMeasure ? ` (Δ ${k.deltaMeasure}, ${k.direction})` : ""}`,
      );
    }
    for (const v of proposal.views) console.log(`  view: ${v.encodingSummary}`);
    console.log(`Layout: ${proposal.layoutSummary}\n`);

    // 3. Build the self-contained (embedded-extract) workbook from the plan.
    const c = AUDIENCE_CONSTRAINTS[audience];
    const sheets: SidecarSheet[] = plan.sheets.map((s) => ({
      title: s.title,
      markType: s.markType,
      rows: s.rows,
      cols: s.cols,
      measures: s.measures,
      ...(s.kind ? { kind: s.kind } : {}),
      ...(s.color ? { color: s.color } : {}),
      ...(s.kpi ? { kpi: s.kpi } : {}),
      ...(s.scatter ? { scatter: s.scatter } : {}),
      ...(s.geo ? { geo: s.geo } : {}),
    }));

    console.log("Building the dashboard workbook (embedded extract)…");
    const { twbxPath } = await sidecar.buildDashboardWorkbook({
      datasourceName,
      datasourceContentUrl: "superstore",
      site: config.siteName,
      serverUrl: config.server,
      hyperPath,
      sheets,
      dashboardSheetTitles: plan.sheets.map((s) => s.title),
      dashboardLayout: "tiled_horizontal",
      canvasWidth: c.canvasWidth,
      canvasHeight: c.canvasHeight,
      ...(plan.dashboardTitle ? { dashboardTitle: plan.dashboardTitle } : {}),
      ...(plan.dashboardSubtitle ? { dashboardSubtitle: plan.dashboardSubtitle } : {}),
      ...(plan.layoutGrammar ? { layoutGrammar: plan.layoutGrammar } : {}),
      ...(brand ? { brand } : {}),
    });

    // 4. Publish the workbook.
    const wb = await rest.publishWorkbook(
      twbxPath,
      "Superstore Executive Dashboard (demo)",
      projectId,
      true,
    );

    console.log("\nDemo complete:");
    console.log(`  Datasource: ${ds.url}`);
    console.log(`  Workbook:   ${wb.url}`);
  } finally {
    await sidecar.stop();
    await rest.signOut().catch(() => undefined);
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
