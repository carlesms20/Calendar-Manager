import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Config de Vite. En dev arranca en localhost:5173 con hot reload y
// proxea las llamadas /api/* al backend Python en localhost:8000.
// En build genera dist/ estático que FastAPI servirá en /app/*.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Base '/app/' porque en produccion la app vive bajo /app en FastAPI.
  base: "/app/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
