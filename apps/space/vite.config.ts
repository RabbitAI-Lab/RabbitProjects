import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// 代理目标可由环境变量覆盖（与 apps/web 同范式；默认本地 Django:8000 / MinIO:9000）
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  // envDir 指向仓库根：三个 app 统一从仓库根加载 .env*（INFRA-001 §4.10）
  envDir: "../..",
  server: {
    port: 3003,
    // 端口被占用时直接报错退出（E2E-06 可诊断性要求）
    strictPort: true,
    proxy: {
      // FILE-004 §3.3 匿名三态页消费公开 API 分组（/api/v1/public/shares/…）——
      // dev 反代到 Django（生产由网关承载）；keep-alive 默认值与 web 同口径。
      "/api": {
        target: API_PROXY_TARGET,
        changeOrigin: false,
        secure: false,
      },
      // 匿名预览/下载 302 目标是 /uploads/… 预签名（服务端已改写前缀）——
      // SigV4 纳 host 签名，必须 changeOrigin（CLAUDE.md 坑 13 同款）。
      "/uploads": {
        target: "http://localhost:9000",
        changeOrigin: true,
        secure: false,
        rewrite: (p) => p.replace(/^\/uploads/, ""),
      },
    },
  },
});
