import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_PRAXILE_API_BASE || "http://127.0.0.1:8765",
        changeOrigin: true
      }
    }
  }
});
