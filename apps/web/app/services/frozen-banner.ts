/** 租户冻结横幅状态通道（AUTH-012 §3.4 / BR-09，P4 R1）。
 *
 * 数据源：治理中间件在冻结租户的响应头携带 X-Tenant-Frozen: 1（读请求
 * 放行但带标记，写请求已 409 短路）；axios 响应拦截器写入本模块，
 * FrozenBanner 订阅渲染。进程内单例——任何一次不带标记的响应即复位。 */
let frozen = false;
const listeners = new Set<(v: boolean) => void>();

export function setTenantFrozen(v: boolean): void {
  if (frozen === v) return;
  frozen = v;
  listeners.forEach((l) => l(v));
}

export function subscribeFrozenBanner(l: (v: boolean) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
