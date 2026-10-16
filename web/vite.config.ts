import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Built into the Python package, which serves it to the app's windows. In development, `npm run dev` serves it with
// hot reload and passes /api to the running app (DOWSE_API, e.g. http://127.0.0.1:52341).
export default defineConfig({
  plugins: [svelte()],
  base: "./",
  build: { outDir: "../src/dowse/desktop/static", emptyOutDir: true, target: "es2022", chunkSizeWarningLimit: 900 },
  server: {
    host: "127.0.0.1",
    proxy: process.env.DOWSE_API ? { "/api": { target: process.env.DOWSE_API, changeOrigin: true } } : undefined,
  },
});
