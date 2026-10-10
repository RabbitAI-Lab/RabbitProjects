import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// 代理目标可由环境变量覆盖（默认不变）——COLLAB-004 e2e 用独立副栈
// （api:8001 + live:3000 + web:3002）时注入 API_PROXY_TARGET；用户 dev 零影响。
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";
const LIVE_PROXY_TARGET = process.env.LIVE_PROXY_TARGET ?? "http://localhost:3000";
// /uploads 直传反代目标可覆盖——错峰端口副栈（rp-minio 不在 9000 时注入
// UPLOADS_PROXY_TARGET；SigV4 按 AWS_S3_ENDPOINT_URL 的 host 签名，代理目标必须一致）
const UPLOADS_PROXY_TARGET = process.env.UPLOADS_PROXY_TARGET ?? "http://localhost:9000";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  // 全量依赖预打包 pin——消灭「套件中途重新预打包」一类（20261010 每夜定位，
  // CI e2e 13 红根因）：冷缓存下，路由懒加载页首次访问才发现新依赖 → vite 中途
  // 重新预打包、browserHash 跳变 → 已加载页面拿到新旧两份 React（invalid hook
  // call 崩进 ErrorBoundary）+ 旧哈希依赖请求吃 504 Outdated Optimize Dep。
  // 实证口径：冷缓存全量 e2e 跑完后 _metadata.json 的 optimized 全集
  // （round-1 10 个 + 中途发现 4 个）。纪律：任何路由新增 bare import 后
  // 必须同步补进这份清单（清单缺项 = 该页面首次访问触发一轮重预打包）。
  optimizeDeps: {
    include: [
      "react",
      "react/jsx-runtime",
      "react/jsx-dev-runtime",
      "react-dom",
      "react-dom/client",
      "react-router",
      "react-router/dom",
      "axios",
      "mobx",
      "mobx-react-lite",
      "@tanstack/react-virtual",
      "@xyflow/react",
      "dagre",
      "html-to-image",
    ],
  },
  // envDir 指向仓库根：三个 app 统一从仓库根加载 .env*（INFRA-001 §4.10）
  envDir: "../..",
  server: {
    port: 3001,
    strictPort: true,
    // 代理：把 /api/v1 → django:8000，/live → express:3000（dev 反代，避免 CORS）
    // 注意不要给 /api 代理设 `agent: { keepAlive: false }`：每请求新 TCP → Django
    // 每请求新线程 + CONN_MAX_AGE=60 的 PG 连接，配合项目列表页对每张卡片拉
    // members 的放大效应，一次全量 e2e 就能把 PG max_connections(100) 打满
    // （实测 too many clients → 全线 500）。默认 keep-alive 复用少数连接反而安全。
    proxy: {
      "/api": {
        target: API_PROXY_TARGET,
        changeOrigin: false,
        secure: false,
      },
      // COLLAB-004 BR-10 健康探测：express 的 /health 挂在根路径，/live 前缀不改写
      // 会 404——dev 代理补一条改写（生产网关同理需在 nginx 侧改写或设 VITE_LIVE_HEALTH_URL）。
      "/live/health": {
        target: LIVE_PROXY_TARGET,
        changeOrigin: false,
        secure: false,
        rewrite: (p) => p.replace(/^\/live/, ""),
      },
      "/live": {
        target: LIVE_PROXY_TARGET,
        ws: true,
        changeOrigin: false,
        secure: false,
      },
      // FILE-001 §4.7：附件/头像直传走同源 /uploads/ 前缀（presign 返回的就是这个形态），
      // 由网关反代到 MinIO，浏览器零跨域。缺这条 dev 代理 → PUT /uploads/... 落到 Vite 自身
      // 返回 404/首页 HTML，表现为「上传按钮点了没反应」。
      "/uploads": {
        target: UPLOADS_PROXY_TARGET,
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
