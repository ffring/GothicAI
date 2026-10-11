import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Tauri serves the dev build from this port (tauri.conf.json devUrl); 8765 is taken on this machine
export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: { port: 47850, strictPort: true },
  build: {
    target: "safari16",
    rollupOptions: { input: { main: "index.html", tray: "tray.html" } },
  },
});
