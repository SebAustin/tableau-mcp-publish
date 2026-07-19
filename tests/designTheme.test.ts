/**
 * Design Excellence, Slice D5 — theme loading + deterministic selection +
 * `design_dashboard` wiring.
 *
 * `vi.mock` wraps the REAL `loadThemes` implementation by default (via
 * `importOriginal`) so every test in this file except the explicit
 * "missing-themes-dir fail-soft" group exercises the real, committed corpus
 * under `design/corpus/themes/` — no fixture corpus, per the task's "use the
 * real committed corpus files" instruction.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { z } from "zod";

vi.mock("../src/design/loadThemes.js", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/design/loadThemes.js")>();
  return { ...actual, loadThemes: vi.fn(actual.loadThemes) };
});

import { loadThemes } from "../src/design/loadThemes.js";
import { selectTheme } from "../src/design/selectTheme.js";
import { toDesignTheme, ThemeFileSchema, type ThemeFile } from "../src/design/schema.js";
import { registerDesignDashboard } from "../src/tools/designDashboard.js";

// ---------------------------------------------------------------------------
// FakeServer / invoke helper — mirrors tests/tools.test.ts's pattern, kept
// self-contained here since this file only needs `design_dashboard`.
// ---------------------------------------------------------------------------

interface RegisteredTool {
  config: { inputSchema?: z.ZodRawShape };
  handler: (args: Record<string, unknown>) => Promise<{ structuredContent?: Record<string, unknown> }>;
}

class FakeServer {
  tools = new Map<string, RegisteredTool>();
  registerTool(name: string, config: RegisteredTool["config"], handler: RegisteredTool["handler"]) {
    this.tools.set(name, { config, handler });
  }
}

function makeServer(): FakeServer {
  const server = new FakeServer();
  registerDesignDashboard(server as never, {} as never);
  return server;
}

async function invoke(server: FakeServer, rawArgs: Record<string, unknown>) {
  const tool = server.tools.get("design_dashboard");
  if (!tool) throw new Error("design_dashboard not registered");
  const parsed = z.object(tool.config.inputSchema ?? {}).parse(rawArgs);
  return tool.handler(parsed as Record<string, unknown>);
}

interface ProposalResult {
  kind: string;
  summary: string;
  plan: {
    audience: string;
    designTheme?: { name: string };
    interactions?: { crossFilter?: boolean; highlight?: boolean };
    sheets: Array<{ kind?: string }>;
  };
}

let tmpDir: string | undefined;

afterEach(() => {
  if (tmpDir) {
    rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = undefined;
  }
});

// ---------------------------------------------------------------------------
// Fixture themes for selectTheme's pure unit tests (fabricated, NOT the real
// corpus — deliberately isolated from disk I/O for these).
// ---------------------------------------------------------------------------

function fixture(name: string, tags: ThemeFile["tags"]): ThemeFile {
  return {
    name,
    tags,
    provenance: [{ source: "fixture.twb", sha256: "0".repeat(64), construct: "unit-test fixture" }],
  };
}

const ALPHA = fixture("alpha_dark", {
  audiences: ["exec"],
  personas: [],
  artifacts: ["dashboard"],
  mood: "dark",
});
const BETA = fixture("beta_dark", {
  audiences: ["exec"],
  personas: ["ceo"],
  artifacts: ["dashboard"],
  mood: "dark",
});
const FALLBACK = fixture("analyst_clean", {
  audiences: ["analyst"],
  personas: [],
  artifacts: ["dashboard"],
  mood: "light",
});

// ---------------------------------------------------------------------------
// selectTheme — determinism / precedence / tie-breaks / fallback
// ---------------------------------------------------------------------------

describe("selectTheme (pure, fabricated fixtures)", () => {
  it("persona-tag match wins over audience-tag match, even against an alphabetically earlier theme", () => {
    const result = selectTheme([ALPHA, BETA], {
      audience: "exec",
      personaName: "ceo",
      artifact: "dashboard",
    });
    expect(result.name).toBe("beta_dark");
  });

  it("persona matching is case-insensitive", () => {
    const result = selectTheme([ALPHA, BETA], {
      audience: "exec",
      personaName: "CEO",
      artifact: "dashboard",
    });
    expect(result.name).toBe("beta_dark");
  });

  it("falls back to audience-tag match, alphabetically-first tie-break, when no persona matches", () => {
    const result = selectTheme([BETA, ALPHA], {
      audience: "exec",
      artifact: "dashboard",
    });
    expect(result.name).toBe("alpha_dark");
  });

  it("falls back to the analyst_clean theme by name when no persona/audience tag matches", () => {
    const result = selectTheme([ALPHA, BETA, FALLBACK], {
      audience: "operational",
      artifact: "dashboard",
    });
    expect(result.name).toBe("analyst_clean");
  });

  it("a persona/audience match requires the artifact tag too — a dashboard-only theme is skipped for artifact='story'", () => {
    const result = selectTheme([BETA], {
      audience: "exec",
      personaName: "ceo",
      artifact: "story", // BETA only declares artifacts: ["dashboard"]
    });
    // No analyst_clean present either -> last-resort alphabetical-first.
    expect(result.name).toBe("beta_dark");
  });

  it("is deterministic regardless of input array order", () => {
    const input = { audience: "exec" as const, artifact: "dashboard" as const };
    const a = selectTheme([ALPHA, BETA], input);
    const b = selectTheme([BETA, ALPHA], input);
    expect(a.name).toBe(b.name);
    expect(a.name).toBe("alpha_dark");
  });

  it("throws a clear error when given an empty theme list", () => {
    expect(() => selectTheme([], { audience: "exec", artifact: "dashboard" })).toThrow(
      /no themes available/i,
    );
  });
});

// ---------------------------------------------------------------------------
// loadThemes — zod validation + string-number coercion (real corpus)
// ---------------------------------------------------------------------------

describe("loadThemes (real committed corpus)", () => {
  it("loads exactly the 4 committed themes, alphabetically by file name", () => {
    const themes = loadThemes();
    expect(themes.map((t) => t.name)).toEqual([
      "analyst_clean",
      "executive_dark",
      "executive_light",
      "operational_plain",
    ]);
  });

  it("every loaded theme zod-validates against ThemeFileSchema", () => {
    for (const theme of loadThemes()) {
      expect(() => ThemeFileSchema.parse(theme)).not.toThrow();
    }
  });

  it("coerces numeric-as-string YAML values (spacing/padding/fontSize/borderWidth) to real numbers", () => {
    const executiveDark = loadThemes().find((t) => t.name === "executive_dark");
    expect(executiveDark).toBeDefined();
    expect(executiveDark?.spacing).toEqual({ outerMargin: 25, gutter: 30 });
    expect(executiveDark?.chartCard?.padding).toBe(25);
    expect(executiveDark?.chartCard?.margin).toBe(0);
    expect(executiveDark?.chartCard?.borderWidth).toBe(0);
    expect(executiveDark?.kpiTile?.padding).toBe(4);
    expect(executiveDark?.chrome?.datalabel?.fontSize).toBe(9);
    for (const value of [
      executiveDark?.spacing?.outerMargin,
      executiveDark?.chartCard?.padding,
      executiveDark?.kpiTile?.padding,
      executiveDark?.chrome?.datalabel?.fontSize,
    ]) {
      expect(typeof value).toBe("number");
    }
  });

  it("throws a clear, actionable error for a nonexistent themes directory", () => {
    expect(() => loadThemes("/nonexistent/design/corpus/themes/xyz")).toThrow(/not found/i);
  });

  it("throws a clear error for a theme file that fails schema validation", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "theme-corpus-test-"));
    writeFileSync(join(tmpDir, "broken.yaml"), "name: broken\n# missing required tags/provenance\n", "utf8");
    expect(() => loadThemes(tmpDir!)).toThrow(/Invalid theme file/i);
  });

  it("throws a clear error for malformed YAML", () => {
    tmpDir = mkdtempSync(join(tmpdir(), "theme-corpus-test-"));
    writeFileSync(join(tmpDir, "broken.yaml"), "name: [unterminated\n  - flow\nbroken: {", "utf8");
    expect(() => loadThemes(tmpDir!)).toThrow(/Malformed YAML/i);
  });
});

// ---------------------------------------------------------------------------
// toDesignTheme — executive_dark.yaml -> wire DesignTheme block, deep-equal
// ---------------------------------------------------------------------------

describe("toDesignTheme (real corpus mapping)", () => {
  it("maps executive_dark.yaml onto the exact wire DesignTheme shape", () => {
    const executiveDark = loadThemes().find((t) => t.name === "executive_dark");
    expect(executiveDark).toBeDefined();
    const wire = toDesignTheme(executiveDark!);

    expect(wire).toEqual({
      name: "executive_dark",
      dashboardBackground: "#2f2e41",
      spacing: { outerMargin: 25, gutter: 30 },
      chartCard: {
        background: "#f5f3f4",
        border: { color: "#000000", style: "none", width: 0 },
        padding: 25,
        margin: 0,
      },
      kpiTile: {
        background: "#2f2e41",
        banColor: "#ffffff",
        padding: 4,
        useSemanticDeltaColors: true,
      },
      header: {
        background: "#2f2e41",
        titleColor: "#ffffff",
        subtitleColor: "#ffffff",
      },
      chrome: {
        hideGridlines: true,
        hideZeroline: true,
        hideAxisTicks: true,
        showMarkLabels: true,
        datalabel: { fontSize: 9, fontWeight: "bold", colorMode: "fixed" },
      },
    });
  });

  it("omits chartCard.border entirely when the theme file sets no border fields", () => {
    const operationalPlain = loadThemes().find((t) => t.name === "operational_plain");
    expect(operationalPlain).toBeDefined();
    const wire = toDesignTheme(operationalPlain!);
    // operational_plain's chartCard DOES set border fields (borderColor etc.)
    // — assert the nesting happened, not that it's absent, since every D0
    // theme actually sets at least borderStyle/borderColor/borderWidth.
    expect(wire.chartCard?.border).toEqual({ color: "#000000", style: "none", width: 0 });
    // ...but operational_plain's chartCard has no `background` set.
    expect(wire.chartCard?.background).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// design_dashboard integration — real corpus, via the actual tool handler
// ---------------------------------------------------------------------------

describe("design_dashboard integration (real corpus)", () => {
  it("persona='ceo' resolves to the executive_dark theme, named in the proposal summary", async () => {
    const server = makeServer();
    const res = await invoke(server, {
      mode: "autonomous",
      persona: "ceo",
      businessQuestion: "How is revenue trending?",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const result = (res.structuredContent as { result: ProposalResult }).result;
    expect(result.kind).toBe("proposal");
    expect(result.plan.designTheme?.name).toBe("executive_dark");
    expect(result.summary).toMatch(/Styled with the "executive_dark" design theme\./);
  });

  it("persona='analyst' resolves to the analyst_clean theme", async () => {
    const server = makeServer();
    const res = await invoke(server, {
      mode: "autonomous",
      persona: "analyst",
      businessQuestion: "Revenue by region",
      fieldHints: [
        { name: "region", dataType: "string" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const result = (res.structuredContent as { result: ProposalResult }).result;
    expect(result.plan.designTheme?.name).toBe("analyst_clean");
    expect(result.summary).toMatch(/Styled with the "analyst_clean" design theme\./);
  });

  it("no persona, audience='operational' resolves to the operational_plain theme", async () => {
    const server = makeServer();
    const res = await invoke(server, {
      mode: "autonomous",
      audience: "operational",
      businessQuestion: "Orders processed today",
      fieldHints: [{ name: "orders", dataType: "number" }],
      datasourceLuid: "DS",
      datasourceName: "Ops",
      projectName: "Ops",
    });
    const result = (res.structuredContent as { result: ProposalResult }).result;
    expect(result.plan.designTheme?.name).toBe("operational_plain");
  });

  it("missing-themes-dir fail-soft: proposal still succeeds, without a designTheme, and warns on stderr", async () => {
    vi.mocked(loadThemes).mockImplementationOnce(() => {
      throw new Error("Design theme directory not found (simulated)");
    });
    const stderrSpy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    try {
      const server = makeServer();
      const res = await invoke(server, {
        mode: "autonomous",
        persona: "ceo",
        businessQuestion: "How is revenue trending?",
        fieldHints: [
          { name: "order_date", dataType: "date" },
          { name: "revenue", dataType: "number" },
        ],
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Sales",
      });
      const result = (res.structuredContent as { result: ProposalResult }).result;
      expect(result.kind).toBe("proposal");
      expect(result.plan.designTheme).toBeUndefined();
      expect(result.summary).not.toMatch(/design theme/);
      expect(stderrSpy).toHaveBeenCalledWith(
        expect.stringMatching(/could not load the design theme corpus/i),
      );
    } finally {
      stderrSpy.mockRestore();
    }
  });

  it("interview mode never touches the theme corpus and still returns questions", async () => {
    const server = makeServer();
    const res = await invoke(server, { mode: "interview" });
    const result = (res.structuredContent as { result: { kind: string } }).result;
    expect(result.kind).toBe("questions");
  });
});

// ---------------------------------------------------------------------------
// Design Excellence, Slice D7 — auto-enable cross-filtering (real corpus)
// ---------------------------------------------------------------------------

describe("design_dashboard — Slice D7 interactions auto-enable (real corpus)", () => {
  it("auto-enables crossFilter when a designTheme is selected AND the plan has >=2 chart sheets", async () => {
    const server = makeServer();
    const res = await invoke(server, {
      mode: "directed",
      audience: "analyst",
      directions: "a bar chart of revenue by region and a trend line of revenue over time",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "revenue", dataType: "number" },
        { name: "region", dataType: "string" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const result = (res.structuredContent as { result: ProposalResult }).result;
    expect(result.kind).toBe("proposal");
    // Precondition: this scenario really does select a theme and produce >=2
    // chart sheets — otherwise the assertion below would be vacuously true.
    expect(result.plan.designTheme?.name).toBe("analyst_clean");
    const chartSheetCount = result.plan.sheets.filter((s) => s.kind !== "kpi_tile").length;
    expect(chartSheetCount).toBeGreaterThanOrEqual(2);

    expect(result.plan.interactions).toEqual({ crossFilter: true });
    expect(result.summary).toMatch(/Cross-filtering enabled\./);
  });

  it("does NOT auto-enable crossFilter when the plan has fewer than 2 chart sheets", async () => {
    const server = makeServer();
    const res = await invoke(server, {
      mode: "autonomous",
      persona: "ceo",
      businessQuestion: "How is revenue trending?",
      fieldHints: [
        { name: "order_date", dataType: "date" },
        { name: "revenue", dataType: "number" },
      ],
      datasourceLuid: "DS",
      datasourceName: "Sales",
      projectName: "Sales",
    });
    const result = (res.structuredContent as { result: ProposalResult }).result;
    // Precondition: this scenario selects a theme (executive_dark) but only
    // produces 1 chart sheet (a KPI band with a single trend chart).
    expect(result.plan.designTheme?.name).toBe("executive_dark");
    const chartSheetCount = result.plan.sheets.filter((s) => s.kind !== "kpi_tile").length;
    expect(chartSheetCount).toBeLessThan(2);

    expect(result.plan.interactions).toBeUndefined();
    expect(result.summary).not.toMatch(/Cross-filtering enabled/);
  });

  it("does NOT auto-enable crossFilter when no designTheme was selected (fail-soft corpus)", async () => {
    vi.mocked(loadThemes).mockImplementationOnce(() => {
      throw new Error("Design theme directory not found (simulated)");
    });
    const stderrSpy = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    try {
      const server = makeServer();
      const res = await invoke(server, {
        mode: "directed",
        audience: "analyst",
        directions: "a bar chart of revenue by region and a trend line of revenue over time",
        fieldHints: [
          { name: "order_date", dataType: "date" },
          { name: "revenue", dataType: "number" },
          { name: "region", dataType: "string" },
        ],
        datasourceLuid: "DS",
        datasourceName: "Sales",
        projectName: "Sales",
      });
      const result = (res.structuredContent as { result: ProposalResult }).result;
      expect(result.plan.designTheme).toBeUndefined();
      const chartSheetCount = result.plan.sheets.filter((s) => s.kind !== "kpi_tile").length;
      expect(chartSheetCount).toBeGreaterThanOrEqual(2);

      expect(result.plan.interactions).toBeUndefined();
      expect(result.summary).not.toMatch(/Cross-filtering enabled/);
    } finally {
      stderrSpy.mockRestore();
    }
  });
});
