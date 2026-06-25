/**
 * End-to-end demo: publish a datasource from a CSV, call design_dashboard
 * (autonomous mode) to get a DashboardPlan, then build_from_plan to publish
 * a dashboard workbook on Tableau Cloud and print both Cloud URLs.
 *
 * Guarded: runs only when SERVER/SITE_NAME/PAT_NAME/PAT_VALUE and DEMO_PROJECT are
 * set. The PAT is never logged. Usage:
 *
 *   npm run demo:dashboard -- examples/top_customers.csv
 */
import { resolve } from "node:path";
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import { AuthoringSidecar } from "../src/sidecar.js";
import { generatePlan } from "../src/planner/plan.js";
import { AUDIENCE_CONSTRAINTS } from "../src/planner/audience.js";

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
  // Load a local, gitignored .env if present so credentials never need to be
  // exported on the command line (Node 20.12+/22+ built-in; no dependency).
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

  const csvPath = resolve(process.argv[2] ?? "examples/top_customers.csv");
  const projectName = process.env.DEMO_PROJECT as string;
  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);

  const datasourceName = "Top Customers Dashboard (demo)";
  const audience = "analyst" as const;

  try {
    console.log(`Signing in to ${config.server} (site "${config.siteName}")…`);
    await rest.signIn();
    console.log("Starting the Python authoring sidecar…");
    await sidecar.start();

    // Step 1: Build and publish the datasource from the CSV file.
    console.log(`Building a datasource from ${csvPath}…`);
    const { tdsxPath, columns } = await sidecar.buildDatasourceFromFile({
      name: datasourceName,
      filePath: csvPath,
      fileType: "csv",
    });
    console.log(
      `Datasource built — real columns: ${columns.map((c) => `${c.name}(${c.dataType})`).join(", ")}`,
    );
    const projectId = await rest.resolveProjectId(projectName);
    const ds = await rest.publishDatasource(tdsxPath, datasourceName, projectId, true);
    console.log(`Datasource published → ${ds.url}`);

    const { contentUrl } = await rest.getDatasource(ds.id);
    if (!contentUrl) {
      throw new Error("Published datasource is missing contentUrl; cannot bind the workbook.");
    }

    // Step 2: Design the dashboard plan (autonomous mode, analyst audience).
    // Use the REAL columns returned by the sidecar so the planner only binds
    // fields that actually exist in the datasource.
    console.log("Designing dashboard plan (autonomous, analyst audience)…");
    const plan = generatePlan({
      mode: "autonomous",
      audience,
      businessQuestion: "Show revenue by customer and revenue by region",
      fieldHints: columns,
      datasourceLuid: ds.id,
      datasourceName,
      projectName,
      workbookName: "Top Customers Dashboard (demo)",
    });
    console.log(
      `Plan generated: ${plan.sheets.length} sheet(s), layout=${plan.dashboardLayout}`,
    );
    for (const sheet of plan.sheets) {
      console.log(`  - [${sheet.markType}] ${sheet.title}`);
    }

    // Step 3: Build the .twbx with dashboard via the sidecar.
    const constraints = AUDIENCE_CONSTRAINTS[audience];
    console.log("Building dashboard workbook…");
    const { twbxPath } = await sidecar.buildDashboardWorkbook({
      datasourceName,
      datasourceContentUrl: contentUrl,
      site: config.siteName,
      serverUrl: config.server,
      sheets: plan.sheets.map((s) => ({
        title: s.title,
        markType: s.markType,
        rows: s.rows,
        cols: s.cols,
        measures: s.measures,
      })),
      dashboardSheetTitles: plan.sheets.map((s) => s.title),
      dashboardLayout: plan.dashboardLayout as "tiled_vertical" | "tiled_horizontal",
      canvasWidth: constraints.canvasWidth,
      canvasHeight: constraints.canvasHeight,
    });

    // Step 4: Publish the workbook.
    const wb = await rest.publishWorkbook(twbxPath, plan.workbookName, projectId, true);
    console.log(`Workbook published → ${wb.url}`);

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
