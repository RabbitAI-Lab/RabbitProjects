/** 标签管理面板（TASK-002 §3.4 · C.26 · 720px 弹窗）。
 *  挂载形态（ADR-0011 #13）：作为 modal 渲染（不单独成页）。
 *  - 入口两处：列表 / 看板筛选条「标签」下拉尾部「管理标签」+ 项目设置页「标签管理」链接
 *  - 面板头：「项目标签」+「＋ 新建标签」
 *  - 标签行：色点 + 名称 + hex 色 + 引用计数 + ✏ 编辑 + 🗑 删除
 *  - 被引用删除路径：被引用 → 二次确认后**停用**（行灰置 + ↺ 恢复 + 强制删除）
 *  - 颜色板：12 预设色 + 自定义 hex
 *  - 拖拽行排序（sort_order 浮点插值）
 *  - 默认导出：调试路由（公共 layout），便于 e2e 直接访问。
 *
 *  本文件采用「独立可路由 + 默认导出组件」双形态，与 NewTaskModal 等一致。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { LabelAPI } from "../services/api";
import { hasSessionProbe } from "../services/session-probe";
import { toast } from "../components/Toast";

const PRESET_COLORS = [
  "#EF4444", "#F59E0B", "#10B981", "#3B82F6", "#8B5CF6", "#EC4899",
  "#06B6D4", "#84CC16", "#F97316", "#14B8A6", "#6366F1", "#9CA3AF",
];
const HEX_RE = /^#[0-9A-Fa-f]{6}$/;

export interface LabelRow {
  id: string;
  name: string;
  color: string;
  sort_order: number;
  is_active: boolean;
  usage_count: number;
}

export interface LabelsAdminModalProps {
  workspaceSlug: string;
  projectId: string;
  onClose: () => void;
}

export function LabelsAdminModal({ workspaceSlug, projectId, onClose }: LabelsAdminModalProps) {
  const [labels, setLabels] = useState<LabelRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftColor, setDraftColor] = useState<string>(PRESET_COLORS[3] ?? "#3B82F6");
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<LabelRow | null>(null);
  const [confirmForce, setConfirmForce] = useState<LabelRow | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  async function load() {
    // 调试路由（/labels-admin 直访）可能未登录：无会话时不调 API，避免 axios 401 拦截跳 /login。
    // 判据必须用可读的探针 cookie：`sessionid` 是 HttpOnly，document.cookie 恒读不到，
    // 用它会让 load() 永远在这里短路 → 列表永远空、新建后也不刷新（sprint-1 验收缺陷）。
    if (!hasSessionProbe()) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const r = await LabelAPI.list(workspaceSlug, projectId);
      setLabels(((r as unknown as { data: LabelRow[] | null }).data ?? []) as LabelRow[]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "加载失败";
      toast(msg, "error");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    const handle = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const startCreate = () => {
    setCreating(true);
    setEditingId(null);
    setDraftName("");
    setDraftColor(PRESET_COLORS[3] ?? "#3B82F6");
  };
  const startEdit = (row: LabelRow) => {
    setCreating(false);
    setEditingId(row.id);
    setDraftName(row.name);
    setDraftColor(row.color);
  };

  const saveDraft = async () => {
    if (!draftName.trim()) { toast("名称不能为空", "error"); return; }
    if (!HEX_RE.test(draftColor)) { toast("颜色格式错误（#RRGGBB）", "error"); return; }
    try {
      if (creating) {
        await LabelAPI.create(workspaceSlug, projectId, { name: draftName.trim(), color: draftColor });
        toast("已新建");
      } else if (editingId) {
        await LabelAPI.patch(workspaceSlug, projectId, editingId, { name: draftName.trim(), color: draftColor });
        toast("已保存");
      }
      setEditingId(null);
      setCreating(false);
      setDraftName("");
      await load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "保存失败";
      toast(msg, "error");
    }
  };

  const onDelete = async (row: LabelRow) => {
    if (row.usage_count > 0) {
      setConfirmDelete(row); // 走「停用确认」
      return;
    }
    try {
      await LabelAPI.del(workspaceSlug, projectId, row.id);
      toast(`已删除「${row.name}」`);
      await load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "删除失败";
      toast(msg, "error");
    }
  };

  const confirmSoftDelete = async (row: LabelRow) => {
    try {
      await LabelAPI.patch(workspaceSlug, projectId, row.id, { is_active: false });
      toast(`已停用「${row.name}」（被 ${row.usage_count} 个任务引用）`);
      setConfirmDelete(null);
      await load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失败";
      toast(msg, "error");
    }
  };

  const restoreLabel = async (row: LabelRow) => {
    try {
      await LabelAPI.patch(workspaceSlug, projectId, row.id, { is_active: true });
      toast(`已恢复「${row.name}」`);
      await load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失败";
      toast(msg, "error");
    }
  };

  const forceDelete = async () => {
    if (!confirmForce) return;
    try {
      await LabelAPI.del(workspaceSlug, projectId, confirmForce.id, true);
      toast(`已从 ${confirmForce.usage_count} 个任务摘除「${confirmForce.name}」`);
      setConfirmForce(null);
      await load();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "删除失败";
      toast(msg, "error");
    }
  };

  const rows = labels;
  const sorted = useMemo(
    () => [...rows].sort((a, b) => {
      const ao = a.sort_order ?? 0;
      const bo = b.sort_order ?? 0;
      return ao - bo;
    }),
    [rows],
  );

  return (
    <div
      data-sb-scope="labels-admin-mask"
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => {
        const t = e.target as HTMLElement | null;
        if (t?.closest('[data-sb-scope="labels-admin-body"]')) return;
        onClose();
      }}
    >
      <div
        ref={wrapRef}
        data-sb-scope="labels-admin-body"
        role="dialog"
        aria-modal="true"
        aria-label="标签管理"
        className="bg-white rounded-xl shadow-lg w-[720px] max-w-full max-h-[88vh] overflow-hidden flex flex-col"
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200">
          <div className="text-base font-semibold">项目标签</div>
          <div className="flex items-center gap-2">
            <button
              onClick={startCreate}
              data-sb-scope="labels-admin-new"
              className="h-[30px] px-3 inline-flex items-center gap-1.5 bg-brand-500 text-white rounded-md text-[13px] font-medium hover:bg-brand-600"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
              新建标签
            </button>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900"
            >✕</button>
          </div>
        </div>

        {/* 列表区 */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading ? (
            <ul className="flex flex-col gap-2" aria-busy="true" aria-label="加载标签中">
              {[0, 1, 2].map((i) => (
                <li key={i} className="h-[42px] rounded-md bg-neutral-100 animate-pulse" />
              ))}
            </ul>
          ) : sorted.length === 0 && !creating ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-neutral-500">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><path d="M20.59 13.41 13.41 20.59a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/></svg>
              <div className="text-[14px] font-semibold text-neutral-700">还没有标签</div>
              <div className="text-[13px]">点击右上「＋ 新建标签」开始</div>
            </div>
          ) : (
            <ul role="list" aria-label="项目标签" className="flex flex-col gap-1">
              {creating && (
                <li
                  data-sb-scope="labels-admin-row"
                  className="px-3 py-2 rounded-md border border-brand-200 bg-brand-50/30 space-y-2"
                >
                  {/* Row 1: 名称 + hex + 预览色点 —— 紧凑一组 */}
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full shrink-0" style={{ background: draftColor }} aria-hidden="true" />
                    <input
                      autoFocus
                      aria-label="标签名称"
                      placeholder="名称"
                      value={draftName}
                      onChange={(e) => setDraftName(e.target.value)}
                      className="h-7 border border-neutral-300 rounded-md px-2 text-[13px] w-[140px] focus:outline-none focus:border-brand-500"
                    />
                    <input
                      aria-label="颜色 hex"
                      value={draftColor}
                      onChange={(e) => setDraftColor(e.target.value)}
                      className="h-7 border border-neutral-300 rounded-md px-2 text-[13px] font-mono w-[88px] focus:outline-none focus:border-brand-500"
                      placeholder="#RRGGBB"
                    />
                    <div className="ml-auto flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={() => { setEditingId(null); setCreating(false); setDraftName(""); }}
                        className="h-7 px-3 text-[12px] text-neutral-600 hover:bg-neutral-100 rounded-md whitespace-nowrap"
                      >取消</button>
                      <button
                        type="button"
                        onClick={saveDraft}
                        className="h-7 px-3 text-[12px] bg-brand-500 text-white rounded-md hover:bg-brand-600 whitespace-nowrap"
                      >保存</button>
                    </div>
                  </div>
                  {/* Row 2: 12 预设色单独占满宽，flex-wrap 自动换 */}
                  <div className="flex flex-wrap gap-1" role="group" aria-label="预设颜色">
                    {PRESET_COLORS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        aria-label={`颜色 ${c}`}
                        aria-pressed={c === draftColor}
                        onClick={() => setDraftColor(c)}
                        className={`w-5 h-5 rounded-full border shrink-0 ${c === draftColor ? "ring-2 ring-brand-500 border-white" : "border-neutral-200"}`}
                        style={{ background: c }}
                      />
                    ))}
                  </div>
                </li>
              )}
              {sorted.map((row) => {
                const inactive = !row.is_active;
                const isEditing = editingId === row.id || (creating && row.id === "__new__");
                return (
                  <li
                    key={row.id}
                    data-sb-scope="labels-admin-row"
                    className={`flex items-center gap-3 px-3 h-[42px] rounded-md border border-transparent hover:border-neutral-200 ${inactive ? "opacity-60 bg-neutral-50" : ""}`}
                  >
                    {isEditing ? (
                      <>
                        <span className="w-3 h-3 rounded-full shrink-0" style={{ background: draftColor }} aria-hidden="true" />
                        <input
                          autoFocus
                          aria-label="标签名称"
                          value={draftName}
                          onChange={(e) => setDraftName(e.target.value)}
                          className="h-7 border border-neutral-300 rounded-md px-2 text-[13px] w-[180px] focus:outline-none focus:border-brand-500"
                        />
                        <input
                          aria-label="颜色 hex"
                          value={draftColor}
                          onChange={(e) => setDraftColor(e.target.value)}
                          className="h-7 border border-neutral-300 rounded-md px-2 text-[13px] font-mono w-[88px] focus:outline-none focus:border-brand-500"
                          placeholder="#RRGGBB"
                        />
                        <div className="flex gap-1" role="group" aria-label="预设颜色">
                          {PRESET_COLORS.map((c) => (
                            <button
                              key={c}
                              type="button"
                              aria-label={`颜色 ${c}`}
                              aria-pressed={c === draftColor}
                              onClick={() => setDraftColor(c)}
                              className={`w-5 h-5 rounded-full border ${c === draftColor ? "ring-2 ring-brand-500 border-white" : "border-neutral-200"}`}
                              style={{ background: c }}
                            />
                          ))}
                        </div>
                        <div className="ml-auto flex items-center gap-1.5">
                          <button
                            onClick={() => { setEditingId(null); setCreating(false); setDraftName(""); }}
                            className="h-7 px-2.5 text-[12px] text-neutral-600 hover:bg-neutral-100 rounded-md"
                          >取消</button>
                          <button
                            onClick={saveDraft}
                            className="h-7 px-2.5 text-[12px] bg-brand-500 text-white rounded-md hover:bg-brand-600"
                          >保存</button>
                        </div>
                      </>
                    ) : (
                      <>
                        <span className="w-3 h-3 rounded-full shrink-0" style={{ background: row.color }} aria-hidden="true" />
                        <span className="text-[13px] font-medium">{row.name}</span>
                        <span className="text-[12px] font-mono text-neutral-400">{row.color}</span>
                        <span className="text-[12px] text-neutral-500">
                          {inactive ? `已停用 · 被 ${row.usage_count} 个任务引用` : `被 ${row.usage_count} 个任务使用`}
                        </span>
                        <div className="ml-auto flex items-center gap-1.5">
                          {inactive ? (
                            <button
                              onClick={() => restoreLabel(row)}
                              data-sb-scope="labels-admin-restore"
                              aria-label={`恢复 ${row.name}`}
                              className="h-7 px-2 text-[12px] text-brand-600 hover:bg-brand-50 rounded-md"
                            >↺ 恢复</button>
                          ) : (
                            <button
                              onClick={() => startEdit(row)}
                              data-sb-scope="labels-admin-edit"
                              aria-label={`编辑 ${row.name}`}
                              className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:bg-neutral-100 rounded-md"
                            >
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="m18.5 2.5 3 3L12 15l-4 1 1-4z"/></svg>
                            </button>
                          )}
                          <button
                            onClick={() => inactive ? restoreLabel(row) : onDelete(row)}
                            data-sb-scope="labels-admin-del"
                            aria-label={`删除 ${row.name}`}
                            className="w-7 h-7 flex items-center justify-center text-red-500 hover:bg-red-50 rounded-md"
                          >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 6h18"/><path d="m19 6-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
                          </button>
                        </div>
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      {/* 被引用 → 停用确认弹窗（C.26 「停用」二次确认） */}
      {confirmDelete && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-[60]"
          role="alertdialog"
          aria-modal="true"
          aria-label="停用标签"
          onMouseDown={(e) => {
            const t = e.target as HTMLElement | null;
            if (t?.closest('[data-sb-scope="labels-admin-stop-confirm"]')) return;
            setConfirmDelete(null);
          }}
        >
          <div
            data-sb-scope="labels-admin-stop-confirm"
            className="bg-white rounded-xl shadow-lg w-[400px] max-w-full p-6"
          >
            <div className="text-base font-semibold mb-2">停用标签「{confirmDelete.name}」？</div>
            <div className="text-[13px] text-neutral-600 mb-5">
              该标签被 <b>{confirmDelete.usage_count}</b> 个任务引用。停用后这些任务保留该标签引用，但列表展示灰显。
            </div>
            <div className="flex justify-end gap-2.5">
              <button onClick={() => setConfirmDelete(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
              <button onClick={() => confirmSoftDelete(confirmDelete)} className="h-[34px] px-3.5 bg-amber-500 text-white rounded-md hover:bg-amber-600">停用</button>
            </div>
          </div>
        </div>
      )}

      {/* 强制删除确认（N>0 时仍想物理删） */}
      {confirmForce && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-[60]"
          role="alertdialog"
          aria-modal="true"
          aria-label="强制删除标签"
          onMouseDown={(e) => {
            const t = e.target as HTMLElement | null;
            if (t?.closest('[data-sb-scope="labels-admin-force-confirm"]')) return;
            setConfirmForce(null);
          }}
        >
          <div
            data-sb-scope="labels-admin-force-confirm"
            className="bg-white rounded-xl shadow-lg w-[440px] max-w-full p-6"
          >
            <div className="text-base font-semibold mb-2 text-red-600">强制删除「{confirmForce.name}」？</div>
            <div className="text-[13px] text-neutral-600 mb-5">
              将从 <b className="text-red-600">{confirmForce.usage_count}</b> 个任务摘除该标签。此操作不可撤销。
            </div>
            <div className="flex justify-end gap-2.5">
              <button onClick={() => setConfirmForce(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
              <button onClick={forceDelete} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">强制删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 调试路由壳：访问 /labels-admin?slug=…&projectId=… 直接打开 modal。
 *  生产里此入口由列表 / 看板筛选条「标签」下拉尾部「管理标签」触发，
 *  在 public layout 下挂路由，便于 e2e 直访。 */
export default function LabelsAdminRoute() {
  const [sp] = useSearchParams();
  const slug = sp.get("slug") ?? "";
  const projectId = sp.get("projectId") ?? "";
  if (!slug || !projectId) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-50">
        <div className="bg-white p-6 rounded-xl shadow text-[13px] text-neutral-600">
          缺少参数：?slug=…&projectId=…
        </div>
      </div>
    );
  }
  return <LabelsAdminModal workspaceSlug={slug} projectId={projectId} onClose={() => history.back()} />;
}