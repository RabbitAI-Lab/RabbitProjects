import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  // envDir 指向仓库根：三个 app 统一从仓库根加载 .env*（INFRA-001 §4.10）
  envDir: "../..",
  server: {
    port: 3001,
    strictPort: true,
    // 代理：把 /api/v1 → django:8000，/live → express:3000（dev 反代，避免 CORS）
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
        secure: false,
      },
      "/live": {
        target: "http://localhost:3000",
        ws: true,
        changeOrigin: false,
        secure: false,
      },
    },
  },
});
