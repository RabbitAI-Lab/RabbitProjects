/** 会话探针 cookie —— 取代读不到的 `sessionid` 嗅探。
 *
 *  根因：`sessionid` 是 HttpOnly（Django 默认，`settings` 未覆写），
 *  `document.cookie` **永远读不到它**。所以散落在 `root.tsx` 守卫与
 *  `labels-admin.tsx` 数据加载里的 `/sessionid=/.test(document.cookie)` 恒为 false：
 *    - root.tsx：每次导航都误判「会话已失效」，重置 store 重跑 bootstrap（多一轮 /users/me/）；
 *    - labels-admin.tsx：`load()` 直接 return，标签列表永远空、新建后也不刷新。
 *
 *  解法：由 SessionStore 在「登录成功 / bootstrap 成功」时写一枚**非 HttpOnly**
 *  的 1-bit 标记（仅表示有/无会话，不含任何身份信息），登出或 bootstrap 失败时清除。
 *  e2e 的 `context.clearCookies()` 会连同它一起清掉，守卫语义不变。
 */
const PROBE = "rp_session";

export function markSessionProbe(): void {
  if (typeof document === "undefined") return;
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${PROBE}=1; path=/; max-age=86400; SameSite=Lax${secure}`;
}

export function clearSessionProbe(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${PROBE}=; path=/; max-age=0; SameSite=Lax`;
}

export function hasSessionProbe(): boolean {
  if (typeof document === "undefined") return false;
  return new RegExp(`(?:^|;\\s*)${PROBE}=`).test(document.cookie);
}
