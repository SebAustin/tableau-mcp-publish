/**
 * Slice T0 (design-excellence PLAN.md) — acquire the top-100 Tableau Public
 * corpus for reverse-engineering dashboard/story design norms.
 *
 * Pages Tableau Public's "Viz of the Day" discover feed (Tableau's own
 * editorial "best of" — the right definition of "top") in feed/curation
 * order, then downloads each candidate's workbook via the proven
 * `workbooks/<repo>.twb` endpoint, which actually returns a `.twbx` zip body
 * for most workbooks (validated by magic bytes, never by `content-type`,
 * which is `application/x-twb` for both shapes).
 *
 * Resumable: files already present under `design/references/top100/` are
 * skipped (no network call, no sleep) and counted toward the 100-success
 * target using their on-disk bytes/sha256. Safe to interrupt and rerun.
 *
 * Guardrails: 1.1s politeness delay between every network request (feed
 * pages + downloads alike), 60s per-file timeout, 25MB per-file cap (stream
 * aborted over cap, nothing partial saved), 1.5GB total-bytes cap for the
 * run, no auth/cookies.
 *
 * Fallback ladder (PLAN.md): stop at 100 successes; if fewer than 100 are
 * reached after 300 candidates (or feed exhaustion), accept N >= 60 with the
 * shortfall + a failure-reason histogram recorded in the manifest header; if
 * N < 60, exit 2 so the caller can decide how to proceed.
 *
 * Output: raw `.twbx`/`.twb` files (gitignored — never committed) plus
 * `design/references/top100/manifest.yaml`, the committed provenance
 * artifact for Slice T1's miner.
 *
 * Usage:
 *   npx tsx scripts/fetch-top100.ts
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { request } from "undici";
import { stringify as stringifyYaml } from "yaml";
import { z } from "zod";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(MODULE_DIR, "..");
const OUT_DIR = resolve(REPO_ROOT, "design/references/top100");
const MANIFEST_PATH = resolve(OUT_DIR, "manifest.yaml");

const FEED_URL = "https://public.tableau.com/public/apis/bff/discover/v2/vizzes/viz-of-the-day";
const DOWNLOAD_BASE = "https://public.tableau.com/workbooks";

/** The feed 400s on any limit other than 12 — verified live. */
const FEED_PAGE_SIZE = 12;
/** Stop paging the feed once this many candidates have been collected. */
const MAX_CANDIDATES = 300;
/** Stop downloading once this many successes are reached. */
const TARGET_SUCCESSES = 100;
/** Fallback floor — below this, the run is a hard failure (exit 2). */
const MIN_ACCEPTABLE_SUCCESSES = 60;
/** Politeness delay between every network request (feed page or download). */
const POLITE_DELAY_MS = 1_100;
/** Per-file network timeout (headers + body), in ms. */
const DOWNLOAD_TIMEOUT_MS = 60_000;
/** Per-file size cap — the stream is aborted (nothing saved) past this. */
const MAX_FILE_BYTES = 25 * 1024 * 1024;
/** Total-bytes-downloaded cap for the whole run. */
const MAX_TOTAL_BYTES = 1.5 * 1024 * 1024 * 1024;

/** Zip local file header magic (PK\x03\x04) — a `.twbx` body. */
const ZIP_MAGIC = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
/** A bare `.twb` XML body starts with this (after optional BOM/whitespace). */
const XML_PREFIX = "<?xml";

// ---------------------------------------------------------------------------
// Feed response schema (validated at the network boundary)
// ---------------------------------------------------------------------------

const FeedItemSchema = z.object({
  workbookRepoUrl: z.string().min(1),
  title: z.string().default(""),
  authorProfileName: z.string().default(""),
  authorDisplayName: z.string().default(""),
  viewCount: z.number().default(0),
  numberOfFavorites: z.number().default(0),
  curatedAt: z.string().default(""),
});
type FeedItem = z.infer<typeof FeedItemSchema>;

const FeedResponseSchema = z.object({
  contents: z.array(z.unknown()).default([]),
  next: z.number().nullable().optional(),
});

// ---------------------------------------------------------------------------
// Manifest types
// ---------------------------------------------------------------------------

type ItemStatus = "downloaded" | "download-disabled" | "invalid-body" | "too-large" | "error";

interface ManifestItem {
  repoUrl: string;
  title: string;
  author: string;
  profileName: string;
  viewCount: number;
  favorites: number;
  curatedAt: string;
  status: ItemStatus;
  bytes?: number;
  sha256?: string;
  savedAs?: string;
  reason?: string;
}

interface Manifest {
  generatedAt: string;
  feedDepthSeen: number;
  candidatesSeen: number;
  successes: number;
  failures: number;
  shortfall: number;
  reasonHistogram: Record<string, number>;
  items: ManifestItem[];
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function getErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Existing on-disk save path for a repoUrl, `.twbx` checked before `.twb`. */
function existingSavePath(repoUrl: string): string | undefined {
  const twbxPath = resolve(OUT_DIR, `${repoUrl}.twbx`);
  if (existsSync(twbxPath)) return twbxPath;
  const twbPath = resolve(OUT_DIR, `${repoUrl}.twb`);
  if (existsSync(twbPath)) return twbPath;
  return undefined;
}

/** Detects the saved extension by magic bytes; `undefined` when neither matches. */
function detectExtension(body: Buffer): "twbx" | "twb" | undefined {
  if (body.subarray(0, ZIP_MAGIC.length).equals(ZIP_MAGIC)) return "twbx";
  const raw = body.subarray(0, 256).toString("utf8").trimStart();
  const head = raw.startsWith("\uFEFF") ? raw.slice(1) : raw;
  if (head.startsWith(XML_PREFIX)) return "twb";
  return undefined;
}

// ---------------------------------------------------------------------------
// Phase 1 — page the VOTD feed for candidates
// ---------------------------------------------------------------------------

interface FeedPage {
  items: FeedItem[];
  next: number | null | undefined;
}

async function fetchFeedPage(startIndex: number): Promise<FeedPage> {
  const url = `${FEED_URL}?startIndex=${startIndex}&limit=${FEED_PAGE_SIZE}`;
  const res = await request(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    headersTimeout: DOWNLOAD_TIMEOUT_MS,
    bodyTimeout: DOWNLOAD_TIMEOUT_MS,
  });
  if (res.statusCode !== 200) {
    throw new Error(`VOTD feed startIndex=${startIndex} returned HTTP ${res.statusCode}`);
  }
  const json = await res.body.json();
  const parsed = FeedResponseSchema.parse(json);
  const items = parsed.contents
    .map((raw) => FeedItemSchema.safeParse(raw))
    .filter((r): r is { success: true; data: FeedItem } => r.success)
    .map((r) => r.data);
  return { items, next: parsed.next };
}

/**
 * Collects up to `MAX_CANDIDATES` feed items, in curation order, paging by
 * `FEED_PAGE_SIZE` from startIndex 0 until the cap is hit or the feed is
 * exhausted (`next` is null/undefined, or a page returns no items).
 */
async function collectCandidates(): Promise<{ candidates: FeedItem[]; feedDepthSeen: number }> {
  const candidates: FeedItem[] = [];
  let startIndex = 0;
  let feedDepthSeen = 0;

  while (candidates.length < MAX_CANDIDATES) {
    const page = await fetchFeedPage(startIndex);
    await sleep(POLITE_DELAY_MS);

    if (page.items.length === 0) break;
    for (const item of page.items) {
      if (candidates.length >= MAX_CANDIDATES) break;
      candidates.push(item);
    }
    feedDepthSeen = startIndex + page.items.length;

    if (page.next === null || page.next === undefined) break;
    startIndex = page.next;
  }

  return { candidates, feedDepthSeen };
}

// ---------------------------------------------------------------------------
// Phase 2 — download each candidate
// ---------------------------------------------------------------------------

interface DownloadOutcome {
  status: ItemStatus;
  bytes?: number;
  sha256?: string;
  savedAs?: string;
  reason?: string;
}

/** Downloads one workbook body, capping at `MAX_FILE_BYTES` (stream aborted, nothing saved). */
async function downloadBody(repoUrl: string): Promise<{ body: Buffer } | { tooLarge: true }> {
  const url = `${DOWNLOAD_BASE}/${encodeURIComponent(repoUrl)}.twb`;
  const res = await request(url, {
    method: "GET",
    headersTimeout: DOWNLOAD_TIMEOUT_MS,
    bodyTimeout: DOWNLOAD_TIMEOUT_MS,
  });

  if (res.statusCode === 404) {
    // Drain the (small) body so the socket can be reused/closed cleanly.
    await res.body.text().catch(() => undefined);
    throw new HttpStatusError(404);
  }
  if (res.statusCode !== 200) {
    await res.body.text().catch(() => undefined);
    throw new HttpStatusError(res.statusCode);
  }

  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of res.body) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as Uint8Array);
    total += buf.length;
    if (total > MAX_FILE_BYTES) {
      // Stop reading; let the response drain/close on its own. Nothing is saved.
      return { tooLarge: true };
    }
    chunks.push(buf);
  }
  return { body: Buffer.concat(chunks) };
}

class HttpStatusError extends Error {
  readonly statusCode: number;
  constructor(statusCode: number) {
    super(`HTTP ${statusCode}`);
    this.statusCode = statusCode;
  }
}

interface TotalBudget {
  remainingBytes: number;
}

async function downloadCandidate(repoUrl: string, budget: TotalBudget): Promise<DownloadOutcome> {
  if (budget.remainingBytes <= 0) {
    return { status: "error", reason: "total download cap (1.5GB) reached" };
  }

  let result: { body: Buffer } | { tooLarge: true };
  try {
    result = await downloadBody(repoUrl);
  } catch (err: unknown) {
    if (err instanceof HttpStatusError && err.statusCode === 404) {
      return { status: "download-disabled", reason: "HTTP 404" };
    }
    return { status: "error", reason: getErrorMessage(err) };
  }

  if ("tooLarge" in result) {
    return { status: "too-large", reason: `exceeds ${MAX_FILE_BYTES} byte cap` };
  }

  const { body } = result;
  const extension = detectExtension(body);
  if (!extension) {
    return { status: "invalid-body", reason: "body is neither zip (PK) nor bare XML" };
  }

  const savedPath = resolve(OUT_DIR, `${repoUrl}.${extension}`);
  writeFileSync(savedPath, body);
  budget.remainingBytes -= body.length;

  return {
    status: "downloaded",
    bytes: body.length,
    sha256: createHash("sha256").update(body).digest("hex"),
    savedAs: `design/references/top100/${repoUrl}.${extension}`,
  };
}

/** Builds the manifest entry for a file already present on disk (resumable skip). */
function reuseExistingEntry(savePath: string): { bytes: number; sha256: string; savedAs: string } {
  const bytes = statSync(savePath).size;
  const sha256 = createHash("sha256").update(readFileSync(savePath)).digest("hex");
  return { bytes, sha256, savedAs: `design/references/top100/${savePath.split("/").pop()}` };
}

// ---------------------------------------------------------------------------
// Manifest I/O
// ---------------------------------------------------------------------------

function writeManifest(manifest: Manifest): void {
  writeFileSync(MANIFEST_PATH, stringifyYaml(manifest, { sortMapEntries: false }));
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  mkdirSync(OUT_DIR, { recursive: true });

  const { candidates, feedDepthSeen } = await collectCandidates();

  const budget: TotalBudget = { remainingBytes: MAX_TOTAL_BYTES };
  const items: ManifestItem[] = [];
  const reasonHistogram: Record<string, number> = {};
  let successes = 0;

  for (const candidate of candidates) {
    if (successes >= TARGET_SUCCESSES) break;

    const { workbookRepoUrl: repoUrl } = candidate;
    const base = {
      repoUrl,
      title: candidate.title,
      author: candidate.authorDisplayName,
      profileName: candidate.authorProfileName,
      viewCount: candidate.viewCount,
      favorites: candidate.numberOfFavorites,
      curatedAt: candidate.curatedAt,
    };

    const existing = existingSavePath(repoUrl);
    if (existing) {
      const reused = reuseExistingEntry(existing);
      items.push({ ...base, status: "downloaded", ...reused });
      successes += 1;
      console.log(`downloaded ${repoUrl}`);
      continue;
    }

    const outcome = await downloadCandidate(repoUrl, budget);
    await sleep(POLITE_DELAY_MS);

    items.push({
      ...base,
      status: outcome.status,
      ...(outcome.bytes !== undefined ? { bytes: outcome.bytes } : {}),
      ...(outcome.sha256 !== undefined ? { sha256: outcome.sha256 } : {}),
      ...(outcome.savedAs !== undefined ? { savedAs: outcome.savedAs } : {}),
      ...(outcome.reason !== undefined ? { reason: outcome.reason } : {}),
    });

    if (outcome.status === "downloaded") {
      successes += 1;
    } else {
      reasonHistogram[outcome.status] = (reasonHistogram[outcome.status] ?? 0) + 1;
    }
    console.log(`${outcome.status} ${repoUrl}`);
  }

  const failures = items.length - successes;
  const shortfall = Math.max(0, TARGET_SUCCESSES - successes);

  const manifest: Manifest = {
    generatedAt: new Date().toISOString(),
    feedDepthSeen,
    candidatesSeen: candidates.length,
    successes,
    failures,
    shortfall,
    reasonHistogram,
    items,
  };
  writeManifest(manifest);

  if (successes < MIN_ACCEPTABLE_SUCCESSES) {
    console.error(
      `fetch-top100: only ${successes} successes (< ${MIN_ACCEPTABLE_SUCCESSES} floor). ` +
        `Manifest written to ${MANIFEST_PATH} for inspection.`,
    );
    process.exit(2);
  }
}

main().catch((err: unknown) => {
  console.error(getErrorMessage(err));
  process.exit(1);
});
