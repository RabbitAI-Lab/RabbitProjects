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
      // FILE-001 §4.7：附件/头像直传走同源 /uploads/ 前缀（presign 返回的就是这个形态），
      // 由网关反代到 MinIO，浏览器零跨域。缺这条 dev 代理 → PUT /uploads/... 落到 Vite 自身
      // 返回 404/首页 HTML，表现为「上传按钮点了没反应」。
      "/uploads": {
        target: "http://localhost:9000",
        // 必须 changeOrigin：S3 SigV4 把 host 纳入签名，presign 是按
        // AWS_S3_ENDPOINT_URL(=http://localhost:9000) 签的，透传浏览器侧的
        // Host: localhost:3001 会让 MinIO 判签名不匹配 → 403。
        changeOrigin: true,
        secure: false,
        rewrite: (p) => p.replace(/^\/uploads/, ""),
      },
    },
  },
});
