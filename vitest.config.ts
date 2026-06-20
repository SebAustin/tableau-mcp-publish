import { defineConfig } from "vitest/config";

export default defineConfig({
  // Source uses NodeNext-style ".js" import specifiers that point at ".ts" files.
  // Map ".js" -> ".ts" so Vite/vitest resolves them during tests.
  resolve: {
    extensionAlias: { ".js": [".ts", ".js"] },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
