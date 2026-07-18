import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";
import { request } from "undici";

export interface BuildResult {
  path: string;
}

/** A single column descriptor returned by the file-ingest sidecar route. */
export interface ColumnInfo {
  /** Column name as it appears in the source file. */
  name: string;
  /**
   * Coarse planner-friendly data type category.
   * One of: "number" | "date" | "boolean" | "string".
   */
  dataType: string;
}

/** Response shape from /datasource/from-file (superset of BuildResult). */
export interface FileResult extends BuildResult {
  /** Real columns from the file schema — use these as fieldHints for the planner. */
  columns: ColumnInfo[];
  /**
   * Path to the raw .hyper extract on disk.
   * Pass this as `hyperPath` to `buildDashboardWorkbook` so the published
   * workbook embeds the extract directly (federated connection) instead of
   * referencing a separately published datasource via sqlproxy.
   */
  hyperPath: string;
}

export interface QueryArgs {
  connection: Record<string, unknown>;
  sql: string;
  name: string;
  maxRows?: number;
}

export interface TableArgs {
  name: string;
  csvPath?: string;
  records?: Array<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// SheetSpec sub-types (mirror planner/schema.ts — kept in sync manually)
// ---------------------------------------------------------------------------

/** Color encoding for a worksheet (dimension / measure-names / quantitative). */
export interface SheetColor {
  field: string;
  kind: "dimension" | "measure_names" | "measure";
}

/** KPI tile encoding: primary + optional comparison/delta/sparkline. */
export interface SheetKpi {
  primaryMeasure: string;
  comparisonMeasure?: string;
  deltaMeasure?: string;
  deltaIsPositiveGood?: boolean;
  sparklineField?: string;
  valuePrefix?: string;
  valueSuffix?: string;
}

/** Scatter-plot axis binding (x / y measures, optional breakdown dimension). */
export interface SheetScatter {
  x: string;
  y: string;
  breakdown?: string;
}

/** Geographic / filled-map encoding. */
export interface SheetGeo {
  geoField: string;
  geoRole: "state" | "country" | "city" | "zipcode";
  colorMeasure?: string;
}

/**
 * A single worksheet style-rule override (mirrors schema.ts SheetStyleRule /
 * the sidecar's SheetStyleRuleModel). Design Excellence, Slice D1 —
 * carry-only: not consumed by the builder yet.
 */
export interface SheetStyleRule {
  element: string;
  formats: Record<string, string>;
}

export interface SheetSpec {
  title: string;
  markType: string;
  rows: string[];
  cols: string[];
  measures: string[];
  // --- Phase-1 optional encoding fields (Slice 2: carry end-to-end) ---
  /** Sheet kind: "chart" (default) or "kpi_tile". */
  kind?: "chart" | "kpi_tile";
  /** Color encoding block. */
  color?: SheetColor;
  /** KPI tile configuration. */
  kpi?: SheetKpi;
  /** Scatter-plot axis binding. */
  scatter?: SheetScatter;
  /** Geographic encoding. */
  geo?: SheetGeo;
  // --- Design Excellence, Slice D1: optional per-sheet style-rule overrides (carry-only) ---
  styleRules?: SheetStyleRule[];
}

export interface FileArgs {
  /** Logical datasource name (shown in Tableau). */
  name: string;
  /** Absolute path to the source file on disk. */
  filePath: string;
  /**
   * Explicit file type.  When omitted the sidecar infers from the extension.
   * Accepted: csv | json | jsonl | xlsx | xls | parquet
   */
  fileType?: string;
  /**
   * Excel only: sheet name or 0-based index.  Defaults to the first sheet.
   */
  excelSheet?: string | number;
  /**
   * JSON/JSONL only: simple JSONPath selector to extract an array from the
   * document (e.g. `$.data`).  Only a single-level key is supported.
   */
  jsonPath?: string;
  /**
   * CSV only: explicit text encoding (e.g. `utf-16`) and column delimiter
   * (e.g. `"\t"`).  When omitted, the sidecar auto-sniffs both from the file's
   * BOM and header row, so UTF-16/TSV exports load without specifying them.
   */
  encoding?: string;
  delimiter?: string;
}

// ---------------------------------------------------------------------------
// Live-connection datasource spec (Phase E2 slice C — mirrors the sidecar's
// LiveConnectionSpec / LiveDatasourceRequest in server.py, kept in sync
// manually per this file's existing convention). NEVER carries credentials —
// those travel separately via `TableauRestClient.publishDatasource`'s
// `credentials` option (see `rest/credentials.ts`).
// ---------------------------------------------------------------------------

/** Snowflake live-connection topology. VERIFY-LIVE attribute mapping — see `sidecar/tds_builder.py`. */
export interface LiveSnowflakeConnection {
  type: "snowflake";
  /** `<account>.snowflakecomputing.com` */
  server: string;
  schema: string;
  table: string;
  warehouse: string;
  dbname: string;
  /** `"username-password"` (default) | `"oauth"`. Key-pair auth is rejected server-side (Desktop-only). */
  authentication?: string;
  role?: string;
}

/** Presto/Trino live-connection topology. VERIFY-LIVE attribute mapping — see `sidecar/tds_builder.py`. */
export interface LivePrestoConnection {
  type: "presto";
  server: string;
  schema: string;
  table: string;
  port?: number;
  catalog: string;
  ssl?: boolean;
}

export type LiveConnectionSpec = LiveSnowflakeConnection | LivePrestoConnection;

export interface LiveDatasourceArgs {
  /** Display name of the published datasource. */
  name: string;
  connection: LiveConnectionSpec;
}

export interface WorkbookArgs {
  /** Display name / caption of the published datasource. */
  datasourceName: string;
  /** Server-assigned contentUrl, used for the workbook's repository-location binding. */
  datasourceContentUrl: string;
  /** Site contentUrl (may be empty for the Default site). */
  site: string;
  /** Tableau Cloud/Server host URL (e.g. https://10ax.online.tableau.com). */
  serverUrl?: string;
  sheets: SheetSpec[];
}

/** Text zone for dashboard header / footer (mirrors schema.ts TextZone). */
export interface TextZone {
  text: string;
  position: "header" | "footer";
}

/** Layout grammar for the dashboard canvas (mirrors schema.ts LayoutGrammar). */
export interface LayoutGrammar {
  kind: "kpi_band_over_charts" | "tiled_vertical" | "tiled_horizontal";
  kpiTileTitles?: string[];
  chartTitles?: string[];
}

// ---------------------------------------------------------------------------
// Story block (Phase E4 — Stories; mirrors schema.ts's StoryArcPoint and the
// sidecar's StoryPointModel/StoryModel, kept in sync manually per this
// file's existing convention).
// ---------------------------------------------------------------------------

/** One story point: a caption over an existing worksheet or dashboard. */
export interface StoryPoint {
  caption: string;
  capturedSheet: string;
}

/** A Tableau story (storyboard dashboard) built alongside the regular dashboard. */
export interface Story {
  name: string;
  navType?: "caption" | "number" | "dot" | "arrowonly";
  points: StoryPoint[];
}

// ---------------------------------------------------------------------------
// Brand block (Phase E1, Slice B — mirrors branding/builderBrand.ts's
// BuilderBrand, kept in sync manually per this file's existing convention).
// ---------------------------------------------------------------------------

export interface WorkbookBrandPalette {
  categorical: string[];
  sequential: string[];
  diverging: string[];
  good: string;
  bad: string;
  neutral: string;
}

export interface WorkbookBrandFontSpec {
  font: string;
  size: number;
  color: string;
}

/** BAN (Big Number / KPI hero figure) font spec — no color field. */
export interface WorkbookBrandBanFontSpec {
  font: string;
  size: number;
}

export interface WorkbookBrandTypography {
  title: WorkbookBrandFontSpec;
  body: WorkbookBrandFontSpec;
  ban: WorkbookBrandBanFontSpec;
}

export interface WorkbookBrandFormats {
  currency: string;
  percent: string;
  number: string;
}

/** Resolved brand block applied to the generated workbook (matches sidecar's BrandModel). */
export interface WorkbookBrand {
  palette: WorkbookBrandPalette;
  typography: WorkbookBrandTypography;
  formats: WorkbookBrandFormats;
  brandName: string;
}

// ---------------------------------------------------------------------------
// Design-theme block (Design Excellence, Slice D1 — wire plumbing,
// carry-only; mirrors sidecar's DesignThemeModel family and
// planner/schema.ts's DesignThemeSchema, kept in sync manually per this
// file's existing convention). Not consumed by the builder yet.
// ---------------------------------------------------------------------------

/** Hairline border spec for a themed zone. */
export interface ThemeBorder {
  color?: string;
  style?: string;
  width?: number;
}

/** Datalabel styling (size / weight / color mode). */
export interface ThemeDatalabel {
  fontSize?: number;
  fontWeight?: string;
  colorMode?: string;
}

/** Chrome-removal rules: gridlines/zeroline/ticks/mark-labels. */
export interface ThemeChrome {
  hideGridlines?: boolean;
  hideZeroline?: boolean;
  hideAxisTicks?: boolean;
  showMarkLabels?: boolean;
  datalabel?: ThemeDatalabel;
}

/** Canvas spacing (outer margin + gutter between zones). */
export interface ThemeSpacing {
  outerMargin?: number;
  gutter?: number;
}

/** Chart-card zone-style box model (background/border/padding/margin/corner-radius). */
export interface ThemeChartCard {
  background?: string;
  border?: ThemeBorder;
  padding?: number;
  margin?: number;
  cornerRadius?: number;
}

/** KPI-tile zone-style box model. */
export interface ThemeKpiTile {
  background?: string;
  border?: ThemeBorder;
  padding?: number;
  banColor?: string;
  useSemanticDeltaColors?: boolean;
}

/** Header-band styling. */
export interface ThemeHeader {
  background?: string;
  titleColor?: string;
  subtitleColor?: string;
}

/**
 * Resolved design theme block (matches sidecar's DesignThemeModel). Design
 * Excellence, Slice D1 — carry-only: forwarded to the sidecar but not read
 * by the builder yet (lands in Slice D2 onward). Omit to keep output
 * unchanged.
 */
export interface DesignTheme {
  name: string;
  dashboardBackground?: string;
  spacing?: ThemeSpacing;
  chartCard?: ThemeChartCard;
  kpiTile?: ThemeKpiTile;
  header?: ThemeHeader;
  chrome?: ThemeChrome;
}

export interface DashboardWorkbookArgs extends WorkbookArgs {
  /** Sheet titles to include in the dashboard (subset or all of sheets[].title). */
  dashboardSheetTitles: string[];
  /** Zone tiling direction. */
  dashboardLayout?: "tiled_vertical" | "tiled_horizontal";
  /** Canvas width in pixels. */
  canvasWidth?: number;
  /** Canvas height in pixels. */
  canvasHeight?: number;
  /**
   * Path to the .hyper extract returned by `buildDatasourceFromFile`.
   *
   * When provided, the workbook embeds the extract directly (federated
   * connection, self-contained .twbx) so Tableau Cloud can render it
   * without needing a separately published datasource binding.
   *
   * Omit only when falling back to the legacy sqlproxy reference path.
   */
  hyperPath?: string;
  // --- Phase-1 optional dashboard-level fields (Slice 2: carry end-to-end) ---
  /** Human-readable dashboard title rendered in the title text zone. */
  dashboardTitle?: string;
  /** Human-readable dashboard subtitle rendered below the title. */
  dashboardSubtitle?: string;
  /** Explicit text zones (header / footer). */
  textZones?: TextZone[];
  /** Structured layout grammar used by the builder to emit multi-zone XML. */
  layoutGrammar?: LayoutGrammar;
  // --- Phase E1 (Slice B): optional resolved brand block ---
  /**
   * Resolved brand block (from `loadBrand()` + `toBuilderBrand()`) applied to
   * the generated workbook: a workbook-level color palette, brand-driven
   * title/subtitle typography, and `default-format` on measure columns.
   *
   * Omit to keep the pre-brand output byte-identical.
   */
  brand?: WorkbookBrand;
  // --- Design Excellence, Slice D1: optional resolved design-theme block (carry-only) ---
  /**
   * Resolved design theme block (from the corpus theme layer, once it
   * lands). Carry-only in this slice: forwarded to the sidecar's Pydantic
   * model but not read by the builder. Omit to keep output unchanged.
   */
  designTheme?: DesignTheme;
  // --- Phase E4: optional stories (Tableau storyboard dashboards) ---
  /**
   * Stories to append after the regular dashboard, inside the SAME
   * `<dashboards>` container the sidecar emits. Every `capturedSheet` must
   * name one of `sheets[].title` or the regular dashboard — the sidecar
   * validates this and raises loudly (never a silently-broken reference)
   * otherwise.
   *
   * Omit to keep the pre-story output byte-identical.
   */
  stories?: Story[];
}

const HEALTH_TIMEOUT_MS = 30_000;
const HEALTH_POLL_MS = 250;
/** Maximum spawn attempts when auto-selecting an ephemeral port (not used for explicit ports). */
const MAX_START_ATTEMPTS = 3;

/**
 * Ask the OS for a free port on `host` by binding to port 0, then releasing it.
 *
 * Binds to the same host the sidecar will use so the probe reflects the
 * correct network interface's availability.
 *
 * @param host - Loopback or interface address to probe (e.g. "127.0.0.1").
 * @returns Resolves to a positive integer port number that was free at call time.
 */
export function getFreePort(host: string): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = createServer();
    server.listen({ host, port: 0 }, () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        server.close(() => reject(new Error("Could not determine free port")));
        return;
      }
      const port = addr.port;
      server.close((err) => {
        if (err) {
          reject(err);
        } else {
          resolve(port);
        }
      });
    });
    server.on("error", (err) => reject(err));
  });
}

/** Resolve the bundled sidecar directory relative to this module (works in src/ and dist/). */
function sidecarDir(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "sidecar");
}

/**
 * Spawns and talks to the Python authoring sidecar over loopback HTTP.
 *
 * Hardening: the sidecar binds 127.0.0.1 only; a per-spawn random token is passed
 * to the child via env and sent as `X-Sidecar-Token` on every request, so other
 * local processes cannot drive the authoring endpoints. The child's stdout/stderr
 * are kept off this process's stdout (which is the MCP stdio transport).
 */
export class AuthoringSidecar {
  private proc?: ChildProcess;
  private token = "";
  private stderr = "";
  /** The port actually used by the running child process (0 until start() resolves). */
  private actualPort = 0;

  /**
   * @param host - Loopback host for uvicorn (default "127.0.0.1").
   * @param port - Explicit port to bind. When `undefined` the sidecar picks a
   *   free ephemeral port automatically and retries up to `MAX_START_ATTEMPTS`
   *   times if the chosen port turns out to be taken. An explicit port is never
   *   retried — a bind failure is surfaced immediately so the caller can
   *   diagnose the conflict.
   * @param cwd - Working directory for the `uv run uvicorn` child process.
   */
  constructor(
    private readonly host: string = "127.0.0.1",
    private readonly port: number | undefined = undefined,
    private readonly cwd: string = sidecarDir(),
  ) {}

  private get baseUrl(): string {
    return `http://${this.host}:${this.actualPort}`;
  }

  async start(): Promise<void> {
    if (this.proc) return;

    const autoSelect = this.port === undefined;
    const maxAttempts = autoSelect ? MAX_START_ATTEMPTS : 1;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const resolvedPort = autoSelect ? await getFreePort(this.host) : (this.port as number);
      const result = await this._tryStart(resolvedPort);
      if (result === "ok") return;

      const { error, isBindError } = result;

      if (autoSelect && isBindError && attempt < maxAttempts) {
        // The ephemeral port was taken between probe and bind; clean up and retry.
        await this._killProc();
        continue;
      }

      // Explicit port bind failure → fail loudly. Also exhausted retries.
      await this._killProc();
      throw error;
    }
  }

  /**
   * Attempt to spawn uvicorn on `port`.
   *
   * @returns `"ok"` on success, or an object describing the failure.
   */
  private async _tryStart(
    port: number,
  ): Promise<"ok" | { error: Error; isBindError: boolean }> {
    this.token = randomBytes(24).toString("hex");
    this.actualPort = port;
    this.stderr = "";

    this.proc = spawn(
      "uv",
      [
        "run",
        "--directory",
        this.cwd,
        "uvicorn",
        "server:app",
        "--host",
        this.host,
        "--port",
        String(port),
        "--log-level",
        "warning",
      ],
      {
        env: { ...process.env, SIDECAR_TOKEN: this.token },
        // stdout ignored so it never corrupts the MCP stdio channel; stderr captured.
        stdio: ["ignore", "ignore", "pipe"],
      },
    );

    this.proc.stderr?.on("data", (d: Buffer) => {
      this.stderr = (this.stderr + d.toString()).slice(-8192);
    });

    let exited = false;
    this.proc.on("exit", (code) => {
      exited = true;
      if (code && code !== 0) {
        // Surface async crash context for the next request.
        this.stderr += `\n[sidecar exited with code ${code}]`;
      }
    });

    const deadline = Date.now() + HEALTH_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (exited) {
        const isBindError = this._isBindError(this.stderr);
        return {
          error: new Error(`Sidecar process exited during startup.\n${this.stderr.trim()}`),
          isBindError,
        };
      }
      if (await this.healthy()) return "ok";
      await sleep(HEALTH_POLL_MS);
    }

    // Health-check timed out — not a bind error, just a slow/broken startup.
    return {
      error: new Error(
        `Sidecar did not become healthy within ${HEALTH_TIMEOUT_MS}ms.\n${this.stderr.trim()}`,
      ),
      isBindError: false,
    };
  }

  /** Returns true when stderr contains a port-already-in-use signal. */
  private _isBindError(stderr: string): boolean {
    const lower = stderr.toLowerCase();
    return (
      lower.includes("address already in use") ||
      lower.includes("errno 48") ||
      lower.includes("error while attempting to bind")
    );
  }

  private async _killProc(): Promise<void> {
    if (!this.proc) return;
    const proc = this.proc;
    this.proc = undefined;
    this.actualPort = 0;
    if (!proc.killed) proc.kill("SIGTERM");
    // Give the OS a moment to release the port before the next attempt.
    await sleep(50);
  }

  private async healthy(): Promise<boolean> {
    try {
      const res = await request(`${this.baseUrl}/health`, {
        method: "GET",
        headers: { "X-Sidecar-Token": this.token },
      });
      if (res.statusCode !== 200) {
        await res.body.text();
        return false;
      }
      const json = (await res.body.json()) as { status?: string };
      return json.status === "ok";
    } catch {
      return false;
    }
  }

  private async post<T>(path: string, payload: unknown): Promise<T> {
    const res = await request(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Sidecar-Token": this.token,
      },
      body: JSON.stringify(payload),
    });
    if (res.statusCode >= 400) {
      const text = await res.body.text();
      throw new Error(`Sidecar ${res.statusCode} on POST ${path}: ${text}`);
    }
    return (await res.body.json()) as T;
  }

  async buildDatasourceFromFile(
    args: FileArgs,
  ): Promise<{ tdsxPath: string; columns: ColumnInfo[]; hyperPath: string }> {
    const payload: Record<string, unknown> = {
      name: args.name,
      filePath: args.filePath,
    };
    if (args.fileType) payload["fileType"] = args.fileType;
    if (args.excelSheet !== undefined) payload["excelSheet"] = args.excelSheet;
    if (args.jsonPath) payload["jsonPath"] = args.jsonPath;
    if (args.encoding) payload["encoding"] = args.encoding;
    if (args.delimiter) payload["delimiter"] = args.delimiter;

    const result = await this.post<FileResult>("/datasource/from-file", payload);
    return { tdsxPath: result.path, columns: result.columns, hyperPath: result.hyperPath };
  }

  /**
   * Build a live-connection `.tds` (Snowflake or Presto — no extract, no
   * credentials; Phase E2 slice C). Credentials are applied separately at
   * publish time via {@link TableauRestClient.publishDatasource}'s
   * `credentials` option, never sent to this route.
   */
  async buildLiveDatasource(args: LiveDatasourceArgs): Promise<{ tdsPath: string }> {
    const { path } = await this.post<BuildResult>("/datasource/live", args);
    return { tdsPath: path };
  }

  async buildDatasourceFromQuery(args: QueryArgs): Promise<{ tdsxPath: string }> {
    const { path } = await this.post<BuildResult>("/datasource/from-query", args);
    return { tdsxPath: path };
  }

  async buildDatasourceFromTable(args: TableArgs): Promise<{ tdsxPath: string }> {
    const { path } = await this.post<BuildResult>("/datasource/from-table", args);
    return { tdsxPath: path };
  }

  async buildStarterWorkbook(args: WorkbookArgs): Promise<{ twbxPath: string }> {
    const { path } = await this.post<BuildResult>("/workbook/starter", args);
    return { twbxPath: path };
  }

  async buildDashboardWorkbook(args: DashboardWorkbookArgs): Promise<{ twbxPath: string }> {
    const payload: Record<string, unknown> = {
      datasourceName: args.datasourceName,
      datasourceContentUrl: args.datasourceContentUrl,
      site: args.site,
      serverUrl: args.serverUrl ?? "",
      sheets: args.sheets,
      dashboards: [
        {
          name: "Dashboard",
          sheetTitles: args.dashboardSheetTitles,
        },
      ],
      dashboardLayout: args.dashboardLayout ?? "tiled_vertical",
      canvasWidth: args.canvasWidth ?? 1000,
      canvasHeight: args.canvasHeight ?? 800,
    };
    // Embed the extract directly when available — this is the path that
    // renders on Tableau Cloud (federated connection, self-contained .twbx).
    if (args.hyperPath) {
      payload["hyperPath"] = args.hyperPath;
    }
    // Phase-1 optional dashboard-level fields (Slice 2: carry end-to-end).
    // Slice 3 will consume these in the builder; here we just forward them
    // so the sidecar Pydantic models can parse and round-trip them.
    if (args.dashboardTitle !== undefined) {
      payload["dashboardTitle"] = args.dashboardTitle;
    }
    if (args.dashboardSubtitle !== undefined) {
      payload["dashboardSubtitle"] = args.dashboardSubtitle;
    }
    if (args.textZones !== undefined) {
      payload["textZones"] = args.textZones;
    }
    if (args.layoutGrammar !== undefined) {
      payload["layoutGrammar"] = args.layoutGrammar;
    }
    // Phase E1 (Slice B): resolved brand block, forwarded as-is (camelCase on
    // the wire; the sidecar's Pydantic model accepts it and its model_dump()
    // produces the snake_case dict twb_builder reads).
    if (args.brand !== undefined) {
      payload["brand"] = args.brand;
    }
    // Design Excellence, Slice D1: resolved design-theme block, forwarded
    // as-is (camelCase on the wire; the sidecar's DesignThemeModel accepts
    // it and its model_dump() produces the snake_case dict — unread by the
    // builder in this slice, same carry-only precedent as Phase-1 encodings).
    if (args.designTheme !== undefined) {
      payload["designTheme"] = args.designTheme;
    }
    // Phase E4: stories, forwarded as-is (camelCase on the wire; the
    // sidecar's StoryModel/StoryPointModel accept it and their model_dump()
    // produces the snake_case dict twb_builder._build_story reads).
    if (args.stories !== undefined) {
      payload["stories"] = args.stories;
    }
    const { path } = await this.post<BuildResult>("/workbook/dashboard", payload);
    return { twbxPath: path };
  }

  async stop(): Promise<void> {
    if (!this.proc) return;
    const proc = this.proc;
    this.proc = undefined;
    this.actualPort = 0;
    if (!proc.killed) proc.kill("SIGTERM");
  }
}
