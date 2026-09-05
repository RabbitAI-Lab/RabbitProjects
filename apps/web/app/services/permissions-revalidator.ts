/** AUTH-005 §2.2 / §3.4 权限快照静默重拉回调注册。
 *
 *  解耦设计：单独成模块，避开 services/axios.ts ↔ services/api.ts 的 import 循环
 *  （axios 拦截器需要触发重拉，api.ts 提供 endpoint 包装；中间用这个 callback 桶连接）。
 *  PermissionStore 在创建时注册；axios 拦截器在 PERM_* 403 时调用。 */
let permissionsRevalidator: (() => void) | null = null;

export function setPermissionsRevalidator(fn: (() => void) | null): void {
  permissionsRevalidator = fn;
}

/** 供 axios 拦截器在 PERM_* 403 时调用 —— 静默、不抛、不 toast（AUTH-005 §3.4）。 */
export function triggerPermissionsRevalidate(): void {
  if (permissionsRevalidator) permissionsRevalidator();
}
