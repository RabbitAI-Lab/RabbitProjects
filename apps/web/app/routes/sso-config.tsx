/** SSO 配置页（AUTH-009 §3.1——C.155，Sprint-8 R6）。
 *
 *  WS_OWNER 面：协议选择（OIDC/SAML）→ 元数据表单 → 测试连接干跑（BR-04
 *  硬门槛——通过才可启用）→ 启用/强制 SSO（BR-05 防自锁前置）→ 绑定成员
 *  清单。密钥永不回显（secret_set，BR-14）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { unwrap } from "../services/api";
import { api } from "../services/axios";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { toast } from "../components/Toast";

type Config = Record<string, unknown> & {
  protocol: "oidc" | "saml"; is_enabled: boolean; enforce_sso: boolean;
  issuer?: string; client_id?: string; secret_set?: boolean;
  idp_entity_id?: string; idp_sso_url?: string; idp_x509_cert?: string;
  last_test_passed_at?: string | null; cert_expires_in_days?: number | null;
};

const SSOG = {
  get: (slug: string) => api.get<Config>(`workspaces/${slug}/sso/`),
  patch: (slug: string, body: Record<string, unknown>) =>
    api.patch<Config>(`workspaces/${slug}/sso/`, body),
  check: (slug: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`workspaces/${slug}/sso/connection-check/`, body),
  enforceOn: (slug: string) => api.post(`workspaces/${slug}/sso/enforce/`),
  enforceOff: (slug: string) => api.delete(`workspaces/${slug}/sso/enforce/`),
  bindings: (slug: string) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/sso/bindings/`),
};

export default function SSOConfigPage() {
  const { workspaceSlug: ws } = useParams();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [bindings, setBindings] = useState<Array<Record<string, unknown>>>([]);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clientSecret, setClientSecret] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const [c, b] = await Promise.all([
        SSOG.get(ws), SSOG.bindings(ws).catch(() => null),
      ]);
      setCfg(unwrap<Config>(c as unknown) ?? null);
      setBindings(unwrap<Array<Record<string, unknown>>>(b as unknown) ?? []);
      setLoadErr(null);
    } catch (e) {
      setLoadErr((e as { message?: string })?.message ?? "加载失败（需工作空间所有者）");
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);

  async function save(fields: Record<string, unknown>) {
    if (!ws) return;
    setSaving(true);
    try {
      const r = await SSOG.patch(ws, fields);
      setCfg(unwrap<Config>(r as unknown) ?? cfg);
      toast("配置已保存");
    } catch (e) {
      toast((e as { message?: string })?.message ?? "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function runCheck() {
    if (!ws) return;
    setChecking(true);
    try {
      const r = await SSOG.check(ws, clientSecret ? { client_secret: clientSecret } : {});
      const res = unwrap<Record<string, unknown>>(r as unknown) ?? {};
      if (res.ok) {
        toast(`测试连接通过（issuer 匹配，验签 OK）`);
        void load();
      } else {
        toast(`测试连接失败：${String(res.error ?? "未知错误")}`, "error");
      }
    } catch (e) {
      toast((e as { message?: string })?.message ?? "干跑失败", "error");
    } finally {
      setChecking(false);
    }
  }

  if (loadErr) {
    return (
      <div className="flex h-screen flex-col">
        <Topbar />
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          {loadErr}
        </div>
      </div>
    );
  }
  if (!cfg) return <div className="p-10 text-sm text-neutral-400">加载 SSO 配置…</div>;

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-5" data-sb-scope="sso-config">
          <div className="mb-[18px]">
            <div className="text-[17px] font-semibold">SSO 单点登录</div>
            <div className="text-[12.5px] text-neutral-400">OIDC / SAML 2.0 · JIT 开通 · 强制与逃生（仅空间所有者可见此页）</div>
          </div>

          {/* 段一：协议与元数据（冻结稿 O4 编号圆标） */}
          <section className="mb-4 rounded-lg border bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center gap-2.5 text-[13.5px] font-semibold">
              <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-neutral-300 text-xs font-semibold text-neutral-600">1</span>
              协议与元数据
              <span className="ml-auto text-xs font-normal text-neutral-400">{cfg.is_enabled ? "启用中 · 不可切换协议（先停用）" : ""}</span>
            </div>
            <div className="mb-3.5 flex gap-2.5">
              {(["oidc", "saml"] as const).map((p) => (
                <button key={p} onClick={() => save({ protocol: p })}
                        disabled={saving || cfg.is_enabled}
                        className={`proto-pill ${cfg.protocol === p ? "on" : ""} disabled:opacity-50`}
                        data-sb-scope="sso-protocol">
                  {p === "oidc" ? "OIDC" : "SAML 2.0"}
                </button>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              {cfg.protocol === "oidc" ? (
                <>
                  <label className="text-xs text-neutral-500">Issuer
                    <input defaultValue={cfg.issuer ?? ""} key={cfg.issuer ?? "i"} onBlur={(e) => e.target.value !== cfg.issuer && save({ issuer: e.target.value })}
                           placeholder="https://sso.company.com/realms/main"
                           className="mt-1 w-full rounded border px-2 py-1.5 text-sm" data-sb-scope="sso-issuer" /></label>
                  <label className="text-xs text-neutral-500">Client ID
                    <input defaultValue={cfg.client_id ?? ""} key={cfg.client_id ?? "c"} onBlur={(e) => e.target.value !== cfg.client_id && save({ client_id: e.target.value })}
                           className="mt-1 w-full rounded border px-2 py-1.5 text-sm" data-sb-scope="sso-client-id" /></label>
                  <label className="text-xs text-neutral-500">Client Secret（{cfg.secret_set ? "已设置，输入以轮换" : "未设置"}）
                    <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)}
                           placeholder="••••••（保存后永不回显）"
                           className="mt-1 w-full rounded border px-2 py-1.5 text-sm" data-sb-scope="sso-secret" /></label>
                </>
              ) : (
                <>
                  <label className="text-xs text-neutral-500">IdP Entity ID
                    <input defaultValue={cfg.idp_entity_id ?? ""} key={cfg.idp_entity_id ?? "e"} onBlur={(e) => e.target.value !== cfg.idp_entity_id && save({ idp_entity_id: e.target.value })}
                           className="mt-1 w-full rounded border px-2 py-1.5 text-sm" /></label>
                  <label className="text-xs text-neutral-500">IdP SSO URL
                    <input defaultValue={cfg.idp_sso_url ?? ""} key={cfg.idp_sso_url ?? "u"} onBlur={(e) => e.target.value !== cfg.idp_sso_url && save({ idp_sso_url: e.target.value })}
                           className="mt-1 w-full rounded border px-2 py-1.5 text-sm" /></label>
                  <label className="text-xs text-neutral-500">IdP X509 证书（PEM）
                    <textarea defaultValue={cfg.idp_x509_cert ?? ""} key={(cfg.idp_x509_cert ?? "").slice(-20)} rows={4}
                              onBlur={(e) => e.target.value !== cfg.idp_x509_cert && save({ idp_x509_cert: e.target.value })}
                              className="mt-1 w-full rounded border px-2 py-1.5 font-mono text-xs"
                              placeholder={"-----BEGIN CERTIFICATE-----\n…（" + (cfg.cert_expires_in_days != null ? `剩 ${cfg.cert_expires_in_days} 天` : "未设置") + "）"} /></label>
                </>
              )}
            </div>
            {clientSecret && (
              <button onClick={() => save({ client_secret: clientSecret }).then(() => setClientSecret(""))}
                      className="mt-2 rounded bg-neutral-800 px-3 py-1.5 text-xs text-white">保存密钥</button>
            )}
          </section>

          {/* 段二：干跑硬门槛（BR-04） */}
          <section className="mb-4 rounded-lg border bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center gap-2.5 text-[13.5px] font-semibold">
              <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-neutral-300 text-xs font-semibold text-neutral-600">2</span>
              测试连接（干跑）与启用
              <span className="ml-auto">{cfg.last_test_passed_at
                ? <span className="pass-badge ok">✓ {new Date(cfg.last_test_passed_at).toLocaleString("zh-CN", { hour12: false }).slice(0, 16)} 通过 · 验签 OK</span>
                : <span className="pass-badge warn">⚠ 未通过——启用前必须干跑</span>}</span>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <button onClick={runCheck} disabled={checking}
                      className="rounded border px-3 py-1.5 text-sm disabled:opacity-50" data-sb-scope="sso-check">
                {checking ? "干跑中…" : "测试连接"}
              </button>
              <span className={cfg.last_test_passed_at ? "text-emerald-600" : "text-amber-600"}>
                {cfg.last_test_passed_at
                  ? `✓ 上次通过 ${new Date(cfg.last_test_passed_at).toLocaleString("zh-CN")}`
                  : "⚠ 未通过干跑——启用前必须通过（BR-04）"}
              </span>
              <label className="ml-auto flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.is_enabled}
                       disabled={!cfg.is_enabled && !cfg.last_test_passed_at}
                       onChange={(e) => save({ is_enabled: e.target.checked })}
                       data-sb-scope="sso-enabled" />
                启用 SSO 登录入口
              </label>
            </div>
          </section>

          {/* 段三：强制 SSO（红警示条 O4） */}
          <section className="mb-4 rounded-lg border bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center gap-2.5 text-[13.5px] font-semibold">
              <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-neutral-300 text-xs font-semibold text-neutral-600">3</span>
              强制 SSO
              <span className="ml-auto text-xs font-normal text-neutral-400">前置：≥1 名所有者已通过 SSO 登录绑定（防自锁）</span>
            </div>
            {cfg.enforce_sso && (
              <div className="mb-2.5 flex items-center gap-2.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-600">
                🔒 全员仅可 SSO 登录——逃生名单账号走环境变量 SSO_BREAK_GLASS_EMAILS（不入库不可改）
              </div>
            )}
            <div className="flex items-center gap-3 text-xs text-neutral-600">
              <button onClick={async () => { try { await SSOG.enforceOn(ws!); toast("已开启强制 SSO"); void load(); } catch (e) { toast((e as { message?: string })?.message ?? "开启失败（需 OWNER 先 SSO 登录一次）", "error"); } }}
                      disabled={cfg.enforce_sso}
                      className="rounded bg-red-600 px-3 py-1.5 text-xs text-white disabled:opacity-40" data-sb-scope="sso-enforce-on">
                开启强制
              </button>
              <button onClick={async () => { try { await SSOG.enforceOff(ws!); toast("已关闭强制 SSO"); void load(); } catch { toast("关闭失败", "error"); } }}
                      disabled={!cfg.enforce_sso}
                      className="rounded border px-3 py-1.5 text-xs disabled:opacity-40" data-sb-scope="sso-enforce-off">
                关闭
              </button>
              {cfg.enforce_sso && <span className="text-red-600">🔒 全员仅 SSO 登录（逃生名单走环境变量 SSO_BREAK_GLASS_EMAILS）</span>}
              <span>前置：≥1 名所有者已通过 SSO 登录绑定（BR-05 防自锁）</span>
            </div>
          </section>

          {/* 段四：绑定清单 + 证书临期（O4） */}
          <section className="rounded-lg border bg-white p-4 shadow-sm">
            <div className="mb-2.5 flex items-center gap-2.5 text-[13.5px] font-semibold">
              <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-neutral-300 text-xs font-semibold text-neutral-600">4</span>
              已绑定成员（{bindings.length}）
              <span className="ml-auto text-xs font-normal text-neutral-400">IdP 侧禁用用户：会话最长 14 天滑动窗口（即时禁用待 AUTH-011 / P4）</span>
            </div>
            {cfg.cert_expires_in_days != null && cfg.cert_expires_in_days < 14 && (
              <div className="mb-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                ⏳ IdP 证书剩余 <b>{cfg.cert_expires_in_days} 天</b>——即将过期，请安排轮换（AUTH-009 §2.6）
              </div>
            )}
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-neutral-400">
                <tr><th className="py-1">邮箱（绑定时）</th><th>subject</th><th>绑定时间</th></tr>
              </thead>
              <tbody>
                {bindings.map((b) => (
                  <tr key={String(b.id)} className="border-t">
                    <td className="py-1.5">{String(b.email)}</td>
                    <td className="font-mono text-xs text-neutral-500">{String(b.subject).slice(0, 16)}…</td>
                    <td className="text-xs text-neutral-400">{new Date(String(b.created_at)).toLocaleDateString("zh-CN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!bindings.length && <div className="py-6 text-center text-xs text-neutral-400">尚无绑定——成员经 IdP 首次登录即自动绑定</div>}
          </section>
        </main>
      </div>
    </div>
  );
}
