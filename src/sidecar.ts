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

export interface SheetSpec {
  title: string;
  markType: string;
  rows: string[];
  cols: string[];
  measures: string[];
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

export interface DashboardWorkbookArgs extends WorkbookArgs {
  /** Sheet titles to include in the dashboard (subset or all of sheets[].title). */
  dashboardSheetTitles: string[];
  /** Zone tiling direction. */
  dashboardLayout?: "tiled_vertical" | "tiled_horizontal";
  /** Canvas width in pixels. */
  canvasWidth?: number;
  /** Canvas height in pixels. */
  canvasHeight?: number;
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

  async buildDatasourceFromFile(args: FileArgs): Promise<{ tdsxPath: string }> {
    const payload: Record<string, unknown> = {
      name: args.name,
      filePath: args.filePath,
    };
    if (args.fileType) payload["fileType"] = args.fileType;
    if (args.excelSheet !== undefined) payload["excelSheet"] = args.excelSheet;
    if (args.jsonPath) payload["jsonPath"] = args.jsonPath;

    const { path } = await this.post<BuildResult>("/datasource/from-file", payload);
    return { tdsxPath: path };
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
