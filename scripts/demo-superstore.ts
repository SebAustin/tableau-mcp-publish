/**
 * Executive-grade dashboard demo ("Super Sample Superstore"): ingest the
 * Superstore file, design an exec dashboard (KPI strip with period deltas, a
 * color-encoded bar, a filled US-state map) and publish it to Tableau Cloud.
 *
 * Exercises the full Phase-1 rich path: UTF-16/TSV ingest + numeric coercion →
 * deterministic exec plan (kpi_tile / color / geo / layoutGrammar) → embedded
 * extract workbook → publish. Prints both Cloud URLs.
 *
 * Guarded: runs only when SERVER/SITE_NAME/PAT_NAME/PAT_VALUE and DEMO_PROJECT
 * are set. The PAT is never logged. Usage:
 *
 *   npm run demo:superstore -- "/path/to/Sample - Superstore_Migrated Data.csv"
 */
import { resolve } from "node:path";
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import { AuthoringSidecar, type SheetSpec as SidecarSheet } from "../src/sidecar.js";
import { generatePlan } from "../src/planner/plan.js";
import { buildProposal } from "../src/planner/proposal.js";
import { AUDIENCE_CONSTRAINTS } from "../src/planner/audience.js";

const DEFAULT_FILE = `${process.env.HOME}/Downloads/Sample - Superstore_Migrated Data.csv`;

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

  const filePath = resolve(process.argv[2] ?? DEFAULT_FILE);
  const projectName = process.env.DEMO_PROJECT as string;
  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);

  const datasourceName = "Superstore (exec demo)";
  const audience = "exec" as const;

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
