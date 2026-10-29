import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    assetsInlineLimit: 100000000,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
  },
});
