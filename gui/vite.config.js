// .config/opencode/AGENTS.md APPLIED
// NutrientConflictsEvaluation Project AGENTS.md APPLIED
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api/v1": {
        target: process.env.API_BASE_URL || "http://127.0.0.1:8077",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/v1/, ""),
      },
    },
  },
});
