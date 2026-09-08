/** 权限快照静默重拉回调桶单测（AUTH-005 §2.2 / §3.4）。
 *
 * axios 拦截器在 PERM_* 403 时经 triggerPermissionsRevalidate() 触发
 * PermissionStore 的静默重拉；模块是防 import 循环的 callback 桶。
 * 覆盖：注册后触发回调、未注册时静默不抛、置 null 摘除。
 */
import { describe, expect, it, vi } from "vitest";

import { setPermissionsRevalidator, triggerPermissionsRevalidate } from "./permissions-revalidator";

describe("setPermissionsRevalidator / triggerPermissionsRevalidate", () => {
  it("注册后触发回调（静默语义：异常也不外抛由回调方自保证）", () => {
    const fn = vi.fn();
    setPermissionsRevalidator(fn);
    triggerPermissionsRevalidate();
    expect(fn).toHaveBeenCalledOnce();
    setPermissionsRevalidator(null);
  });

  it("未注册 / 已摘除时触发是 no-op，不抛", () => {
    setPermissionsRevalidator(null);
    expect(() => triggerPermissionsRevalidate()).not.toThrow();
  });

  it("重复注册以后注册者为准", () => {
    const first = vi.fn();
    const second = vi.fn();
    setPermissionsRevalidator(first);
    setPermissionsRevalidator(second);
    triggerPermissionsRevalidate();
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    setPermissionsRevalidator(null);
  });
});
