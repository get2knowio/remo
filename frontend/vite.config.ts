import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    // Never inline fonts as `data:` URLs. The web service ships a strict CSP
    // (`default-src 'self'`, no `data:`), so every font must load as a
    // same-origin file. Vite's default inlines assets under 4KB, which turned
    // small @fontsource woff2 subsets into `data:font/woff2` @font-face srcs
    // the browser then refused. Non-font assets keep the default size-based
    // behavior (return undefined -> fall back to Vite's default logic).
    assetsInlineLimit: (filePath) =>
      /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
  },
});
