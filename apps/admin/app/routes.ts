import { type RouteConfig, index, route } from "@react-router/dev/routes";
export default [
  index("routes/home.tsx"),
  route("governance", "routes/governance.tsx", [
    index("routes/governance.index.tsx"),
    route("risk-events", "routes/governance.risk-events.tsx"),
    route("tenants/:tenantId", "routes/governance.tenants.$tenantId.tsx"),
  ]),
  route("ops", "routes/ops.tsx", [
    index("routes/ops.index.tsx"),
    route("backups", "routes/ops.backups.tsx"),
    route("release", "routes/ops.release.tsx"),
  ]),
] satisfies RouteConfig;
