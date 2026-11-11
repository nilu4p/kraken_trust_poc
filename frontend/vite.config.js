import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      // Knowledge base: served by the docs container behind the console (make up).
      "/docs": "http://127.0.0.1:8080",
    },
  },
  build: {
    sourcemap: false,
    rollupOptions: {
      output: {
        // Libraries change rarely: separate long-cached chunks (an app update re-downloads only app code).
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          if (/node_modules\/(leaflet|react-leaflet|@react-leaflet)\//.test(id)) return "map";
          if (id.includes("node_modules/lucide-react/")) return "icons";
          return "vendor";
        },
      },
    },
  },
});
