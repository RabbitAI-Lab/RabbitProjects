import { type RouteConfig, index, route } from "@react-router/dev/routes";
export default [
  index("routes/home.tsx"),
  route("ops", "routes/ops.tsx", [
    index("routes/ops.index.tsx"),
    route("backups", "routes/ops.backups.tsx"),
    route("release", "routes/ops.release.tsx"),
  ]),
] satisfies RouteConfig;
