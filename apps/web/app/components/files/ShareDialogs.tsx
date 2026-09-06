import { useEffect, useState } from "react";
import { liveEventBus } from "@rp/shared-state";
import type { LiveEnvelope } from "@rp/types";
import { FileShareAPI, unwrap, type LibraryFileRow, type ShareLinkRow } from "../../services/api";
import { toast } from "../Toast";
import { ConfirmDialog, PopoverMenu } from "./FileDialogs";
import { humanSize } from "./filelib-shared";

/** FILE-004 §3.1/§3.2 分享弹层（C.123/C.124）。
 *
 *  - ShareDialog：三件套（权限单选 view/download、密码开关 + 掩码 + 👁、
 *    有效期 永久/1/7/30/自定义（O5 日期选择））+ 警示行 + [创建分享]；
 *    创建成功切换到结果态（链接 + 复制 + 摘要）——链接在 POST 之后才存在
 *    （slug 服务端生成，BR-02 不可枚举）。
 *  - ShareManageDialog：列表（链接截断 + 复制 / 权限 / 有效期 / 访问计数）+
 *    行 ⋯（延期 30 天（BR-15 非幂等）/ 复制 / 吊销二次确认）；含失效态行；
 *    WS file.share.* 事件刷新列表（BR-13 内部视角）。 */

/** 有效期下拉选项（值 → expires_in_days 载荷；custom → 日期输入换算天数）。 */
const EXP_OPTIONS = [
  { key: "forever", label: "永久", days: null as number | null },
  { key: "d1", label: "1 天", days: 1 },
  { key: "d7", label: "7 天", days: 7 },
  { key: "d30", label: "30 天（默认）", days: 30 },
  { key: "custom", label: "自定义", days: undefined as unknown as number | null },
] as const;

function defaultCustomDate(): string {
  const d = new Date(Date.now() + 7 * 86_400_000); // O5：默认今天 +7
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast(`已复制：${text}`, "ok", { ttl: 1600 });
  } catch {
    // execCommand 降级（§4.4）
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); toast(`已复制：${text}`, "ok", { ttl: 1600 }); }
    catch { toast("复制失败，请手动选择链接复制", "warning"); }
    ta.remove();
  }
}

/** 有效期展示（管理列表/结果态：「29 天后 / 永久 / 已过期」）。 */
export function shareExpiryLabel(expiresAt: string | null): string {
  if (!expiresAt) return "永久";
  const days = Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 86_400_000);
  return days <= 0 ? "已过期" : `${days} 天后`;
}

const SHARE_STATUS_LABEL: Record<ShareLinkRow["status"], string> = {
  active: "有效",
  revoked: "已吊销",
  expired: "已过期",
  invalidated: "源失效",
};

export function ShareDialog({ slug, projectId, file, onClose }: {
  slug: string; projectId: string; file: LibraryFileRow; onClose: () => void;
}) {
  const [permission, setPermission] = useState<"view" | "download">("download");
  const [usePassword, setUsePassword] = useState(true);
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [expKey, setExpKey] = useState<string>("d30");
  const [expMenuOpen, setExpMenuOpen] = useState(false);
  const [customDate, setCustomDate] = useState(defaultCustomDate);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ShareLinkRow | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** 自定义有效期天数（O5：日期选择 → 1~365；事件期求值——渲染期取时被 purity 规则拦）。 */
  function customDays(): number {
    return Math.max(1, Math.min(365, Math.ceil((new Date(`${customDate}T23:59:59`).getTime() - Date.now()) / 86_400_000)));
  }
  const presetDays = expKey === "forever" ? null : EXP_OPTIONS.find((o) => o.key === expKey)?.days ?? 30;

  async function create() {
    if (usePassword) {
      const pwd = password.trim();
      if (pwd.length < 4 || pwd.length > 64) {
        toast("密码长度需在 4~64 位之间", "warning");
        return;
      }
    }
    setCreating(true);
    try {
      const r = await FileShareAPI.create(slug, projectId, file.id, {
        permission,
        ...(usePassword ? { password: password.trim() } : {}),
        ...(expKey === "custom" ? { expires_in_days: customDays() } : presetDays !== undefined ? { expires_in_days: presetDays } : {}),
      });
      setCreated(unwrap<ShareLinkRow>(r));
      toast("分享已创建（密码 Argon2id · 审计留痕）", "ok");
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "创建分享失败", "error");
    } finally {
      setCreating(false);
    }
  }

  const expLabel = expKey === "custom" ? "自定义" : EXP_OPTIONS.find((o) => o.key === expKey)?.label ?? "";

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-xl shadow-lg p-6 w-[520px] max-w-full max-h-[88vh] overflow-auto" role="dialog" aria-modal="true" aria-label="分享文件"
        data-sb-scope="share-create">
        <div className="flex items-center justify-between mb-4">
          <div className="text-[16px] font-semibold">分享 · {file.name}</div>
          <button type="button" aria-label="关闭" onClick={onClose} className="w-[30px] h-[30px] rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
        </div>

        {created ? (
          <>
            <div className="text-[13px] font-medium text-neutral-700 mb-1.5">链接（22 位不可枚举 slug）</div>
            <div className="flex gap-2" data-sb-scope="share-created-link">
              <input className="flex-1 h-9 px-3 rounded-md border border-neutral-300 font-mono text-[13px] w-full" readOnly
                value={created.share_url} aria-label="分享链接" data-sb-scope="share-link-input" />
              <button type="button" data-sb-scope="share-copy" onClick={() => { void copyText(created.share_url); }}
                className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-700 hover:bg-neutral-50 shrink-0">复制链接</button>
            </div>
            <div className="mt-3.5 text-[13px] text-neutral-600 space-y-1" data-sb-scope="share-created-summary">
              <div>权限：{created.permission === "download" ? "预览 + 下载" : "仅预览"}</div>
              <div>密码：{created.has_password ? "已开启（Argon2id 哈希存储）" : "未开启"}</div>
              <div>有效期：{shareExpiryLabel(created.expires_at)}</div>
            </div>
            <div className="mt-4 text-[12px] text-neutral-400">访问入口：space 匿名页（/s/{created.slug}）· 访问留痕与计数见「分享管理」</div>
            <div className="flex justify-end gap-2.5 mt-5">
              <button type="button" data-sb-scope="share-done" onClick={onClose}
                className="h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white">完成</button>
            </div>
          </>
        ) : (
          <>
            {/* 权限单选（BR-05：view 态下载按钮不渲染且 ?download=1 被拒） */}
            <div className="text-[13px] font-medium text-neutral-700 mb-1.5">权限</div>
            <div className="flex gap-4 text-[13px]" data-sb-scope="share-perm">
              <label className="inline-flex gap-1.5 items-center cursor-pointer">
                <input type="radio" name="sperm" value="view" checked={permission === "view"} data-share-perm="view"
                  onChange={() => setPermission("view")} className="accent-brand-500" /> 仅预览
              </label>
              <label className="inline-flex gap-1.5 items-center cursor-pointer">
                <input type="radio" name="sperm" value="download" checked={permission === "download"} data-share-perm="download"
                  onChange={() => setPermission("download")} className="accent-brand-500" /> 预览 + 下载
              </label>
            </div>

            {/* 密码开关 + 掩码输入 + 👁（BR-03 Argon2id） */}
            <div className="text-[13px] font-medium text-neutral-700 mt-4 mb-1.5">密码</div>
            <div className="flex items-center gap-2.5" data-sb-scope="share-pwd">
              <label className="inline-flex gap-1.5 items-center text-[13px] cursor-pointer">
                <input type="checkbox" checked={usePassword} data-sb-scope="share-pwd-toggle"
                  onChange={() => setUsePassword(!usePassword)} className="accent-brand-500" /> 开启
              </label>
              {usePassword && (
                <>
                  <input className="w-[180px] h-9 px-3 rounded-md border border-neutral-300 text-[13px]" type={showPwd ? "text" : "password"}
                    value={password} placeholder="4~64 位" aria-label="访问密码" data-sb-scope="share-pwd-input"
                    onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" aria-label={showPwd ? "隐藏密码" : "显示密码"} data-sb-scope="share-pwd-eye"
                    onClick={() => setShowPwd(!showPwd)}
                    className="w-[30px] h-[30px] rounded-md text-neutral-400 hover:bg-neutral-100">👁</button>
                </>
              )}
            </div>

            {/* 有效期（BR-04 ≤365；O5 自定义日期默认今天+7） */}
            <div className="text-[13px] font-medium text-neutral-700 mt-4 mb-1.5">有效期</div>
            <div className="relative" data-sb-scope="share-exp">
              <button type="button" data-sb-scope="share-exp-btn" aria-haspopup="menu" aria-expanded={expMenuOpen}
                onClick={() => setExpMenuOpen(expMenuOpen ? false : true)}
                className="h-8 px-2.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50 bg-white">
                {expLabel} ▾
              </button>
              {expMenuOpen && (
                <PopoverMenu items={EXP_OPTIONS.map((o) => ({ key: o.key, label: `${o.label}${expKey === o.key ? " ✓" : ""}` }))}
                  onPick={(k) => { setExpKey(k); setExpMenuOpen(false); }} close={() => setExpMenuOpen(false)} />
              )}
              {expKey === "custom" && (
                <input type="date" value={customDate} min={new Date().toISOString().slice(0, 10)}
                  aria-label="自定义有效期" data-sb-scope="share-exp-custom"
                  className="ml-2.5 h-8 px-2 rounded-md border border-neutral-300 text-[13px]"
                  onChange={(e) => setCustomDate(e.target.value)} />
              )}
            </div>

            {/* 警示行（C.123） */}
            <div className="mt-4 px-3 py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-[12.5px]"
              role="note" data-sb-scope="share-warn">
              ⚠ 任何获得链接（与密码）的人都能访问该文件
            </div>

            <div className="flex justify-end gap-2.5 mt-5">
              <button type="button" onClick={onClose}
                className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
              <button type="button" data-sb-scope="share-create-btn" disabled={creating} onClick={() => { void create(); }}
                className="h-9 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-[13px] text-white">创建分享</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function ShareManageDialog({ slug, projectId, file, onClose }: {
  slug: string; projectId: string; file: LibraryFileRow; onClose: () => void;
}) {
  const [rows, setRows] = useState<ShareLinkRow[] | null>(null);
  const [rowMenuFor, setRowMenuFor] = useState<string | null>(null);
  const [revokeFor, setRevokeFor] = useState<ShareLinkRow | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    FileShareAPI.list(slug, projectId, file.id)
      .then((r) => { setRows(unwrap<ShareLinkRow[]>(r) ?? []); })
      .catch(() => setRows([]));
  };

  useEffect(reload, [slug, projectId, file.id]); // oxlint-disable-line react-hooks/exhaustive-deps

  // WS：file.share.* → 列表刷新（BR-13；匿名访问不投——防刷屏）
  useEffect(() => {
    const onShare = (env: LiveEnvelope) => {
      const p = env.payload as unknown as { asset_id?: string };
      if (p?.asset_id !== file.id) return;
      reload();
    };
    const offs = [
      liveEventBus.on("file.share.created", onShare),
      liveEventBus.on("file.share.revoked", onShare),
      liveEventBus.on("file.share.extended", onShare),
    ];
    return () => offs.forEach((off) => off());
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, slug, projectId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !revokeFor) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, revokeFor]);

  async function extend(link: ShareLinkRow) {
    setBusy(true);
    try {
      const r = await FileShareAPI.extend(slug, projectId, link.id, { extend_days: 30 });
      const res = unwrap<{ expires_at: string | null }>(r);
      setRows((cur) => (cur ?? []).map((x) => (x.id === link.id ? { ...x, expires_at: res.expires_at } : x)));
      toast("已延期 30 天（非幂等动作——重试前请先核对有效期）", "ok");
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "延期失败", "error");
    } finally {
      setBusy(false);
    }
  }

  async function doRevoke() {
    if (!revokeFor) return;
    const link = revokeFor;
    setRevokeFor(null);
    try {
      await FileShareAPI.revoke(slug, projectId, link.id);
      setRows((cur) => (cur ?? []).map((x) => (x.id === link.id ? { ...x, status: "revoked" as const } : x)));
      toast("已吊销（已签发的 5 分钟预签名自然过期——BR-14 诚实上界）", "ok", { ttl: 3200 });
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "吊销失败", "error");
    }
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
        onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="bg-white rounded-xl shadow-lg p-6 w-[920px] max-w-full max-h-[88vh] overflow-auto" role="dialog" aria-modal="true" aria-label="分享管理"
          data-sb-scope="share-manage">
          <div className="flex items-center justify-between mb-4">
            <div className="text-[16px] font-semibold" data-sb-scope="share-manage-title">
              {file.name} 的分享（{rows?.length ?? 0}）
            </div>
            <button type="button" aria-label="关闭" onClick={onClose} className="w-[30px] h-[30px] rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
          </div>
          {rows == null ? (
            <div className="py-6 space-y-2">{[0, 1, 2].map((i) => <div key={i} className="h-9 rounded bg-neutral-100 animate-pulse" />)}</div>
          ) : rows.length === 0 ? (
            <div className="py-10 text-center text-[13px] text-neutral-500" data-sb-scope="share-manage-empty">暂无分享——从 ⋯ 菜单「分享…」创建第一条</div>
          ) : (
            <table className="w-full border-collapse text-[13px]" data-sb-scope="share-manage-table">
              <thead>
                <tr>
                  {["链接", "权限", "有效期", "访问", "操作"].map((h) => (
                    <th key={h} className="text-left px-2 py-2 border-b border-neutral-200 text-[12px] font-medium text-neutral-400">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className="hover:bg-neutral-50" data-sb-scope="share-manage-row" data-share-id={s.id} data-share-status={s.status}>
                    <td className="px-2 py-1.5 border-b border-neutral-100">
                      <span className="font-mono text-[12px] text-neutral-600">/s/{s.slug.slice(0, 10)}…</span>{" "}
                      <button type="button" className="text-[13px] text-brand-600 hover:underline" data-sb-scope="share-row-copy"
                        onClick={() => { void copyText(s.share_url); }}>复制</button>
                      {s.status !== "active" && <span className="text-[12px] text-neutral-400 ml-1">（{SHARE_STATUS_LABEL[s.status]}）</span>}
                    </td>
                    <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-600">{s.permission === "download" ? "预览 + 下载" : "仅预览"}</td>
                    <td className="px-2 py-1.5 border-b border-neutral-100 text-neutral-600" data-sb-scope="share-row-expiry">{shareExpiryLabel(s.expires_at)}</td>
                    <td className="px-2 py-1.5 border-b border-neutral-100 font-mono text-neutral-600">{s.access_count} 次</td>
                    <td className="px-2 py-1.5 border-b border-neutral-100">
                      <span className="relative inline-block">
                        <button type="button" aria-label="分享操作" data-share-row-menu={s.id}
                          onClick={() => setRowMenuFor(rowMenuFor === s.id ? null : s.id)}
                          className="h-7 px-2 rounded-md border border-neutral-300 text-[12.5px] text-neutral-600 hover:bg-neutral-50 bg-white">⋯</button>
                        {rowMenuFor === s.id && (
                          <PopoverMenu items={[
                            { key: "extend", label: "⏱ 延期 30 天" },
                            { key: "copy", label: "🔗 复制链接" },
                            { key: "sep", label: "", sep: true },
                            { key: "revoke", label: "🗑 吊销分享", danger: true },
                          ]} onPick={(k) => {
                            setRowMenuFor(null);
                            if (k === "extend") void extend(s);
                            else if (k === "copy") void copyText(s.share_url);
                            else if (k === "revoke") setRevokeFor(s);
                          }} close={() => setRowMenuFor(null)} />
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="text-[11.5px] text-neutral-400 mt-2.5" data-sb-scope="share-manage-hint">
            ⋯：延期 30 天 / 吊销（二次确认）/ 复制链接 · 密码 Argon2id · (IP, slug) 防爆破限流 · 文件大小 {humanSize(file.size_bytes)}
          </div>
        </div>
      </div>
      {revokeFor && (
        <ConfirmDialog title="吊销分享？" danger okText="吊销"
          onOk={() => { void doRevoke(); }} onClose={() => setRevokeFor(null)}>
          吊销后链接立即失效，访问者将看到「链接不存在或已失效」。
        </ConfirmDialog>
      )}
      {busy && <span className="sr-only" aria-live="polite">处理中…</span>}
    </>
  );
}
