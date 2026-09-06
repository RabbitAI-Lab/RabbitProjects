import { type RouteConfig, index, route } from "@react-router/dev/routes";
export default [
  index("routes/home.tsx"),
  // FILE-004 §3.3 匿名访问页（/s/{slug}：密码门 / 文件页 / 失效页 三态）
  route("s/:slug", "routes/share.tsx"),
] satisfies RouteConfig;
