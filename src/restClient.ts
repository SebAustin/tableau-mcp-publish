import { readFile, stat } from "node:fs/promises";
import { basename } from "node:path";
import { randomBytes } from "node:crypto";
import { request } from "undici";
import type { Config } from "./config.js";

export interface Session {
  token: string;
  siteId: string;
  userId: string;
}

export interface PublishResult {
  id: string;
  url: string;
  /** Server-assigned slug; present when publishing a datasource. */
  contentUrl?: string;
}

export interface ProjectRef {
  id: string;
  name: string;
}

export interface ContentItem {
  id: string;
  name: string;
  type: "datasource" | "workbook";
  projectName?: string;
  updatedAt?: string;
}

export type ContentType = "datasource" | "workbook";
export type PermissionMode = "Allow" | "Deny";
export type GranteeType = "user" | "group";

export interface PermissionGrant {
  granteeId: string;
  granteeType?: GranteeType;
  capability: string;
  mode: PermissionMode;
}

/** Tableau's documented threshold: files larger than this must use chunked upload. */
export const SINGLE_REQUEST_LIMIT_BYTES = 64 * 1024 * 1024;
/** Max bytes per appendFileUpload chunk (Tableau allows up to 64 MB). */
export const DEFAULT_CHUNK_SIZE_BYTES = 64 * 1024 * 1024;

/**
 * Pure helper: choose the publish strategy from a file size.
 *
 * Align to official TSC `>=` boundary: files at or above the limit
 * (incl. exactly 64 MiB) use the chunked path. Official
 * `server-client-python` (`datasources_endpoint.py`) chunks when
 * `file_size >= FILESIZE_LIMIT_MB * BYTES_PER_MB` — the `>=` boundary
 * is safer because a single multipart request at exactly 64 MiB exceeds
 * the limit once boundary overhead is added.
 */
export function selectPublishStrategy(
  sizeBytes: number,
  limit: number = SINGLE_REQUEST_LIMIT_BYTES,
): "single" | "chunked" {
  return sizeBytes >= limit ? "chunked" : "single";
}

/** Pure helper: split a buffer into <=chunkSize pieces. Unit-tested without big allocations. */
export function splitIntoChunks(buf: Buffer, chunkSize: number): Buffer[] {
  if (chunkSize <= 0) throw new Error("chunkSize must be positive");
  if (buf.length === 0) return [];
  const chunks: Buffer[] = [];
  for (let offset = 0; offset < buf.length; offset += chunkSize) {
    chunks.push(buf.subarray(offset, Math.min(offset + chunkSize, buf.length)));
  }
  return chunks;
}

function asArray<T>(value: T | T[] | undefined | null): T[] {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

interface MultipartPart {
  name: string;
  data: Buffer;
  contentType: string;
  filename?: string;
}

/** Strip characters that could break out of a Content-Disposition header (CRLF/quote/backslash). */
function sanitizeHeaderValue(value: string): string {
  return value.replace(/[\r\n"\\]/g, "_");
}

/** Build a `multipart/mixed` body the way the Tableau REST API expects. */
export function buildMultipart(parts: MultipartPart[]): { body: Buffer; contentType: string } {
  const boundary = `boundary-${randomBytes(16).toString("hex")}`;
  const segments: Buffer[] = [];
  for (const part of parts) {
    const name = sanitizeHeaderValue(part.name);
    const disposition = part.filename
      ? `name="${name}"; filename="${sanitizeHeaderValue(part.filename)}"`
      : `name="${name}"`;
    const header =
      `--${boundary}\r\n` +
      `Content-Disposition: ${disposition}\r\n` +
      `Content-Type: ${part.contentType}\r\n\r\n`;
    segments.push(Buffer.from(header, "utf8"), part.data, Buffer.from("\r\n", "utf8"));
  }
  segments.push(Buffer.from(`--${boundary}--\r\n`, "utf8"));
  return { body: Buffer.concat(segments), contentType: `multipart/mixed; boundary=${boundary}` };
}

interface ApiOptions {
  query?: Record<string, string | number | boolean | undefined>;
  body?: Buffer | string;
  contentType?: string;
  auth?: boolean;
  parse?: "json" | "none";
}

/**
 * Minimal Tableau REST API client. Owns PAT sign-in and the publish paths
 * (single multipart for <=64 MB, chunked fileUploads for larger). The PAT secret
 * is only ever sent in the sign-in body and is never logged.
 */
export class TableauRestClient {
  private session?: Session;

  constructor(
    private readonly cfg: Config,
    private readonly chunkSize: number = DEFAULT_CHUNK_SIZE_BYTES,
  ) {}

  private get baseUrl(): string {
    return `${this.cfg.server}/api/${this.cfg.apiVersion}`;
  }

  private requireSession(): Session {
    if (!this.session) throw new Error("Not signed in. Call signIn() first.");
    return this.session;
  }

  private async api(method: string, path: string, opts: ApiOptions = {}): Promise<unknown> {
    const { query, body, contentType, auth = true, parse = "json" } = opts;
    const url = new URL(`${this.baseUrl}${path}`);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined) url.searchParams.set(k, String(v));
      }
    }
    const headers: Record<string, string> = { Accept: "application/json" };
    if (auth) headers["X-Tableau-Auth"] = this.requireSession().token;
    if (contentType) headers["Content-Type"] = contentType;

    const res = await request(url.toString(), { method, headers, body });
    if (res.statusCode >= 400) {
      // Log the full upstream body to stderr for debugging, but return only a
      // redacted message to the caller (the agent) to avoid leaking site internals.
      const text = await res.body.text();
      process.stderr.write(
        `[tableau-mcp-publish] Tableau API ${res.statusCode} on ${method} ${path}: ${text}\n`,
      );
      throw new Error(`Tableau API request failed (${res.statusCode}) on ${method} ${path}.`);
    }
    if (parse === "none") {
      await res.body.text();
      return undefined;
    }
    return res.body.json();
  }

  async signIn(): Promise<Session> {
    const payload = {
      credentials: {
        personalAccessTokenName: this.cfg.patName,
        personalAccessTokenSecret: this.cfg.patValue,
        site: { contentUrl: this.cfg.siteName },
      },
    };
    const json = (await this.api("POST", "/auth/signin", {
      auth: false,
      body: JSON.stringify(payload),
      contentType: "application/json",
    })) as { credentials?: { token?: string; site?: { id?: string }; user?: { id?: string } } };

    const creds = json.credentials;
    if (!creds?.token || !creds.site?.id || !creds.user?.id) {
      throw new Error("Sign-in response missing token/site/user.");
    }
    this.session = { token: creds.token, siteId: creds.site.id, userId: creds.user.id };
    return this.session;
  }

  async signOut(): Promise<void> {
    if (!this.session) return;
    try {
      await this.api("POST", "/auth/signout", { parse: "none" });
    } finally {
      this.session = undefined;
    }
  }

  private async getAllPages<T>(path: string, pick: (page: unknown) => T[]): Promise<T[]> {
    const { siteId } = this.requireSession();
    const items: T[] = [];
    let pageNumber = 1;
    const pageSize = 100;
    for (;;) {
      const json = (await this.api("GET", `/sites/${siteId}${path}`, {
        query: { pageSize, pageNumber },
      })) as { pagination?: { totalAvailable?: string } };
      items.push(...pick(json));
      const total = Number(json.pagination?.totalAvailable ?? items.length);
      if (items.length >= total || pick(json).length === 0) break;
      pageNumber += 1;
    }
    return items;
  }

  async listProjects(): Promise<ProjectRef[]> {
    return this.getAllPages<ProjectRef>("/projects", (page) => {
      const projects = (page as { projects?: { project?: ProjectRef[] } }).projects?.project ?? [];
      return projects.map((p) => ({ id: p.id, name: p.name }));
    });
  }

  /** Resolve a project name to its LUID. Refuses empty and the Default project (governed publishing). */
  async resolveProjectId(projectName: string): Promise<string> {
    if (!projectName.trim()) {
      throw new Error("A non-empty projectName is required (never publish to Default silently).");
    }
    if (projectName.trim().toLowerCase() === "default") {
      throw new Error('Refusing to target the "Default" project. Specify a named, governed project.');
    }
    const projects = await this.listProjects();
    const match = projects.find((p) => p.name === projectName);
    if (!match) {
      throw new Error(
        `Project "${projectName}" not found. Available: ${projects.map((p) => p.name).join(", ") || "(none)"}`,
      );
    }
    return match.id;
  }

  async createProject(name: string, description?: string): Promise<ProjectRef> {
    const { siteId } = this.requireSession();
    const desc = description ? ` description="${xmlEscape(description)}"` : "";
    const body = `<tsRequest><project name="${xmlEscape(name)}"${desc} /></tsRequest>`;
    const json = (await this.api("POST", `/sites/${siteId}/projects`, {
      body,
      contentType: "text/xml",
    })) as { project?: ProjectRef };
    if (!json.project?.id) throw new Error("createProject: missing project id in response.");
    return { id: json.project.id, name: json.project.name };
  }

  /** Browser URL fallback when the REST response omits webpageUrl (uses LUID — may not open in Cloud UI). */
  private fallbackCloudUrl(type: ContentType, id: string): string {
    const sitePart = this.cfg.siteName ? `/site/${this.cfg.siteName}` : "";
    const seg = type === "datasource" ? "datasources" : "workbooks";
    return `${this.cfg.server}/#${sitePart}/${seg}/${id}`;
  }

  async publishDatasource(
    filePath: string,
    name: string,
    projectId: string,
    overwrite: boolean,
  ): Promise<PublishResult> {
    return this.publish("datasource", filePath, name, projectId, overwrite);
  }

  async publishWorkbook(
    filePath: string,
    name: string,
    projectId: string,
    overwrite: boolean,
  ): Promise<PublishResult> {
    return this.publish("workbook", filePath, name, projectId, overwrite, {
      skipConnectionCheck: true,
    });
  }

  private async publish(
    type: ContentType,
    filePath: string,
    name: string,
    projectId: string,
    overwrite: boolean,
    extraQuery?: Record<string, string | boolean | undefined>,
  ): Promise<PublishResult> {
    const { siteId } = this.requireSession();
    const { size } = await stat(filePath);
    const fileExt = filePath.split(".").pop() ?? (type === "datasource" ? "tdsx" : "twbx");
    const elem = type === "datasource" ? "datasource" : "workbook";
    const collection = type === "datasource" ? "datasources" : "workbooks";
    const filePartName = type === "datasource" ? "tableau_datasource" : "tableau_workbook";
    const requestXml =
      `<tsRequest><${elem} name="${xmlEscape(name)}">` +
      `<project id="${xmlEscape(projectId)}" /></${elem}></tsRequest>`;

    let json: unknown;
    if (selectPublishStrategy(size, this.chunkSize) === "single") {
      const fileData = await readFile(filePath);
      const { body, contentType } = buildMultipart([
        { name: "request_payload", data: Buffer.from(requestXml, "utf8"), contentType: "text/xml" },
        {
          name: filePartName,
          data: fileData,
          contentType: "application/octet-stream",
          filename: basename(filePath),
        },
      ]);
      json = await this.api("POST", `/sites/${siteId}/${collection}`, {
        query: { overwrite, ...extraQuery },
        body,
        contentType,
      });
    } else {
      const uploadSessionId = await this.uploadInChunks(filePath);
      const { body, contentType } = buildMultipart([
        { name: "request_payload", data: Buffer.from(requestXml, "utf8"), contentType: "text/xml" },
      ]);
      const typeParam = type === "datasource" ? { datasourceType: fileExt } : { workbookType: fileExt };
      json = await this.api("POST", `/sites/${siteId}/${collection}`, {
        query: { uploadSessionId, overwrite, ...typeParam, ...extraQuery },
        body,
        contentType,
      });
    }

    const dsPublished =
      type === "datasource"
        ? (json as {
            datasource?: { id?: string; contentUrl?: string | null; webpageUrl?: string };
          }).datasource
        : undefined;
    const wbPublished =
      type === "workbook"
        ? (json as { workbook?: { id?: string; webpageUrl?: string } }).workbook
        : undefined;

    const id = dsPublished?.id ?? wbPublished?.id;
    if (!id) throw new Error(`publish ${type}: missing id in response.`);

    const contentUrl = dsPublished?.contentUrl ?? undefined;
    const webpageUrl = dsPublished?.webpageUrl ?? wbPublished?.webpageUrl;

    return {
      id,
      url: webpageUrl ?? this.fallbackCloudUrl(type, id),
      ...(contentUrl ? { contentUrl } : {}),
    };
  }

  /**
   * Chunked upload: initiate a session, append each chunk, then return the
   * uploadSessionId for finalize. If any append fails the session is abandoned
   * (we never finalize a partial upload) and the error propagates.
   */
  private async uploadInChunks(filePath: string): Promise<string> {
    const { siteId } = this.requireSession();
    const init = (await this.api("POST", `/sites/${siteId}/fileUploads`, {})) as {
      fileUpload?: { uploadSessionId?: string };
    };
    const uploadSessionId = init.fileUpload?.uploadSessionId;
    if (!uploadSessionId) throw new Error("initiateFileUpload: missing uploadSessionId.");

    const fileData = await readFile(filePath);
    const chunks = splitIntoChunks(fileData, this.chunkSize);
    for (const chunk of chunks) {
      const { body, contentType } = buildMultipart([
        { name: "request_payload", data: Buffer.alloc(0), contentType: "text/xml" },
        {
          name: "tableau_file",
          data: chunk,
          contentType: "application/octet-stream",
          filename: "chunk",
        },
      ]);
      // A failure here throws and we deliberately do NOT call the finalize POST.
      await this.api("PUT", `/sites/${siteId}/fileUploads/${uploadSessionId}`, {
        body,
        contentType,
      });
    }
    return uploadSessionId;
  }

  /** Fetch a published datasource's name + contentUrl (needed to bind a workbook to it). */
  async getDatasource(id: string): Promise<{ id: string; name: string; contentUrl: string }> {
    const { siteId } = this.requireSession();
    const json = (await this.api("GET", `/sites/${siteId}/datasources/${id}`)) as {
      datasource?: { id?: string; name?: string; contentUrl?: string | null };
    };
    const ds = json.datasource;
    if (!ds?.id) throw new Error(`getDatasource: datasource ${id} not found.`);

    let contentUrl = ds.contentUrl ?? "";
    if (!contentUrl) {
      const listed = await this.getAllPages<{ id: string; contentUrl?: string; name?: string }>(
        "/datasources",
        (page) => {
          const list = asArray(
            (page as { datasources?: { datasource?: Array<{ id?: string; contentUrl?: string; name?: string }> | { id?: string; contentUrl?: string; name?: string } } })
              .datasources?.datasource,
          );
          return list.map((d) => ({
            id: d.id ?? "",
            contentUrl: d.contentUrl,
            name: d.name,
          }));
        },
      );
      const match = listed.find((d) => d.id === id);
      contentUrl = match?.contentUrl ?? match?.name ?? ds.name ?? "";
    }

    if (!contentUrl) {
      throw new Error(
        `getDatasource: contentUrl missing for datasource ${id}. Republish the datasource or pass contentUrl from the publish response.`,
      );
    }

    return { id: ds.id, name: ds.name ?? "", contentUrl };
  }

  async refreshDatasource(datasourceId: string): Promise<void> {
    const { siteId } = this.requireSession();
    await this.api("POST", `/sites/${siteId}/datasources/${datasourceId}/refresh`, {
      body: "<tsRequest></tsRequest>",
      contentType: "text/xml",
      parse: "none",
    });
  }

  async listContent(): Promise<ContentItem[]> {
    const { siteId } = this.requireSession();
    const datasources = await this.getAllPages<ContentItem>("/datasources", (page) => {
      const list = (page as { datasources?: { datasource?: RawContent[] } }).datasources?.datasource ?? [];
      return list.map((d) => toContentItem(d, "datasource"));
    });
    const workbooks = await this.getAllPages<ContentItem>("/workbooks", (page) => {
      const list = (page as { workbooks?: { workbook?: RawContent[] } }).workbooks?.workbook ?? [];
      return list.map((w) => toContentItem(w, "workbook"));
    });
    void siteId;
    return [...datasources, ...workbooks];
  }

  async deleteContent(type: ContentType, contentId: string): Promise<void> {
    const { siteId } = this.requireSession();
    const collection = type === "datasource" ? "datasources" : "workbooks";
    await this.api("DELETE", `/sites/${siteId}/${collection}/${contentId}`, { parse: "none" });
  }

  async setPermissions(
    type: ContentType,
    contentId: string,
    grants: PermissionGrant[],
  ): Promise<void> {
    const { siteId } = this.requireSession();
    const collection = type === "datasource" ? "datasources" : "workbooks";
    // Group capabilities per grantee.
    const byGrantee = new Map<string, { type: GranteeType; caps: PermissionGrant[] }>();
    for (const g of grants) {
      const key = `${g.granteeType ?? "user"}:${g.granteeId}`;
      const entry = byGrantee.get(key) ?? { type: g.granteeType ?? "user", caps: [] };
      entry.caps.push(g);
      byGrantee.set(key, entry);
    }
    const granteeXml = [...byGrantee.entries()]
      .map(([key, { type: gType, caps }]) => {
        const granteeId = key.slice(key.indexOf(":") + 1);
        const capsXml = caps
          .map((c) => `<capability name="${xmlEscape(c.capability)}" mode="${c.mode}" />`)
          .join("");
        return (
          `<granteeCapabilities><${gType} id="${xmlEscape(granteeId)}" />` +
          `<capabilities>${capsXml}</capabilities></granteeCapabilities>`
        );
      })
      .join("");
    const elem = type === "datasource" ? "datasource" : "workbook";
    const body =
      `<tsRequest><permissions><${elem} id="${xmlEscape(contentId)}" />` +
      `${granteeXml}</permissions></tsRequest>`;
    await this.api("PUT", `/sites/${siteId}/${collection}/${contentId}/permissions`, {
      body,
      contentType: "text/xml",
      parse: "none",
    });
  }
}

interface RawContent {
  id: string;
  name: string;
  updatedAt?: string;
  project?: { name?: string };
}

function toContentItem(raw: RawContent, type: ContentType): ContentItem {
  return {
    id: raw.id,
    name: raw.name,
    type,
    projectName: raw.project?.name,
    updatedAt: raw.updatedAt,
  };
}
