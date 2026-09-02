import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useStores } from "../stores";
import { WorkspaceAPI } from "../services/api";

const ROLE_LABEL: Record<number, string> = { 20: "所有者", 15: "管理员", 10: "成员", 5: "访客" };
const hashColor = (id: string) =>
  ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"][(id.charCodeAt(0) ?? 48) % 5];

/** 顶栏（高保真 TEAM-001 §3.2/§3.3 + AUTH-001 退出）：左＝团队切换器下拉，右＝⌘K + 头像菜单。 */
export function Topbar() {
  const { session } = useStores();
  const nav = useNavigate();
  const [menu, setMenu] = useState<"switcher" | "avatar" | null>(null);
  const [showTeamModal, setShowTeamModal] = useState(false);
  const cur = session.workspaces.find((x) => x.slug === session.currentWsSlug);

  // 点击外部 / Esc 关闭下拉
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [menu]);

  async function switchWs(slug: string) {
    session.setCurrentWs(slug);
    nav(`/${slug}/projects`);
  }

  return (
    <header className="h-12 bg-white border-b border-neutral-200 flex items-center gap-2.5 px-3.5 relative z-30">
      {/* ── 团队切换器（左）── */}
      <div className="relative">
        <button
          onClick={(e) => { e.stopPropagation(); setMenu(menu === "switcher" ? null : "switcher"); }}
          aria-haspopup="listbox" aria-expanded={menu === "switcher"}
          className="flex items-center gap-2 h-9 px-2 rounded-md hover:bg-neutral-50">
          <span className="w-5 h-5 rounded-md text-white text-xs font-semibold flex items-center justify-center"
            style={{ background: cur ? hashColor(cur.id) : "#3b82f6" }}>{(cur?.name ?? "?").slice(0, 1)}</span>
          <span className="text-sm font-medium max-w-[160px] truncate">{cur?.name ?? "—"}</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        {menu === "switcher" && (
          <div className="absolute top-[42px] left-0 w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1.5" role="listbox">
            <div className="text-[11px] font-semibold text-neutral-400 px-2.5 py-1.5">我的团队</div>
            {session.workspaces.map((w) => (
              <button key={w.id} role="option" aria-selected={w.slug === session.currentWsSlug}
                onClick={(e) => { e.stopPropagation(); setMenu(null); if (w.slug !== session.currentWsSlug) switchWs(w.slug); }}
                className={`w-full h-10 px-2.5 flex items-center gap-2 text-left ${w.slug === session.currentWsSlug ? "bg-brand-50" : "hover:bg-neutral-50"}`}>
                <span className="w-[14px] text-brand-500 flex items-center justify-center">
                  {w.slug === session.currentWsSlug &&
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6 9 17l-5-5"/></svg>}
                </span>
                <span className="w-5 h-5 rounded text-white text-[10px] font-semibold flex items-center justify-center" style={{ background: hashColor(w.id) }}>{w.name.slice(0, 1)}</span>
                <span className="flex-1 text-[13px] truncate">{w.name}</span>
                <span className="text-xs text-neutral-400">{ROLE_LABEL[w.role] ?? "成员"}</span>
              </button>
            ))}
            <div className="h-px bg-neutral-200 my-1.5" />
            <button onClick={(e) => { e.stopPropagation(); setMenu(null); setShowTeamModal(true); }}
              className="w-full h-8 px-2.5 flex items-center gap-2 text-[13px] text-neutral-700 hover:bg-neutral-50">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>
              创建新团队
            </button>
          </div>
        )}
      </div>

      {/* ── 右：⌘K + 头像菜单 ── */}
      <div className="ml-auto flex items-center gap-2">
        <span className="h-7 px-2 border border-neutral-200 rounded-md text-xs text-neutral-400 bg-white flex items-center gap-1" title="全局搜索 · Sprint 1+ 交付">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>⌘K
        </span>
        <div className="relative">
          <button onClick={(e) => { e.stopPropagation(); setMenu(menu === "avatar" ? null : "avatar"); }}
            aria-haspopup="menu" aria-expanded={menu === "avatar"} aria-label="账号菜单"
            className="w-7 h-7 rounded-full text-white text-xs font-semibold flex items-center justify-center"
            style={{ background: hashColor(session.user?.id ?? "u") }}>
            {(session.user?.display_name ?? "?").slice(0, 1)}
          </button>
          {menu === "avatar" && (
            <div className="absolute top-[36px] right-0 w-[190px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" role="menu">
              <div className="px-3 py-2">
                <div className="text-[13px] font-semibold truncate">{session.user?.display_name}</div>
                <div className="text-xs text-neutral-400 truncate">{session.user?.email}</div>
              </div>
              <div className="h-px bg-neutral-200 my-1" />
              <button role="menuitem" onClick={async (e) => {
                e.stopPropagation(); setMenu(null);
                try { await session.signOut(); } catch { /* 会话可能已失效，仍落地登录页 */ }
                location.href = "/login"; // 登出全量重载：清空内存态（store/缓存），避免 SPA 内残留
              }}
                className="w-full h-8 px-3 flex items-center gap-2 text-[13px] text-neutral-700 hover:bg-neutral-50">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/></svg>
                退出登录
              </button>
            </div>
          )}
        </div>
      </div>

      {showTeamModal && <CreateTeamModal onClose={() => setShowTeamModal(false)} />}
    </header>
  );
}

/** 创建团队弹窗（高保真 TEAM-001 §3.3：480px，slug 预览，描述 500 计数） */
function CreateTeamModal({ onClose }: { onClose: () => void }) {
  const { session } = useStores();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const slugPreview = name.trim()
    ? "访问地址预览：rabbit.example.com/" + name.trim().toLowerCase().replace(/[^a-z0-9一-龥]+/g, "-").replace(/^-|-$/g, "")
    : "";

  async function submit() {
    if (!name.trim() || loading) return;
    setLoading(true); setErr(null);
    try {
      const r = await WorkspaceAPI.create(name.trim(), desc.trim() || undefined);
      const slug = (r as any).data?.slug as string | undefined;
      await session.bootstrap(); // 刷新工作区列表（含新团队 OWNER 角色）
      if (slug) session.setCurrentWs(slug); // 顶栏跟随新团队（bootstrap 会把 currentWs 重置为默认团队）
      onClose();
      if (slug) nav(`/${slug}/projects`);
    } catch (e: any) {
      setErr(e?.message ?? "创建失败"); setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(); }}>
      <div className="bg-white rounded-xl shadow-lg w-[480px] max-w-full p-6" role="dialog" aria-modal="true">
        <div className="flex items-center justify-between mb-[18px]">
          <div className="text-base font-semibold">创建团队</div>
          <button onClick={onClose} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        {err && <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px]">{err}</div>}
        <div className="mb-4">
          <label htmlFor="tm-name" className="block text-[13px] font-medium text-neutral-700 mb-1.5">团队名称 *</label>
          <input id="tm-name" autoFocus className="w-full h-9 border border-neutral-300 rounded-md px-2.5 focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50"
            maxLength={80} placeholder="RabbitProjects" value={name}
            onChange={(e) => setName(e.target.value)} />
          {slugPreview && <div className="text-xs text-neutral-400 mt-1.5">{slugPreview}（预览，最终由服务端生成）</div>}
        </div>
        <div className="mb-4">
          <label htmlFor="tm-desc" className="block text-[13px] font-medium text-neutral-700 mb-1.5">团队描述</label>
          <textarea id="tm-desc" rows={3} maxLength={500} className="w-full border border-neutral-300 rounded-md p-2 text-sm focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50"
            placeholder="企业级项目管理系统研发团队" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <div className="text-right text-xs text-neutral-400 mt-1">{desc.length} / 500</div>
        </div>
        <div className="flex justify-end gap-2.5">
          <button onClick={onClose} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
          <button onClick={submit} disabled={!name.trim() || loading}
            className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50">{loading ? "创建中…" : "创建团队"}</button>
        </div>
      </div>
    </div>
  );
}
