/**
 * End-to-end demo: publish a datasource from a CSV, then a starter workbook bound
 * to it, and print both Cloud URLs.
 *
 * Guarded: runs only when SERVER/SITE_NAME/PAT_NAME/PAT_VALUE and DEMO_PROJECT are
 * set. The PAT is never logged. Usage:
 *
 *   npm run demo -- examples/top_customers.csv
 */
import { loadConfig } from "../src/config.js";
import { TableauRestClient } from "../src/restClient.js";
import { AuthoringSidecar } from "../src/sidecar.js";

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

  const csvPath = process.argv[2] ?? "examples/top_customers.csv";
  const projectName = process.env.DEMO_PROJECT as string;
  const config = loadConfig();
  const rest = new TableauRestClient(config);
  const sidecar = new AuthoringSidecar(config.sidecarHost, config.sidecarPort);

  const datasourceName = "Top Customers (demo)";
  const workbookName = "Top Customers Starter (demo)";

  try {
    console.log(`Signing in to ${config.server} (site "${config.siteName}")…`);
    await rest.signIn();
    console.log("Starting the Python authoring sidecar…");
    await sidecar.start();

    console.log(`Building a datasource from ${csvPath}…`);
    const { tdsxPath } = await sidecar.buildDatasourceFromTable({
      name: datasourceName,
      csvPath,
    });
    const projectId = await rest.resolveProjectId(projectName);
    const ds = await rest.publishDatasource(tdsxPath, datasourceName, projectId, true);
    console.log(`✅ Datasource published → ${ds.url}`);

    const { contentUrl } = await rest.getDatasource(ds.id);
    const { twbxPath } = await sidecar.buildStarterWorkbook({
      datasourceName,
      datasourceContentUrl: contentUrl,
      site: config.siteName,
      sheets: [
        { title: "Revenue by Region", markType: "bar", cols: ["region"], rows: [], measures: ["revenue"] },
      ],
    });
    const wb = await rest.publishWorkbook(twbxPath, workbookName, projectId, true);
    console.log(`✅ Workbook published → ${wb.url}`);

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
