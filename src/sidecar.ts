import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
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

export interface WorkbookArgs {
  /** Display name / caption of the published datasource. */
  datasourceName: string;
  /** Server-assigned contentUrl, used for the workbook's repository-location binding. */
  datasourceContentUrl: string;
  /** Site contentUrl (may be empty for the Default site). */
  site: string;
  sheets: SheetSpec[];
}

const HEALTH_TIMEOUT_MS = 30_000;
const HEALTH_POLL_MS = 250;

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

  constructor(
    private readonly host: string = "127.0.0.1",
    private readonly port: number = 8899,
    private readonly cwd: string = sidecarDir(),
  ) {}

  private get baseUrl(): string {
    return `http://${this.host}:${this.port}`;
  }

  async start(): Promise<void> {
    if (this.proc) return;
    this.token = randomBytes(24).toString("hex");

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
        String(this.port),
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
        throw new Error(`Sidecar process exited during startup.\n${this.stderr.trim()}`);
      }
      if (await this.healthy()) return;
      await sleep(HEALTH_POLL_MS);
    }
    await this.stop();
    throw new Error(
      `Sidecar did not become healthy within ${HEALTH_TIMEOUT_MS}ms.\n${this.stderr.trim()}`,
    );
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

  async stop(): Promise<void> {
    if (!this.proc) return;
    const proc = this.proc;
    this.proc = undefined;
    if (!proc.killed) proc.kill("SIGTERM");
  }
}
