import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/status": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/manual_trade": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/position": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
