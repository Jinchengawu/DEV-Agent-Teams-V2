import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/antd") || id.includes("node_modules/@ant-design") || id.includes("node_modules/@rc-component")) return "ui-vendor";
          if (id.includes("node_modules/react") || id.includes("node_modules/scheduler")) return "react-vendor";
          if (id.includes("node_modules/@xyflow") || id.includes("node_modules/d3-")) return "graph-vendor";
          if (id.includes("node_modules/@tanstack") || id.includes("node_modules/@dnd-kit") || id.includes("node_modules/zod")) return "data-vendor";
          return undefined;
        },
      },
    },
  },
  server: { port: 5173, proxy: { "/v1": "http://127.0.0.1:8080" } },
  test: { setupFiles: ["./src/test/setup.ts"] },
});
