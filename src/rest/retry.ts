/**
 * Bounded retry/backoff for Tableau REST + VDS calls (Phase E2 — Foundation).
 *
 * Kept deliberately generic (no Tableau-specific types) so both
 * `restClient.ts` and `rest/vds.ts` can share one hardened retry loop.
 * Callers classify their own errors into a {@link RetrySignal} via the
 * `classify` callback; this module owns only the *scheduling* policy
 * (which statuses are retriable, how long to wait, when to give up).
 */

/** The HTTP statuses this policy ever retries. Never 401, never other 4xx. */
const RETRIABLE_STATUSES: ReadonlySet<number> = new Set([429, 502, 503, 504]);

/** True for the small, deliberate set of transient statuses we retry. */
export function isRetriableStatus(status: number): boolean {
  return RETRIABLE_STATUSES.has(status);
}

export interface RetryPolicy {
  /** Total attempts including the first (non-retry) call. Default 3. */
  maxAttempts: number;
  /** Base delay for exponential backoff, in ms. */
  baseDelayMs: number;
  /** Ceiling on any single computed backoff delay, in ms. */
  maxDelayMs: number;
  /** Wall-clock budget for the whole operation (all attempts + waits), in ms. */
  totalTimeCapMs: number;
}

export const DEFAULT_RETRY_POLICY: RetryPolicy = {
  maxAttempts: 3,
  baseDelayMs: 200,
  maxDelayMs: 2_000,
  totalTimeCapMs: 8_000,
};

/** What a failed attempt tells the retry loop about itself. */
export interface RetrySignal {
  /** HTTP status of the failure, when known. */
  status: number;
  /** Parsed `Retry-After` value in ms, when the upstream sent one. */
  retryAfterMs?: number;
}

export interface RetryDeps {
  /** Injectable sleep so tests never wait for real — defaults to a real timer. */
  sleep?: (ms: number) => Promise<void>;
  /**
   * Injectable jitter source in [0, 1). Defaults to `Math.random` (real
   * jitter in production); tests inject a fixed function for deterministic
   * delay assertions.
   */
  jitterFn?: () => number;
  /** Injectable clock (ms since epoch) for the total-time cap. */
  now?: () => number;
}

const realSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * "Full jitter" exponential backoff (AWS-recommended): a random delay in
 * `[0, min(baseDelayMs * 2^(attempt-1), maxDelayMs)]`. Deterministic when
 * `jitterFn` is injected (tests pass a fixed function instead of
 * `Math.random`), so the exact delay is reproducible without ever calling a
 * real timer.
 */
export function computeBackoffMs(
  attempt: number,
  policy: Pick<RetryPolicy, "baseDelayMs" | "maxDelayMs">,
  jitterFn: () => number = Math.random,
): number {
  const exponential = Math.min(policy.baseDelayMs * 2 ** (attempt - 1), policy.maxDelayMs);
  return Math.round(exponential * jitterFn());
}

/**
 * Runs `attempt()` with bounded retry for transient failures.
 *
 * Policy:
 *  - Only retries when `idempotent` is true (see per-call-site reasoning in
 *    `restClient.ts` / `rest/vds.ts` — repeating a non-idempotent mutation
 *    risks duplicating a server-side effect if the original request actually
 *    succeeded but the response was lost).
 *  - Only retries statuses classified retriable by {@link isRetriableStatus}
 *    (429, 502, 503, 504) — never 401, never any other 4xx.
 *  - Honors an upstream `Retry-After` value when the classifier supplies
 *    one; otherwise waits `computeBackoffMs`.
 *  - Bounded by `policy.maxAttempts` and `policy.totalTimeCapMs` so a
 *    misbehaving upstream can never hang a tool call indefinitely.
 *  - On exhaustion, rethrows the last error unchanged (no extra wrapping),
 *    so the caller sees the same clear, actionable error either way.
 */
export async function withRetry<T>(
  attempt: (attemptNumber: number) => Promise<T>,
  classify: (err: unknown) => RetrySignal | undefined,
  idempotent: boolean,
  policy: RetryPolicy = DEFAULT_RETRY_POLICY,
  deps: RetryDeps = {},
): Promise<T> {
  const sleep = deps.sleep ?? realSleep;
  const jitterFn = deps.jitterFn ?? Math.random;
  const now = deps.now ?? Date.now;
  const startedAt = now();

  for (let attemptNumber = 1; attemptNumber <= policy.maxAttempts; attemptNumber += 1) {
    try {
      return await attempt(attemptNumber);
    } catch (err: unknown) {
      const signal = idempotent ? classify(err) : undefined;
      const hasBudget =
        signal !== undefined &&
        isRetriableStatus(signal.status) &&
        attemptNumber < policy.maxAttempts &&
        now() - startedAt < policy.totalTimeCapMs;

      if (!hasBudget) throw err;

      // Retry-After is honored as the server sent it (not clamped to
      // maxDelayMs) — computeBackoffMs already applies the cap internally
      // for the no-header fallback path.
      const delayMs = signal.retryAfterMs ?? computeBackoffMs(attemptNumber, policy, jitterFn);
      await sleep(delayMs);
    }
  }
  // Unreachable: the loop always returns or throws, but satisfies noImplicitReturns.
  throw new Error("withRetry: exhausted attempts without a result or error.");
}
