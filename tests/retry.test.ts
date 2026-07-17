import { describe, it, expect, vi } from "vitest";
import {
  withRetry,
  computeBackoffMs,
  isRetriableStatus,
  DEFAULT_RETRY_POLICY,
  type RetrySignal,
} from "../src/rest/retry.js";

describe("isRetriableStatus", () => {
  it("is true only for 429/502/503/504", () => {
    expect(isRetriableStatus(429)).toBe(true);
    expect(isRetriableStatus(502)).toBe(true);
    expect(isRetriableStatus(503)).toBe(true);
    expect(isRetriableStatus(504)).toBe(true);
  });

  it("is false for 401 and other 4xx/5xx", () => {
    expect(isRetriableStatus(401)).toBe(false);
    expect(isRetriableStatus(400)).toBe(false);
    expect(isRetriableStatus(403)).toBe(false);
    expect(isRetriableStatus(404)).toBe(false);
    expect(isRetriableStatus(500)).toBe(false);
    expect(isRetriableStatus(200)).toBe(false);
  });
});

describe("computeBackoffMs (deterministic-jitter exponential backoff)", () => {
  const policy = { baseDelayMs: 200, maxDelayMs: 2000 };

  it("is deterministic given a fixed jitter function", () => {
    // full jitter: delay = min(base * 2^(attempt-1), max) * jitterFn()
    expect(computeBackoffMs(1, policy, () => 1)).toBe(200); // 200 * 2^0 = 200
    expect(computeBackoffMs(2, policy, () => 1)).toBe(400); // 200 * 2^1 = 400
    expect(computeBackoffMs(3, policy, () => 1)).toBe(800); // 200 * 2^2 = 800
    expect(computeBackoffMs(1, policy, () => 0)).toBe(0);
  });

  it("caps the exponential growth at maxDelayMs", () => {
    expect(computeBackoffMs(10, policy, () => 1)).toBe(2000);
  });
});

describe("withRetry", () => {
  const noSleep = vi.fn().mockResolvedValue(undefined);

  it("retries a 429 and honors an explicit Retry-After delay, then succeeds", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    let calls = 0;
    const result = await withRetry(
      async () => {
        calls += 1;
        if (calls === 1) throw { status: 429 };
        return "ok";
      },
      (err: unknown): RetrySignal | undefined => {
        const e = err as { status?: number };
        return e.status !== undefined ? { status: e.status, retryAfterMs: 1234 } : undefined;
      },
      true,
      DEFAULT_RETRY_POLICY,
      { sleep },
    );
    expect(result).toBe("ok");
    expect(calls).toBe(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(1234);
  });

  it("retries a 503 with computed backoff (no Retry-After) using an injected deterministic jitter", async () => {
    const sleep = vi.fn().mockResolvedValue(undefined);
    let calls = 0;
    const result = await withRetry(
      async () => {
        calls += 1;
        if (calls < 3) throw { status: 503 };
        return "ok";
      },
      (err: unknown): RetrySignal | undefined => {
        const e = err as { status?: number };
        return e.status !== undefined ? { status: e.status } : undefined;
      },
      true,
      DEFAULT_RETRY_POLICY,
      { sleep, jitterFn: () => 1 },
    );
    expect(result).toBe("ok");
    expect(calls).toBe(3);
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenNthCalledWith(1, 200);
    expect(sleep).toHaveBeenNthCalledWith(2, 400);
  });

  it("never retries a non-idempotent call, regardless of status", async () => {
    let calls = 0;
    await expect(
      withRetry(
        async () => {
          calls += 1;
          throw { status: 503 };
        },
        (): RetrySignal => ({ status: 503 }),
        false, // idempotent=false
        DEFAULT_RETRY_POLICY,
        { sleep: noSleep },
      ),
    ).rejects.toEqual({ status: 503 });
    expect(calls).toBe(1);
  });

  it("never retries a 400 or 401 even when idempotent", async () => {
    for (const status of [400, 401]) {
      let calls = 0;
      await expect(
        withRetry(
          async () => {
            calls += 1;
            throw { status };
          },
          (): RetrySignal => ({ status }),
          true,
          DEFAULT_RETRY_POLICY,
          { sleep: noSleep },
        ),
      ).rejects.toEqual({ status });
      expect(calls).toBe(1);
    }
  });

  it("throws a clear (unwrapped) error once maxAttempts is exhausted", async () => {
    let calls = 0;
    const finalError = { status: 503, message: "still down" };
    await expect(
      withRetry(
        async () => {
          calls += 1;
          throw finalError;
        },
        (): RetrySignal => ({ status: 503 }),
        true,
        { ...DEFAULT_RETRY_POLICY, maxAttempts: 3 },
        { sleep: noSleep, jitterFn: () => 0 },
      ),
    ).rejects.toBe(finalError);
    expect(calls).toBe(3); // exactly maxAttempts, no extra call
  });

  it("stops retrying once the total-time cap is exceeded, even mid-budget", async () => {
    let now = 0;
    let calls = 0;
    const finalError = { status: 503 };
    await expect(
      withRetry(
        async () => {
          calls += 1;
          now += 100; // simulate elapsed time per attempt
          throw finalError;
        },
        (): RetrySignal => ({ status: 503 }),
        true,
        { maxAttempts: 5, baseDelayMs: 10, maxDelayMs: 50, totalTimeCapMs: 150 },
        { sleep: noSleep, jitterFn: () => 0, now: () => now },
      ),
    ).rejects.toBe(finalError);
    // First attempt at now=100 (<150) retries; second attempt pushes now to 200 (>=150) → stop.
    expect(calls).toBe(2);
  });
});
