// ============================================================================
// File:         vite.config.ts
// Written by:   BV (Blas Vargas)
// What it does: Build and development server settings.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  Vite; Vite React plugin; Tailwind CSS (Vite plugin)
// ============================================================================
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: "127.0.0.1", port: 8443, proxy: { "/api": { target: "http://127.0.0.1:5000", changeOrigin: false } } },
});
