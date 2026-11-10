import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";
import { execFileSync } from "child_process";
import fs from "fs";

// The dev server's /api proxy goes to this machine's Flask: the deployment's ports.flask (smconfig; SM_DEPLOYMENT,
// deployments/README.md). VITE_API_URL still overrides it; if smconfig can't answer, Flask's usual 5000.
function flaskUrl(): string {
  const py = fs.existsSync(path.resolve(__dirname, ".venv/bin/python")) ? path.resolve(__dirname, ".venv/bin/python") : "python3";
  try {
    const port = execFileSync(py, ["-m", "smconfig", "get", "ports.flask"], {
      cwd: __dirname, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 10000,
    }).trim();
    if (/^\d+$/.test(port)) return `http://localhost:${port}`;
  } catch { /* fall through */ }
  return "http://localhost:5000";
}

// https://vitejs.dev/config/
export default defineConfig(({ mode, command }) => {
  const apiTarget = process.env.VITE_API_URL || (command === "serve" ? flaskUrl() : "http://localhost:5000");
  return {
  base: '/',
  
  server: {
    host: "::",
    port: 8080,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        secure: false,
      },
      '/app': {
        target: apiTarget,
        changeOrigin: true,
        secure: false,
      }
    }
  },
  plugins: [
    react(),
    mode === 'development' &&
    componentTagger(),
  ].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
};
});
