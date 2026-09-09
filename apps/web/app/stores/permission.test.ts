/** PermissionStore 单测（AUTH-005 §4.5.1 can() + S8 A#5 并集分支，Sprint-9）。
 *
 *  覆盖：fail-closed（快照 null 恒 false）/ 系统管理员短路 / 角色阈值比较 /
 *  项目隐式提升（WS_ADMIN+ → PROJ_ADMIN）/ **并集分支**（threshold 未过但码
 *  在 custom_codes 内）/ 快照缺 custom_codes 的零差异兼容。 */
import { describe, expect, it } from "vitest";
import type { PermissionSnapshot } from "../services/api";
import { PermissionStore } from "./permission";
import type { RootStore } from "./index";

function makeStore(_snapshot: PermissionSnapshot | null): PermissionStore {
  // root 仅承载引用（can() 不触达其它 store 面）；测试聚焦判定逻辑本身
  return new PermissionStore({ session: null } as unknown as RootStore);
}

function withProjects(projects: PermissionSnapshot["projects"]): PermissionStore {
  const store = makeStore(null);
  store.hydrate({
    is_system_admin: false,
    workspaces: { "ws-1": { slug: "acme", role: 10 } },
    projects,
    meta: { generated_at: "2026-09-10T00:00:00Z", truncated: false },
  });
  return store;
}

describe("fail-closed 与短路（AUTH-005 BR-09/10）", () => {
  it("快照 null → can() 恒 false", () => {
    const s = makeStore(null);
    expect(s.can("project.read", "project", "p1")).toBe(false);
    expect(s.can("workspace.member.read", "workspace", "ws-1")).toBe(false);
  });
  it("is_system_admin → 恒 true", () => {
    const s = makeStore(null);
    s.hydrate({ is_system_admin: true, workspaces: {}, projects: {} });
    expect(s.can("project.delete", "project", "any")).toBe(true);
  });
});

describe("阈值与隐式提升（rbac §7.4）", () => {
  it("显式项目角色过阈 → true；不足 → false", () => {
    const s = withProjects({ p1: { workspace_id: "ws-1", role: 20, inherited: false } });
    expect(s.can("project.setting.manage", "project", "p1")).toBe(true);   // ADMIN ≥ 20
    const s2 = withProjects({ p1: { workspace_id: "ws-1", role: 10, inherited: false } });
    expect(s2.can("project.setting.manage", "project", "p1")).toBe(false); // VIEWER < 20
  });
  it("工作空间角色档（workspace scope）", () => {
    const s = withProjects({});
    expect(s.can("workspace.member.read", "workspace", "ws-1")).toBe(true);  // MEMBER ≥ 10
    expect(s.can("workspace.setting.manage", "workspace", "ws-1")).toBe(false);
  });
});

describe("并集分支（S8 A#5，Sprint-9）", () => {
  it("threshold 未过但码在 custom_codes → true", () => {
    const s = withProjects({
      p1: { workspace_id: "ws-1", role: 10, inherited: false, custom_codes: ["report.export"] },
    });
    // report.export 阈值 ADMIN(20)；角色 10 不足，但并集含该码 → 放行
    expect(s.can("report.export", "project", "p1")).toBe(true);
    // 并集只加不减：其它码不连带放行
    expect(s.can("project.delete", "project", "p1")).toBe(false);
  });
  it("快照缺 custom_codes（旧载荷）→ 零差异（恒 false 走原路径）", () => {
    const s = withProjects({ p1: { workspace_id: "ws-1", role: 10, inherited: false } });
    expect(s.can("report.export", "project", "p1")).toBe(false);
  });
  it("并集不作用于 workspace scope", () => {
    const s = withProjects({
      p1: { workspace_id: "ws-1", role: 10, inherited: false, custom_codes: ["workspace.setting.manage"] },
    });
    expect(s.can("workspace.setting.manage", "workspace", "ws-1")).toBe(false);
  });
});
