import { describe, it, expect } from "vitest";
import { createServer } from "node:net";
import { getFreePort } from "../src/sidecar.js";
import { loadConfig } from "../src/config.js";

// ---------------------------------------------------------------------------
// getFreePort
// ---------------------------------------------------------------------------

describe("getFreePort", () => {
  it("resolves a positive integer port", async () => {
    const port = await getFreePort("127.0.0.1");
    expect(port).toBeGreaterThan(0);
    expect(Number.isInteger(port)).toBe(true);
  });

  it("returns a port that is actually bindable", async () => {
    const port = await getFreePort("127.0.0.1");

    // We should be able to bind a server to this port right away.
    await new Promise<void>((resolve, reject) => {
      const server = createServer();
      server.listen({ host: "127.0.0.1", port }, () => {
        server.close((err) => (err ? reject(err) : resolve()));
      });
      server.on("error", reject);
    });
  });

  it("two sequential calls each return a usable port", async () => {
    const p1 = await getFreePort("127.0.0.1");
    const p2 = await getFreePort("127.0.0.1");

    // Both must be positive integers.
    expect(p1).toBeGreaterThan(0);
    expect(p2).toBeGreaterThan(0);

    // Both must be bindable independently.
    const bind = (port: number) =>
      new Promise<void>((resolve, reject) => {
        const server = createServer();
        server.listen({ host: "127.0.0.1", port }, () => {
          server.close((err) => (err ? reject(err) : resolve()));
        });
        server.on("error", reject);
      });

    await expect(bind(p1)).resolves.toBeUndefined();
    await expect(bind(p2)).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// loadConfig — sidecarPort optional / present
// ---------------------------------------------------------------------------

describe("loadConfig sidecarPort", () => {
  const base = {
    SERVER: "https://x.online.tableau.com",
    SITE_NAME: "s",
    PAT_NAME: "p",
    PAT_VALUE: "secret-value",
  };

  it("leaves sidecarPort undefined when SIDECAR_PORT is absent", () => {
    const config = loadConfig(base as NodeJS.ProcessEnv);
    expect(config.sidecarPort).toBeUndefined();
  });

  it("parses sidecarPort when SIDECAR_PORT is present", () => {
    const config = loadConfig({ ...base, SIDECAR_PORT: "9123" } as NodeJS.ProcessEnv);
    expect(config.sidecarPort).toBe(9123);
  });

  it("rejects a non-numeric SIDECAR_PORT", () => {
    expect(() =>
      loadConfig({ ...base, SIDECAR_PORT: "not-a-number" } as NodeJS.ProcessEnv),
    ).toThrow(/Invalid configuration/i);
  });

  it("rejects a zero SIDECAR_PORT", () => {
    expect(() =>
      loadConfig({ ...base, SIDECAR_PORT: "0" } as NodeJS.ProcessEnv),
    ).toThrow(/Invalid configuration/i);
  });
});
