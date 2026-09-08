/** 会话探针 cookie 单测（CLAUDE.md 已知坑 11 的修复载体）。
 *
 * `sessionid` 是 HttpOnly 恒不可读，SessionStore 用非 HttpOnly 的
 * `rp_session` 1-bit 标记表示有/无会话。本测覆盖三函数的正/反向与
 * SSR 守卫分支（document 未定义时静默不抛）。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { clearSessionProbe, hasSessionProbe, markSessionProbe } from "./session-probe";

afterEach(() => {
  document.cookie = "rp_session=; path=/; max-age=0";
});

describe("markSessionProbe / hasSessionProbe / clearSessionProbe", () => {
  it("标记后探针可读，清除后不可读", () => {
    expect(hasSessionProbe()).toBe(false);
    markSessionProbe();
    expect(hasSessionProbe()).toBe(true);
    clearSessionProbe();
    expect(hasSessionProbe()).toBe(false);
  });

  it("jsdom 下按 http 写出（不带 Secure）——https 分支不误加", () => {
    markSessionProbe();
    expect(document.cookie).toContain("rp_session=1");
    expect(document.cookie).not.toContain("Secure");
  });

  it("对同域其它 cookie 名不误报（前缀/子串边界）", () => {
    document.cookie = "other_rp_session_x=1; path=/";
    expect(hasSessionProbe()).toBe(false);
  });

  it("SSR 守卫：document 未定义时三函数静默不抛", () => {
    const doc = globalThis.document;
    vi.stubGlobal("document", undefined);
    try {
      expect(() => markSessionProbe()).not.toThrow();
      expect(() => clearSessionProbe()).not.toThrow();
      expect(hasSessionProbe()).toBe(false);
    } finally {
      vi.unstubAllGlobals();
      globalThis.document = doc;
    }
  });
});
