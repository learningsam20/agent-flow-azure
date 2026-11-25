import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.WEBAPP_FRONTEND_PORT || 5748),
    proxy: {
      "/api": {
        target: `http://localhost:${process.env.WEBAPP_BACKEND_PORT || 5749}`,
        changeOrigin: true,
      },
      "/health": {
        target: `http://localhost:${process.env.WEBAPP_BACKEND_PORT || 5749}`,
        changeOrigin: true,
      },
    },
  },
});
