import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  // envDir 指向仓库根：三个 app 统一从仓库根加载 .env*（INFRA-001 §4.10）
  envDir: "../..",
  server: {
    port: 3002,
    // 端口被占用时直接报错退出（E2E-06 可诊断性要求）
    strictPort: true,
    // 运维台 API 同源代理（web 域同款）：会话 cookie 落 3002 域，免跨域
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: false, // web 域同款：Host 透传保 CSRF Origin 校验一致
      },
    },
  },
});
