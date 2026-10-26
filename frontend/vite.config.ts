/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = process.env.WADIMA_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      // secure: false admite el certificado de la autoridad local (python manage.py tls) en el proxy local.
      "/api": { target: API, secure: false },
      "/ws": { target: API.replace(/^http/, "ws"), ws: true, secure: false },
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 1800,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes("node_modules/phaser")) return "phaser";
          if (id.includes("node_modules/@codemirror") || id.includes("node_modules/@lezer")) return "codemirror";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
