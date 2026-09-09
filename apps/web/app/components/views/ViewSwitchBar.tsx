import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import type { ViewLayout } from "@rp/shared-state";

import { ViewGovernanceAPI } from "../../services/api";
import { reloadViewsFor } from "./useViewPage";
import { toast } from "../Toast";
import { DegradedBanner, PresenceBar } from "../../realtime/PresenceBar";
import type { ViewPage } from "./useViewPage";
import { DisplayDrawer } from "./DisplayDrawer";
import { SaveViewModal } from "./SaveViewModal";
import { FilterPanelDrawer } from "./FilterPanelDrawer";
import { groupDisplayName } from "./view-dsl";

/** BOARD-003 §3.1 视图切换器工具条（R1：布局器最左 / R2：?view_id=）。
 *  覆盖 C.64（四段器/Tabs/星标/＋▾/右键菜单/⚙显示/分组切换器）、C.65（黄条+离开确认）、
 *  C.70（引导/删除回退/停用降级黄条）。presence 位 = 空容器（COLLAB-004 C 批）。 */

const LAYOUT_SEGS: Array<{ key: ViewLayout; label: string }> = [
  { key: "list", label: "列表" },
  { key: "kanban", label: "看板" },
  { key: "table", label: "表格" },
  { key: "gantt", label: "甘特" },
];

/** 通用锚定下拉菜单（mousedown 外击关闭——CLAUDE.md 教训 #4：mousedown 阶段 + closest 判 scope）。 */
export function AnchorMenu({ scope, label, className, children }: {
  scope: string; label: string; className?: string; children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t?.closest(`[data-sb-scope="${scope}"]`)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open, scope]);
  return (
    <span className={`relative ${className ?? ""}`} data-sb-scope={scope}>
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{label}</button>
      {open && (
        <div role="menu" className="absolute left-0 top-[34px] z-40 min-w-[170px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}

/** 视图 Tab 右键菜单 / ＋▾ 下拉的菜单项。 */
function MenuItem({ label, onClick, danger, disabled, checked, onContextMenu }: {
  label: string; onClick: () => void; danger?: boolean; disabled?: boolean; checked?: boolean;
  onContextMenu?: ((e: React.MouseEvent) => void) | undefined;
}) {
  return (
    <button type="button" role="menuitem" disabled={disabled} onClick={onClick} onContextMenu={onContextMenu}
      className={`w-full text-left px-3 h-8 text-[13px] flex items-center gap-2 ${danger ? "text-red-600" : "text-neutral-700"} hover:bg-neutral-50 disabled:text-neutral-300 disabled:cursor-not-allowed disabled:hover:bg-transparent`}>
      {checked !== undefined && <span className="w-3.5 text-brand-600">{checked ? "✓" : ""}</span>}
      {label}
    </button>
  );
}

export function ViewSwitchBar({ vp }: { vp: ViewPage }) {
  const navigate = useNavigate();
  const { views, currentView, dirty } = vp;
  const viewId = vp.viewIdParam;

  // ── 弹层状态 ──
  const [showDisplay, setShowDisplay] = useState(false);
  const [showSaveAs, setShowSaveAs] = useState(false);
  /** 筛选面板（filterbar「⊞ 筛选」与右键「编辑条件」经 rp:open-filter-panel 事件打开）。 */
  const [showFilter, setShowFilter] = useState(false);
  useEffect(() => {
    const openFilter = () => setShowFilter(true);
    const openSave = () => setShowSaveAs(true);
    window.addEventListener("rp:open-filter-panel", openFilter);
    window.addEventListener("rp:open-save-view", openSave);
    return () => {
      window.removeEventListener("rp:open-filter-panel", openFilter);
      window.removeEventListener("rp:open-save-view", openSave);
    };
  }, []);
  const [renameFor, setRenameFor] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  /** 右键菜单（Tab contextmenu；锚定固定定位）。 */
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; viewId: string } | null>(null);
  /** ＋▾ 折叠下拉（fixed 定位——view-tabs 容器 overflow-hidden 会把向下展开的
   *  absolute 菜单整体裁掉，Sprint-8 缺陷：菜单 DOM 已开但视觉不可见）。 */
  const [moreOpen, setMoreOpen] = useState(false);
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  const [morePos, setMorePos] = useState<{ x: number; y: number } | null>(null);
  /** 分组切换下拉。 */
  const [groupOpen, setGroupOpen] = useState(false);
  /** 首次引导（§3.6：项目无自定义视图时出现；知道了 → localStorage 记忆）。 */
  const [guideOpen, setGuideOpen] = useState(false);
  const guideStorageKey = useRef("");
  /** 未保存离开确认（§3.1：dirty 下切视图/布局）。 */
  const [leaveAsk, setLeaveAsk] = useState<null | (() => void)>(null);
  /** Tab 折叠：全部 + 前 5 个视图内联，其余入 ＋ ▾（§3.1「超出 6 个折叠」）。 */
  const INLINE_MAX = 6;
  const tabs = [{ id: null as string | null, name: "全部", icon: "", isSystem: true, fixed: true }, ...views.map((v) => ({ id: v.id, name: v.name + (v.is_locked ? " 🔒" : "") + (v.is_project_default ? "★" : "") + (v.access === "shared" && !v.is_locked ? " 👤" : ""), icon: (v.display_props?.icon ?? "") as string, isSystem: v.is_system, fixed: false }))];

  /** BOARD-005 治理动作（C.153：共享/锁定/项目默认/订阅/副本） */
  const gov = {
    toggleShare: async (viewId: string, toShared: boolean) => {
      try {
        await ViewGovernanceAPI.setAccess(vp.workspaceSlug!, vp.projectId!, viewId, toShared ? "shared" : "personal");
        toast(toShared ? "已共享（全员可见）" : "已收回共享（订阅一并清除）");
        void reloadViewsFor(vp);
      } catch (e) { toast((e as { message?: string })?.message ?? "操作失败", "error"); }
    },
    lock: async (viewId: string, isLocked: boolean, setDefault?: boolean) => {
      try {
        await ViewGovernanceAPI.lock(vp.workspaceSlug!, vp.projectId!, viewId,
          { is_locked: isLocked, ...(setDefault !== undefined ? { is_project_default: setDefault } : {}) });
        toast(isLocked ? (setDefault ? "已锁定并设为项目默认（存量成员已订阅）" : "已锁定（仅管理员可改）") : "已解锁");
        void reloadViewsFor(vp);
      } catch (e) { toast((e as { message?: string })?.message ?? "操作失败（先取消默认再解锁）", "error"); }
    },
    pin: async (viewId: string) => {
      try {
        await ViewGovernanceAPI.pin(vp.workspaceSlug!, vp.projectId!, viewId);
        toast("已订阅（侧栏可见）");
      } catch (e) { toast((e as { message?: string })?.message ?? "仅共享视图支持订阅", "error"); }
    },
    duplicate: async (viewId: string) => {
      try {
        await ViewGovernanceAPI.duplicate(vp.workspaceSlug!, vp.projectId!, viewId);
        toast("已另存为个人副本");
        void reloadViewsFor(vp);
      } catch (e) { toast((e as { message?: string })?.message ?? "复制失败", "error"); }
    },
  };
  const inlineTabs = tabs.slice(0, INLINE_MAX);
  const overflowTabs = tabs.slice(INLINE_MAX);

  useEffect(() => {
    // 引导条件：视图列表已加载 && 无个人视图 && 未点过「知道了」
    const pid = location.pathname.split("/projects/")[1]?.split("/")[0] ?? "";
    guideStorageKey.current = `views-guide-dismissed:${pid}`;
    try {
      if (!localStorage.getItem(guideStorageKey.current) && views.length > 0 && !views.some((v) => !v.is_system)) {
        setGuideOpen(true);
      }
    } catch { /* 私有模式 */ }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [views.length]);

  useEffect(() => {
    if (!ctxMenu) return;
    const close = (e: MouseEvent) => {
      // 菜单项自身的 mousedown 不关闭——否则捕获阶段先卸载菜单，click 永远到不了（S3V-13 实测）
      const t = e.target as HTMLElement | null;
      if (!t?.closest('[data-sb-scope="view-ctx-menu"]')) setCtxMenu(null);
    };
    document.addEventListener("mousedown", close, true);
    return () => document.removeEventListener("mousedown", close, true);
  }, [ctxMenu]);
  useEffect(() => {
    if (!moreOpen && !groupOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (moreOpen && !t?.closest('[data-sb-scope="views-more"]')) setMoreOpen(false);
      if (groupOpen && !t?.closest('[data-sb-scope="view-group-dd"]')) setGroupOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [moreOpen, groupOpen]);

  /** dirty 守卫：任何离开当前视图的操作先过确认（C.65）。 */
  function guardLeave(action: () => void) {
    if (dirty) setLeaveAsk(() => action);
    else action();
  }

  /** 当前项目 id（URL 段解析；默认视图 ★ 读取 board.default_view_id 镜像）。 */
  const projectId = location.pathname.split("/projects/")[1]?.split("/")[0] ?? "";
  const defaultVid = vp.defaultViewIdOf(projectId);

  return (
    <div className="flex items-center gap-2.5 px-5 py-2.5 border-b border-neutral-200 bg-white flex-wrap shrink-0" data-sb-scope="viewswitch" role="tablist" aria-label="视图">
      {/* 布局四段器（R1：最左；gantt 自 Sprint-4 起启用——GANTT-001 §3.1 视图切换条） */}
      <div className="inline-flex bg-neutral-100 rounded-lg p-0.5 gap-0.5" role="group" aria-label="布局" data-sb-scope="layout-seg">
        {LAYOUT_SEGS.map((s) => {
          const active = vp.layout === s.key;
          return (
            <button key={s.key} type="button" aria-pressed={active} data-sb-scope={`layout-seg-${s.key}`}
              onClick={() => { if (active) return; guardLeave(() => void vp.switchLayout(s.key, navigate)); }}
              className={`h-7 px-2.5 rounded-md text-[12.5px] inline-flex items-center gap-1.5 ${active ? "bg-white text-neutral-900 font-medium shadow-sm" : "text-neutral-400 hover:text-neutral-600"}`}>
              {s.key === "list" ? "☰" : s.key === "kanban" ? "▦" : s.key === "gantt" ? "📅" : "▤"} {s.label}
            </button>
          );
        })}
      </div>

      {/* 视图 Tabs：「全部」固定首项 + 内置🔒 + ★默认 + 超出折叠 */}
      <div className="flex items-center gap-0.5 min-w-0 overflow-hidden" data-sb-scope="view-tabs">
        {inlineTabs.map((t) => {
          const active = (t.id ?? null) === (viewId ?? null);
          const isDefault = t.id != null && t.id === vp.defaultViewIdOf(projectId);
          return (
            <button key={t.id ?? "__all__"} type="button" role="tab" aria-selected={active}
              data-sb-scope="view-tab" data-view-id={t.id ?? "__all__"} data-view-name={t.name}
              onClick={() => { if (active) return; guardLeave(() => vp.switchView(t.id)); }}
              onContextMenu={(e) => {
                if (t.fixed) return;
                e.preventDefault();
                setCtxMenu({ x: e.clientX, y: e.clientY, viewId: t.id! });
              }}
              className={`group h-8 px-2.5 rounded-md text-[13px] inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 ${active ? "text-neutral-900 font-semibold border-brand-500" : "text-neutral-500 border-transparent hover:bg-neutral-100"}`}>
              {t.icon ? <span aria-hidden="true">{t.icon}</span> : null}
              <span>{t.name}</span>
              {t.fixed ? null : t.isSystem ? (
                <span className="text-[11px] text-neutral-300" title="内置视图（口径锁定）" aria-label="内置视图">🔒</span>
              ) : null}
              {t.fixed ? null : isDefault ? (
                <span className="text-[12px] text-amber-500" title="默认视图" aria-label="默认视图">★</span>
              ) : active ? (
                <span className="text-[12px] text-neutral-300 opacity-0 group-hover:opacity-70" title="设为默认"
                  aria-label="设为默认" data-sb-scope="view-tab-star"
                  onClick={(e) => { e.stopPropagation(); void vp.setDefaultView(t.id); }}>★</span>
              ) : null}
            </button>
          );
        })}
        <span className="relative" data-sb-scope="views-more">
          <button type="button" ref={moreBtnRef} aria-haspopup="menu" aria-expanded={moreOpen} data-sb-scope="views-more-btn"
            onClick={() => {
              if (!moreOpen) {
                const r = moreBtnRef.current?.getBoundingClientRect();
                if (r) setMorePos({ x: r.left, y: r.bottom + 6 });
              }
              setMoreOpen((v) => !v);
            }}
            className="h-7 px-2 rounded-md text-[12.5px] text-neutral-400 inline-flex items-center gap-1 hover:bg-neutral-100 hover:text-neutral-600">＋ ▾</button>
          {moreOpen && (
            <div role="menu" aria-label="更多视图" style={morePos ? {
              position: "fixed",
              left: Math.min(morePos.x, window.innerWidth - 200),
              top: morePos.y,
              zIndex: 60,
            } : undefined}
              className="min-w-[190px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[320px] overflow-y-auto">
              {overflowTabs.map((t) => (
                <MenuItem key={t.id ?? "__all__"} label={`${t.icon ? t.icon + " " : ""}${t.name}${t.id === defaultVid ? " ★" : ""}`}
                  checked={(t.id ?? null) === (viewId ?? null)}
                  onClick={() => { setMoreOpen(false); if ((t.id ?? null) !== (viewId ?? null)) guardLeave(() => vp.switchView(t.id)); }}
                  onContextMenu={t.fixed ? undefined : (e) => {
                    e.preventDefault();
                    setCtxMenu({ x: e.clientX, y: e.clientY, viewId: t.id! });
                  }} />
              ))}
              {overflowTabs.length > 0 && <div className="h-px bg-neutral-100 my-1" />}
              <MenuItem label="新建视图" onClick={() => { setMoreOpen(false); setShowSaveAs(true); }} />
              <MenuItem label="从当前筛选另存" onClick={() => { setMoreOpen(false); setShowSaveAs(true); }} />
              <div className="h-px bg-neutral-100 my-1" />
              <MenuItem label="管理视图（P3 共享设置）" disabled onClick={() => {}} />
            </div>
          )}
        </span>
      </div>

      {/* 右侧：presence 头像列 + 连接指示（COLLAB-004 §3.1 · O1 消费位点亮）+ 分组切换器（kanban）+ ⚙ 显示 */}
      <div className="ml-auto flex items-center gap-2">
        <PresenceBar projectId={projectId} members={vp.members} />
        {vp.layout === "kanban" && (
          <span className="relative" data-sb-scope="view-group-dd">
            {/* BOARD-005（C.154）：行分组（泳道）——同集维度，清空回一维 */}
            <select aria-label="泳道维度" data-sb-scope="view-subgroup-select"
              value={new URLSearchParams(window.location.search).get("sub_group_by") ?? ""}
              onChange={(e) => {
                const n = new URLSearchParams(window.location.search);
                if (e.target.value) n.set("sub_group_by", e.target.value);
                else n.delete("sub_group_by");
                window.history.replaceState(null, "", `?${n}${location.hash}`);
                window.location.reload();  // 矩阵/一维切换走整页换形态（保 SWR key 干净）
              }}
              className="h-8 px-2 border border-neutral-300 rounded-md text-[13px] bg-white">
              <option value="">泳道：无</option>
              {vp.groupCandidates
                .filter((d) => d.key !== vp.groupBy)
                .map((d) => <option key={d.key} value={d.key}>泳道：{d.name}</option>)}
            </select>
                        <button type="button" aria-haspopup="menu" aria-expanded={groupOpen} aria-label="分组维度" data-sb-scope="view-group-btn"
              onClick={() => setGroupOpen((v) => !v)}
              className="h-8 px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">
              分组：{groupDisplayName(vp.groupBy, vp.cfDefs.find((d) => d.key === vp.groupBy)?.name)} ▾
            </button>
            {groupOpen && (
              <div role="menu" aria-label="分组维度" className="absolute right-0 top-[36px] z-40 min-w-[200px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[320px] overflow-y-auto">
                {vp.groupCandidates.map((g) => (
                  <MenuItem key={g.key} label={g.name} checked={g.key === vp.groupBy}
                    onClick={() => {
                      setGroupOpen(false);
                      if (g.key === vp.groupBy) return;
                      vp.patchDisplay({ group_by: g.key });
                      toast("分组列由配置生成（无 SELECT DISTINCT）· 列重建零请求", "info");
                    }} />
                ))}
              </div>
            )}
          </span>
        )}
        <button type="button" data-sb-scope="view-display-btn" aria-label="显示配置"
          onClick={() => setShowDisplay(true)}
          className="h-8 px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">⚙ 显示 ▾</button>
      </div>

      {/* 实时降级横幅（COLLAB-004 §3.1 / BR-10：live 不可达 → 轮询模式黄条，恢复自动撤除） */}
      <DegradedBanner />
      {/* 停用字段降级黄条（§3.6 / meta.degraded.group_by） */}
      {vp.degraded && (
        <div role="status" data-sb-scope="view-degraded-bar"
          className="flex items-center gap-2.5 bg-amber-50 border border-amber-200 text-amber-700 rounded-lg px-3 py-1.5 text-[12.5px] w-full">
          ⚠ 分组字段已停用，已回退状态分组（{vp.degraded}）
        </div>
      )}
      {/* 视图已删除/他人视图回退黄条（§3.6 / BR-12） */}
      {vp.viewGone && (
        <div role="status" data-sb-scope="view-gone-bar"
          className="flex items-center gap-2.5 bg-amber-50 border border-amber-200 text-amber-700 rounded-lg px-3 py-1.5 text-[12.5px] w-full">
          ⚠ 视图不存在或不可见（已切换默认视图）
        </div>
      )}
      {/* BOARD-005（C.153）：锁定横幅——组织标准视图只读 + 副本引导 */}
      {currentView?.is_locked && (
        <div data-sb-scope="view-locked-bar"
          className="flex items-center gap-2.5 bg-blue-50 border border-blue-200 text-blue-700 rounded-lg px-3 py-1.5 text-[12.5px] w-full">
          🔒 组织标准视图，只读
          <button type="button" data-sb-scope="view-locked-fork"
            onClick={() => void gov.duplicate(currentView.id)}
            className="h-[26px] px-2.5 bg-white border border-blue-200 text-blue-600 rounded-md text-[12.5px] hover:bg-blue-50">另存为副本</button>
          {currentView.is_project_default && <span>★ 项目默认（新成员自动订阅）</span>}
        </div>
      )}
      {/* 视图已修改黄条（§3.1） */}
      {dirty && (
        <div data-sb-scope="view-dirty-bar"
          className="flex items-center gap-2.5 bg-amber-50 border border-amber-200 text-amber-700 rounded-lg px-3 py-1.5 text-[12.5px] w-full">
          视图已修改
          {currentView && (
            <button type="button" data-sb-scope="view-dirty-save"
              onClick={() => void vp.saveInPlace()}
              className="h-[26px] px-2.5 bg-brand-500 text-white rounded-md text-[12.5px] hover:bg-brand-600">保存</button>
          )}
          <button type="button" data-sb-scope="view-dirty-saveas"
            onClick={() => setShowSaveAs(true)}
            className="h-[26px] px-2.5 border border-neutral-300 bg-white rounded-md text-[12.5px] text-neutral-700 hover:bg-neutral-50">另存为</button>
          <button type="button" data-sb-scope="view-dirty-discard"
            onClick={() => { vp.discardChanges(); toast("已放弃未保存的修改", "info"); }}
            className="h-[26px] px-2.5 border border-neutral-300 bg-white rounded-md text-[12.5px] text-neutral-700 hover:bg-neutral-50">放弃</button>
        </div>
      )}
      {/* 首次引导气泡（§3.6） */}
      {guideOpen && (
        <div data-sb-scope="view-guide-pop"
          className="relative bg-white border border-brand-100 shadow-md rounded-[10px] px-3.5 py-2.5 text-[12.5px] text-neutral-600 inline-flex gap-2.5 items-center">
          ✨ 保存你的第一块看板：筛选 + 分组 + 显示列可存为个人视图
          <button type="button" data-sb-scope="view-guide-ok"
            onClick={() => {
              setGuideOpen(false);
              try { localStorage.setItem(guideStorageKey.current, "1"); } catch { /* ignore */ }
            }}
            className="h-[26px] px-2.5 bg-brand-500 text-white rounded-md text-[12.5px]">知道了</button>
        </div>
      )}

      {/* Tab 右键菜单（§3.1 / TASK-011 §3.2；内置无删除） */}
      {ctxMenu && (() => {
        const v = views.find((x) => x.id === ctxMenu.viewId);
        if (!v) return null;
        return (
          <div role="menu" aria-label={`视图 ${v.name} 菜单`} data-sb-scope="view-ctx-menu"
            style={{ position: "fixed", left: Math.min(ctxMenu.x, window.innerWidth - 190), top: ctxMenu.y, zIndex: 60 }}
            className="min-w-[170px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
            {!v.is_system && (
              <MenuItem label="重命名" onClick={() => { setCtxMenu(null); setRenameFor(v.id); setRenameDraft(v.name); }} />
            )}
            <MenuItem label="编辑条件（打开筛选面板）" onClick={() => { setCtxMenu(null); window.dispatchEvent(new CustomEvent("rp:open-filter-panel")); }} />
            <MenuItem label="复制视图" onClick={() => { setCtxMenu(null); setShowSaveAs(true); }} />
            <MenuItem label="设为默认视图（用户级）" onClick={() => { setCtxMenu(null); void vp.setDefaultView(v.id); }} />
            {/* BOARD-005（C.153）：治理菜单 */}
            <div className="my-1 border-t border-neutral-100" />
            {!v.is_system && !v.is_locked && (
              <MenuItem label={v.access === "shared" ? "收回共享" : "共享给全员"}
                onClick={() => { setCtxMenu(null); void gov.toggleShare(v.id, v.access !== "shared"); }} />
            )}
            {!v.is_system && (
              <MenuItem label={v.is_locked ? "解锁（先取消项目默认）" : "锁定为组织标准"}
                onClick={() => { setCtxMenu(null); void gov.lock(v.id, !v.is_locked); }} />
            )}
            {v.is_locked && (
              <MenuItem label={v.is_project_default ? "取消项目默认" : "设为项目默认（新成员订阅）"}
                onClick={() => { setCtxMenu(null); void gov.lock(v.id, true, !v.is_project_default); }} />
            )}
            {v.access === "shared" && (
              <MenuItem label="订阅到侧栏" onClick={() => { setCtxMenu(null); void gov.pin(v.id); }} />
            )}
            <MenuItem label="另存为我的副本" onClick={() => { setCtxMenu(null); void gov.duplicate(v.id); }} />
            {!v.is_system && !v.is_locked && (
              <MenuItem label="删除" danger onClick={() => {
                setCtxMenu(null);
                void vp.deleteView(v.id);
              }} />
            )}
          </div>
        );
      })()}

      {/* 未保存离开确认（C.65） */}
      {leaveAsk && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[85]" data-sb-scope="view-leave-confirm">
          <div className="bg-white rounded-xl shadow-lg w-[400px] p-5" role="dialog" aria-modal="true" aria-label="未保存的视图修改">
            <div className="text-base font-semibold mb-2">未保存的视图修改</div>
            <div className="text-[13.5px] text-neutral-700">当前视图有未保存的显示配置修改，离开将丢失。保存后继续、放弃修改，还是留在本页？</div>
            <div className="flex justify-end gap-2.5 mt-5">
              <button type="button" onClick={() => setLeaveAsk(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
              <button type="button" data-sb-scope="view-leave-discard"
                onClick={() => { vp.discardChanges(); const fn = leaveAsk; setLeaveAsk(null); fn(); }}
                className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">放弃修改</button>
              {currentView && (
                <button type="button" data-sb-scope="view-leave-save"
                  onClick={() => { void (async () => { const ok = await vp.saveInPlace(); if (ok) { const fn = leaveAsk; setLeaveAsk(null); fn(); } })(); }}
                  className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">保存并离开</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 重命名弹层 */}
      {renameFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[85]" data-sb-scope="view-rename-modal">
          <div className="bg-white rounded-xl shadow-lg w-[400px] p-5" role="dialog" aria-modal="true" aria-label="重命名视图">
            <div className="text-base font-semibold mb-3">重命名视图</div>
            <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">名称（≤128）</label>
            <input autoFocus maxLength={128} value={renameDraft} onChange={(e) => setRenameDraft(e.target.value)}
              className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px] focus:outline-none focus:border-brand-500"
              aria-label="视图名称" />
            <div className="flex justify-end gap-2.5 mt-5">
              <button type="button" onClick={() => setRenameFor(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
              <button type="button" data-sb-scope="view-rename-go"
                onClick={() => { if (renameDraft.trim()) { void vp.renameView(renameFor, renameDraft.trim()); setRenameFor(null); } }}
                className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">保存</button>
            </div>
          </div>
        </div>
      )}

      {showDisplay && <DisplayDrawer vp={vp} onClose={() => setShowDisplay(false)} />}
      {showSaveAs && <SaveViewModal vp={vp} onClose={() => setShowSaveAs(false)} />}
      {showFilter && <FilterPanelDrawer vp={vp} onClose={() => setShowFilter(false)} />}
    </div>
  );
}
