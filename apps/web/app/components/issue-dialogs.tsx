import { useEffect, useRef, useState } from "react";
import {
  AssigneeAPI,
  IssueAPI,
  ProjectMemberAPI,
  RelationAPI,
  WorkLogAPI,
  unwrap,
  type ProjectMember,
  type RelationType,
} from "../services/api";
import type { ApiError } from "../services/axios";
import { toast } from "./Toast";

/** Sprint-2 抽屉/看板共用弹层（TASK-005~009 §3；视觉与逐字文案 = 冻结原型 M-*）：
 *  - M-LINK   AddRelationModal        C.43 添加关联（类型下拉 + 目标搜索 + 语义提示 + 409 环红条）
 *  - M-WORKLOG WorkLogDialog          C.47 工时填报/编辑两态（chips + 30 天窗口 + ⌘ 保存并再开）
 *  - M-ASSIGN AssigneePickerModal     C.50 转交弹层（n/10 + chips + 转交说明 + 通知预览）
 *  - M-DUP    DuplicateDialog         C.57 复制选项（五选项 + 副本预览 + 固定信息条）
 *  - M-ARCH   ArchiveConfirmDialog    C.58 归档确认 + 10s 撤销 Toast
 *  - M-BLOCKED BlockedCompleteDialog  C.44 完成被拦截（阻塞列表 + 管理员强制完成展开）
 *
 *  弹层统一 absolute 覆盖层（fixed inset-0）；键盘 Esc 关闭；外层不再包 dialog 嵌套 button。 */

const STATE_COLOR: Record<string, string> = {
  unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171", backlog: "#a1a1aa",
};

/** C.43 类型下拉四项（图标 + 中文名 + 代码角标；TASK-005 §3.2 固定）。 */
export const REL_TYPE_META: Array<{ t: RelationType; icon: string; name: string; code: string }> = [
  { t: "blocks", icon: "⚡", name: "阻塞了…", code: "blocks" },
  { t: "is_blocked_by", icon: "⛔", name: "被…阻塞", code: "is_blocked_by" },
  { t: "relates_to", icon: "🔗", name: "相关于…", code: "relates_to" },
  { t: "duplicates", icon: "👥", name: "重复于…", code: "duplicates" },
];
export const REL_TYPE_LABEL: Record<RelationType, string> = {
  blocks: "阻塞了…", is_blocked_by: "被…阻塞", relates_to: "相关于…", duplicates: "重复于…",
};

function ModalShell({ title, onClose, children, width = "w-[520px]", alertLike = false, labelledBy }: {
  title: React.ReactNode; onClose: () => void; children: React.ReactNode; width?: string; alertLike?: boolean; labelledBy?: string;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-[2px] flex items-center justify-center p-4 z-[90]">
      <div
        className={`bg-white rounded-xl shadow-lg ${width} max-w-full max-h-[88vh] overflow-y-auto p-6 ${alertLike ? "border-l-4 border-amber-500" : ""}`}
        role={alertLike ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        aria-labelledby={labelledBy}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold" id={labelledBy} data-sb-scope="modal-title">{title}</div>
          <button aria-label="关闭" onClick={onClose} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ═══════════ M-LINK 添加关联弹层（C.43 / TASK-005 §3.2）═══════════ */

export function AddRelationModal({ slug, projectId, issueId, issueName, excludedIds, onClose, onCreated }: {
  slug: string; projectId: string; issueId: string; issueName: string;
  /** 已关联目标（搜索行灰字「已关联」且不可选，C.43 条件态） */
  excludedIds: Set<string>;
  onClose: () => void; onCreated: () => void;
}) {
  const [typeOpen, setTypeOpen] = useState(false);
  const [relType, setRelType] = useState<RelationType>("blocks");
  const [target, setTarget] = useState<{ id: string; issue_key: string; name: string; state_group: string } | null>(null);
  const [kw, setKw] = useState("");
  const [candidates, setCandidates] = useState<Array<{ id: string; issue_key: string; name: string; state_group: string }>>([]);
  const [submitting, setSubmitting] = useState(false);
  const [cycleErr, setCycleErr] = useState<string | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 防抖 300ms 目标搜索（排除自身；行显状态圆点）
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      IssueAPI.list(slug, projectId, { q: kw.trim(), per_page: 20 })
        .then((r) => {
          const rows = (unwrap<Array<{ id: string; issue_key: string; name: string; state_group: string }>>(r)) ?? [];
          setCandidates(rows.filter((x) => x.id !== issueId));
        })
        .catch(() => setCandidates([]));
    }, 300);
    return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
  }, [kw, slug, projectId, issueId]);

  const meta = REL_TYPE_META.find((m) => m.t === relType)!;
  const blocking = relType === "blocks" || relType === "is_blocked_by";

  async function submit() {
    if (!target || submitting) return;
    setSubmitting(true); setCycleErr(null);
    try {
      await RelationAPI.create(slug, projectId, issueId, { related_issue_id: target.id, relation_type: relType });
      toast(`已创建关联 ${issueName && target ? `${target.issue_key}` : ""}`.trim(), "ok");
      onCreated(); onClose();
    } catch (e: unknown) {
      const err = e as ApiError;
      if (err?.code === "RESOURCE_CIRCULAR_DEPENDENCY") {
        // C.43：409 环路径 → 弹层内红条完整依赖链（details[0].message 直出）
        setCycleErr(err.details?.[0]?.message ?? err.message ?? "该依赖会构成循环依赖");
      } else if (err?.code === "RESOURCE_ALREADY_EXISTS") {
        setCycleErr(err.details?.[0]?.message ?? "两个任务之间已存在该关联");
      } else {
        toast(err?.message ?? "创建关联失败", "error");
      }
    } finally { setSubmitting(false); }
  }

  return (
    <ModalShell title="添加关联" onClose={onClose} labelledBy="link-modal-title">
      {/* C.43 类型下拉：四项固定（图标+中文名+代码角标） */}
      <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">关系类型</span>
      <div className="relative" data-sb-scope="link-type-dd">
        <button
          className="w-full h-9 inline-flex items-center justify-between border border-neutral-300 rounded-md px-2.5 text-[13px] hover:bg-neutral-50"
          onClick={() => setTypeOpen((v) => !v)}
          aria-haspopup="menu"
          aria-label="关系类型"
          data-sb-scope="link-type-btn"
        >
          <span>{meta.icon} {meta.name}（{meta.code}）</span>
          <span className="text-neutral-400" aria-hidden="true">▾</span>
        </button>
        {typeOpen && (
          <div role="menu" className="absolute left-0 right-0 top-10 z-10 bg-white border border-neutral-200 rounded-lg shadow-lg py-1" data-sb-scope="link-type-menu">
            {REL_TYPE_META.map((m) => (
              <button key={m.t} role="menuitem" data-sb-scope="link-type-item" data-rel-type={m.t}
                onClick={() => { setRelType(m.t); setTypeOpen(false); }}
                className={`w-full text-left px-3 h-9 text-[13px] hover:bg-neutral-50 flex items-center gap-2 ${m.t === relType ? "bg-brand-50 text-brand-600 font-medium" : ""}`}>
                <span aria-hidden="true">{m.icon}</span>{m.name}
                <span className="ml-auto font-mono text-[11px] text-neutral-400">{m.code}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* C.43 目标搜索：防抖 300ms + 排除自身 + 行显状态圆点 */}
      <div className="mt-3.5">
        <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">目标任务</span>
        <input
          className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px] focus:outline-none focus:border-brand-500"
          placeholder="🔍 搜索任务标题或编号…"
          aria-label="搜索目标任务"
          data-sb-scope="link-search"
          value={kw}
          onChange={(e) => setKw(e.target.value)}
        />
        <div className="mt-1.5 border border-neutral-200 rounded-lg max-h-[200px] overflow-y-auto" role="listbox" aria-label="候选任务" data-sb-scope="link-results">
          {candidates.length === 0 ? (
            <div className="px-3.5 py-3.5 text-center text-[13px] text-neutral-400">未找到匹配任务，换个关键词</div>
          ) : candidates.map((c) => {
            const linked = excludedIds.has(c.id);
            return (
              <div
                key={c.id}
                role="option"
                aria-selected={target?.id === c.id}
                aria-disabled={linked}
                data-sb-scope="link-candidate"
                data-candidate-id={c.id}
                onClick={() => { if (!linked) setTarget(c); }}
                className={`flex items-center gap-2 px-2.5 py-[7px] text-[13px] ${linked ? "text-neutral-400 cursor-not-allowed" : "cursor-pointer hover:bg-neutral-50"} ${target?.id === c.id ? "bg-brand-50" : ""}`}
              >
                {linked && <span className="text-[12px] text-neutral-400">已关联</span>}
                <span className="font-mono text-[12px] text-neutral-400">{c.issue_key}</span>
                <span className="flex-1 truncate">{c.name}</span>
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: STATE_COLOR[c.state_group] ?? "#9ca3af" }} aria-label={`状态 ${c.state_group}`} />
              </div>
            );
          })}
        </div>
      </div>

      {/* C.43 语义提示行：blocks/is_blocked_by → 阻断流转；其余 → 仅建立关联 */}
      <div className="mt-3.5 flex items-start gap-2 rounded-lg bg-brand-50 text-brand-700 px-3 py-2 text-[12.5px]" data-sb-scope="link-sem-tip">
        <span aria-hidden="true">ⓘ</span>
        <span><b>选择「{meta.name}」：</b>{blocking ? "目标完成前，当前任务将无法流转到已完成。" : "仅建立关联，不阻塞流转。"}</span>
      </div>

      {/* C.43 409 环：弹层内红条完整依赖链 */}
      {cycleErr && (
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 px-3 py-2 text-[12.5px]" role="alert" data-sb-scope="link-cycle-err">
          <span aria-hidden="true">⛔</span>
          <span>存在循环依赖：{cycleErr}</span>
        </div>
      )}

      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
        <button
          onClick={() => void submit()}
          disabled={!target || submitting}
          data-sb-scope="link-submit"
          className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed"
        >创建关联</button>
      </div>
    </ModalShell>
  );
}

/* ═══════════ M-WORKLOG 工时填报弹层（C.47 / TASK-006 §3.2；填报/编辑两态复用）═══════════ */

export function fmtMinutes(min: number | null | undefined): string {
  if (min == null) return "—";
  if (min < 60) return `${min}m`;
  if (min < 480) return `${+(min / 60).toFixed(1)}h`;
  const d = Math.floor(min / 480);
  const h = +((min % 480) / 60).toFixed(1);
  return h ? `${d}d ${h}h` : `${d}d 0h`; // T006 §4.4.2：1d=8h
}

const WL_CHIPS = [30, 60, 120, 240, 480];
const WL_DURATIONS = [30, 60, 120, 240, 480, 960, 1440];

export function WorkLogDialog({ slug, projectId, issueId, issueName, edit, onClose, onSaved }: {
  slug: string; projectId: string; issueId: string; issueName: string;
  /** 编辑态预填原值（对新值重校验窗口）；null = 填报态 */
  edit: { id: string; minutes: number; worked_on: string; note: string } | null;
  onClose: () => void; onSaved: (spentMinutes: number | null) => void;
}) {
  const [minutes, setMinutes] = useState(edit?.minutes ?? 120);
  const [customMin, setCustomMin] = useState("");
  const [date, setDate] = useState(edit?.worked_on ?? new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState(edit?.note ?? "");
  const [saving, setSaving] = useState(false);
  const todayStr = new Date().toISOString().slice(0, 10);
  const min30 = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);

  async function save(reopen: boolean) {
    if (saving) return;
    // 客户端预校验（后端 400 INVALID_DATE 兜底同文案口径）
    if (date > todayStr || date < min30) { toast("日期超出可补填窗口（30 天）", "error"); return; }
    if (!Number.isFinite(minutes) || minutes <= 0) { toast("时长必须为正整数分钟", "error"); return; }
    setSaving(true);
    try {
      const r = edit
        ? await WorkLogAPI.patch(slug, projectId, issueId, edit.id, { minutes, worked_on: date, note })
        : await WorkLogAPI.create(slug, projectId, issueId, { minutes, worked_on: date, note });
      const row = unwrap<{ issue_spent_minutes?: number }>(r);
      toast(`已记录 ${fmtMinutes(minutes)}（${date}）`, "ok", { ttl: 4200 });
      onSaved(row?.issue_spent_minutes ?? null);
      if (reopen) { setMinutes(120); setNote(""); setCustomMin(""); return; } // 保存并再开：清空时长保留日期
      onClose();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "保存失败", "error");
    } finally { setSaving(false); }
  }

  return (
    <ModalShell title={`记工时 · ${issueName}`} onClose={onClose} width="w-[480px]" labelledBy="wl-modal-title">
      {/* C.47 时长下拉 0.5h 步进 + 自定义分钟输入；快捷 chips 选中即填 */}
      <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">时长</span>
      <div className="flex items-center gap-2">
        <select
          className="h-9 border border-neutral-300 rounded-md px-2 text-[13px] font-mono bg-white"
          aria-label="时长"
          data-sb-scope="wl-duration"
          value={WL_DURATIONS.includes(minutes) ? String(minutes) : "custom"}
          onChange={(e) => {
            if (e.target.value !== "custom") setMinutes(Number(e.target.value));
            else setCustomMin(String(minutes));
          }}
        >
          {WL_DURATIONS.map((m) => <option key={m} value={m}>{fmtMinutes(m)}</option>)}
          <option value="custom">自定义…</option>
        </select>
        {!WL_DURATIONS.includes(minutes) && (
          <input
            type="number" inputMode="numeric" min={1}
            className="w-[110px] h-9 border border-neutral-300 rounded-md px-2 text-[13px] font-mono"
            aria-label="自定义时长（分钟）"
            data-sb-scope="wl-custom-min"
            value={customMin}
            onChange={(e) => { setCustomMin(e.target.value); const v = Number(e.target.value); if (v > 0) setMinutes(v); }}
          />
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {WL_CHIPS.map((m) => (
          <button
            key={m}
            data-sb-scope="wl-chip"
            onClick={() => setMinutes(m)}
            className={`h-[26px] px-2.5 rounded-full border text-[12px] ${minutes === m ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300 text-neutral-600 hover:border-brand-400 hover:text-brand-600"}`}
          >{fmtMinutes(m)}</button>
        ))}
      </div>

      {/* C.47 日期：max=today / min=today-30d / 默认今天 */}
      <div className="mt-3.5">
        <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">日期</span>
        <input
          type="date" className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px] font-mono"
          aria-label="日期" data-sb-scope="wl-date" value={date} max={todayStr} min={min30}
          onChange={(e) => setDate(e.target.value)}
        />
        <div className="text-[12px] text-neutral-400 mt-1">可补填最近 30 天</div>
      </div>

      {/* C.47 备注（可选） */}
      <div className="mt-3.5">
        <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">备注（可选）</span>
        <textarea
          rows={2} className="w-full border border-neutral-300 rounded-md p-2 text-[13px] focus:outline-none focus:border-brand-500"
          placeholder="做了什么…" aria-label="备注" data-sb-scope="wl-note" value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>

      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
        <button
          onClick={(e) => void save(e.metaKey || e.ctrlKey)}
          disabled={saving}
          data-sb-scope="wl-save"
          className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50"
        >保存</button>
      </div>
      {/* C.47：⌘+点击保存 = 保存并再开（清空时长保留日期）；⌘/Ctrl+Enter 提交 */}
      <div className="text-center text-[12px] text-neutral-400 mt-2.5" data-sb-scope="wl-hint">⌘ + 点击保存 = 保存并再开 · ⌘/Ctrl + Enter 提交</div>
      <SaveHotkey onTrigger={() => void save(false)} />
    </ModalShell>
  );
}

/** ⌘/Ctrl+Enter 全局提交（弹层挂载期间生效）。 */
function SaveHotkey({ onTrigger }: { onTrigger: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onTrigger(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onTrigger]);
  return null;
}

/* ═══════════ M-ASSIGN 转交弹层 AssigneePicker（C.50 / TASK-007 §3.2）═══════════ */

export const MAX_ASSIGNEES = 10;

export function AssigneePickerModal({ slug, projectId, issueId, issueName, currentIds, myUserId, onClose, onSaved }: {
  slug: string; projectId: string; issueId: string; issueName: string;
  currentIds: string[]; myUserId: string | null;
  onClose: () => void; onSaved: () => void;
}) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [selected, setSelected] = useState<string[]>(currentIds);
  const [kw, setKw] = useState("");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      ProjectMemberAPI.list(slug, projectId, { per_page: 100 })
        .then((r) => setMembers(unwrap<ProjectMember[]>(r) ?? []))
        .catch(() => {});
    }, 0);
    return () => clearTimeout(t);
  }, [slug, projectId]);

  const filtered = members.filter((m) => !kw.trim() || m.user.display_name.includes(kw.trim()) || m.user.email.includes(kw.trim()));
  const added = selected.filter((id) => !currentIds.includes(id)).length;
  const removed = currentIds.filter((id) => !selected.includes(id)).length;
  const nameOf = (id: string) => members.find((m) => m.user.id === id)?.user.display_name ?? "…";

  function toggle(id: string) {
    setSelected((cur) => {
      if (cur.includes(id)) return cur.filter((x) => x !== id);
      if (cur.length >= MAX_ASSIGNEES) {
        toast(`执行人已达上限 ${MAX_ASSIGNEES} 人`, "error");
        return cur;
      }
      return [...cur, id];
    });
  }

  async function save() {
    if (saving) return;
    setSaving(true);
    const payload: { assignee_ids: string[]; comment?: string } = { assignee_ids: selected };
    if (comment.trim()) payload.comment = comment.trim();
    try {
      await AssigneeAPI.put(slug, projectId, issueId, payload);
      toast(`执行人已更新（${selected.length} 人）`, "ok");
      onSaved(); onClose();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "转交失败", "error");
    } finally { setSaving(false); }
  }

  return (
    <ModalShell title={`修改执行人 · ${issueName}`} onClose={onClose} labelledBy="asg-modal-title">
      <input
        className="w-full h-8 border border-neutral-300 rounded-md px-2.5 text-[13px] focus:outline-none focus:border-brand-500"
        placeholder="🔍 搜索成员…" aria-label="搜索成员" data-sb-scope="asg-search"
        value={kw} onChange={(e) => setKw(e.target.value)}
      />
      {/* C.50 成员列表：CONTRIBUTOR+ 可勾选；COMMENTER/VIEWER 灰显标注不可指派；自己带（我） */}
      <div className="mt-2 border border-neutral-200 rounded-lg max-h-[240px] overflow-y-auto" role="group" aria-label="候选成员" data-sb-scope="asg-list">
        {members.length === 0 ? (
          Array.from({ length: 5 }, (_, i) => <div key={i} className="h-9 mx-2 my-1 rounded animate-pulse bg-neutral-100" />)
        ) : filtered.map((m) => {
          const notAssignable = m.role < 15; // ProjectRole.CONTRIBUTOR=15 以下（COMMENTER/VIEWER）不可指派
          const checked = selected.includes(m.user.id);
          const disabled = notAssignable || (!checked && selected.length >= MAX_ASSIGNEES);
          return (
            <label key={m.id} className={`flex items-center gap-2 px-2.5 py-1.5 text-[13px] ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer hover:bg-neutral-50"}`}>
              <input
                type="checkbox" className="accent-brand-500 w-[15px] h-[15px]" checked={checked} disabled={disabled}
                onChange={() => toggle(m.user.id)}
                aria-label={m.user.display_name}
              />
              <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center shrink-0"
                style={{ background: "#3b82f6" }} aria-hidden="true">{Array.from(m.user.display_name)[0]}</span>
              <span className="flex-1 truncate">
                {m.user.display_name}{m.user.id === myUserId ? "（我）" : ""}
                {notAssignable && <span className="text-neutral-400 text-[12px]">（{m.role === 10 ? "评论者" : "查看者"}，不可指派）</span>}
              </span>
              <span className="text-[12px] text-neutral-400 font-mono">{m.user.email}</span>
            </label>
          );
        })}
      </div>
      {/* C.50 计数 n/10 + 已选 chips（顺序即提交顺序，× 移除） */}
      <div className="mt-3">
        <span className="text-[12px] text-neutral-400" data-sb-scope="asg-count">已选 {selected.length}/{MAX_ASSIGNEES}：</span>
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          {selected.map((id) => (
            <span key={id} className="inline-flex items-center gap-1 rounded-full bg-brand-50 text-brand-700 px-2.5 py-0.5 text-[12px]" data-sb-scope="asg-chip">
              {nameOf(id)}
              <button aria-label={`移除 ${nameOf(id)}`} onClick={() => toggle(id)} className="hover:bg-brand-100 rounded-full w-3.5 h-3.5 inline-flex items-center justify-center">✕</button>
            </span>
          ))}
        </div>
      </div>
      {/* C.50 转交说明（可选，将随通知发送）maxLength 500 */}
      <div className="mt-3">
        <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">转交说明（可选，将随通知发送）</span>
        <textarea
          rows={2} maxLength={500} className="w-full border border-neutral-300 rounded-md p-2 text-[13px] focus:outline-none focus:border-brand-500"
          placeholder="联调窗口改到周四，请两位对接…" aria-label="转交说明" data-sb-scope="asg-comment"
          value={comment} onChange={(e) => setComment(e.target.value)}
        />
      </div>
      {/* C.50 通知预览灰字 aria-live=polite */}
      <div className="text-[12px] text-neutral-400 mt-2" aria-live="polite" data-sb-scope="asg-preview">
        {added || removed ? `将通知：${added ? `新增 ${added} 人` : ""}${added && removed ? "、" : ""}${removed ? `移除 ${removed} 人` : ""}` : ""}
      </div>
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
        <button onClick={() => void save()} disabled={saving} data-sb-scope="asg-save"
          className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50"
        >保存修改</button>
      </div>
    </ModalShell>
  );
}

/* ═══════════ M-DUP 复制选项弹层（C.57 / TASK-009 §3.1）═══════════ */

export interface DupOptions {
  sub: boolean; asg: boolean; labels: boolean; cf: boolean; dates: boolean;
}
export const DUP_DEFAULT: DupOptions = { sub: true, asg: false, labels: true, cf: true, dates: false };

export function DuplicateDialog({ slug, projectId, issueId, issue, subCount, onClose, onCreated }: {
  slug: string; projectId: string; issueId: string;
  issue: { issue_key: string; name: string; assigneeIds: string[]; labelCount: number; cfCount: number; start: string | null; target: string | null };
  subCount: number;
  onClose: () => void; onCreated: (newId: string, newKey: string) => void;
}) {
  const [opt, setOpt] = useState<DupOptions>({ ...DUP_DEFAULT });
  const [submitting, setSubmitting] = useState(false);

  const rows: Array<{ k: keyof DupOptions; label: string; val: string }> = [
    { k: "sub", label: "包含子任务", val: subCount ? `${subCount} 个，将一并复制结构` : "无子任务" },
    { k: "asg", label: "包含执行人", val: issue.assigneeIds.length ? `${issue.assigneeIds.length} 人` : "无" },
    { k: "labels", label: "包含标签", val: issue.labelCount ? `${issue.labelCount} 个` : "无" },
    { k: "cf", label: "包含自定义字段值", val: issue.cfCount ? `${issue.cfCount} 项` : "无" },
    { k: "dates", label: "包含起止日期", val: `${issue.start ?? "—"} ~ ${issue.target ?? "—"}` },
  ];
  // C.57 副本预览：随选项实时更新
  const preview = `副本预览：${issue.name} (副本)${opt.sub && subCount ? ` + ${subCount} 个子任务` : ""}`;

  async function go() {
    if (submitting) return;
    setSubmitting(true);
    try {
      const r = await IssueAPI.duplicate(slug, projectId, issueId, {
        include_subtrees: opt.sub, include_assignees: opt.asg, include_labels: opt.labels,
        include_custom_fields: opt.cf, include_dates: opt.dates,
      });
      // 响应 data 为扁平 {id, issue_key, name, total_created}（IssueDuplicateView created_response）
      const res = unwrap<{ id: string; issue_key: string; name: string; total_created: number }>(r);
      toast(`已创建 ${res.issue_key} ${res.name}`, "ok");
      onCreated(res.id, res.issue_key);
      onClose();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "复制失败", "error");
    } finally { setSubmitting(false); }
  }

  return (
    <ModalShell title={`复制任务 · ${issue.issue_key} ${issue.name}`} onClose={onClose} width="w-[480px]" labelledBy="dup-modal-title">
      {/* C.57 五选项（默认 子✓执行人✗标签✓字段✓日期✗） */}
      {rows.map((row) => (
        <label key={row.k} className="flex items-center gap-2 py-1.5 text-[13px] cursor-pointer" data-sb-scope="dup-row">
          <input
            type="checkbox" className="accent-brand-500 w-[15px] h-[15px]" checked={opt[row.k]}
            onChange={(e) => setOpt((o) => ({ ...o, [row.k]: e.target.checked }))}
            aria-label={row.label}
          />
          <span>{row.label}</span>
          <span className="ml-auto text-[12px] text-neutral-400">{row.val}</span>
        </label>
      ))}
      {/* C.57 固定信息条 */}
      <div className="mt-3.5 flex items-start gap-2 rounded-lg bg-neutral-100 px-3 py-2 text-[12.5px] text-neutral-500" data-sb-scope="dup-notice">
        <span aria-hidden="true">ⓘ</span>
        <span>新任务将进入「待办」状态，编号重新分配；<b>评论、附件、依赖、工时不会被复制</b>。</span>
      </div>
      <div className="mt-3 text-[12.5px] text-neutral-500 bg-neutral-100 rounded-md px-2.5 py-1.5" data-sb-scope="dup-preview">{preview}</div>
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
        <button onClick={() => void go()} disabled={submitting} data-sb-scope="dup-go"
          className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50"
        >{submitting ? `正在复制 ${opt.sub ? subCount + 1 : 1} 个任务…` : "创建副本"}</button>
      </div>
    </ModalShell>
  );
}

/* ═══════════ M-ARCH 归档确认 + 撤销 Toast（C.58 / TASK-009 §3.2）═══════════ */

export function ArchiveConfirmDialog({ slug, projectId, issueId, issueKey, issueName, descendantCount, onClose, onArchived, onRestored }: {
  slug: string; projectId: string; issueId: string; issueKey: string; issueName: string;
  descendantCount: number;
  onClose: () => void;
  /** 归档成功（详情关闭 + 列表刷新由调用方处理）；restore 撤销成功 */
  onArchived: () => void; onRestored: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  async function go() {
    if (submitting) return;
    setSubmitting(true);
    try {
      const r = await IssueAPI.archive(slug, projectId, issueId);
      const res = unwrap<{ archived_count: number; archived_at: string }>(r);
      onArchived(); onClose();
      // C.58：Toast 带「撤销」按钮（10s 内一键恢复）+ 倒计时可见文本
      toast(`已归档 ${issueKey}${res && res.archived_count > 1 ? `（含 ${res.archived_count - 1} 个子任务）` : ""}`, "warning", {
        ttl: 10_000, countdown: true,
        action: {
          label: "撤销",
          onAction: () => {
            IssueAPI.unarchive(slug, projectId, issueId)
              .then(() => { toast("已恢复归档", "ok"); onRestored(); })
              .catch(() => toast("恢复失败，可在「显示已归档」视图中重试", "error"));
          },
        },
      });
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.message ?? "归档失败", "error");
    } finally { setSubmitting(false); }
  }
  return (
    <ModalShell title={<span className="inline-flex items-center gap-1.5"><span aria-hidden="true">🗄</span> 归档任务？</span>} onClose={onClose} width="w-[400px]" labelledBy="arch-modal-title">
      {/* C.58 确认弹窗：含子任务时提示「将同时归档 N 个子任务」 */}
      <p className="text-[13px] text-neutral-600" data-sb-scope="arch-text">
        将归档 <b>{issueKey} {issueName}</b>
        {descendantCount > 0 ? <>，<b>将同时归档 {descendantCount} 个子任务</b></> : null}。
      </p>
      <p className="text-[12px] text-neutral-400 mt-1.5">归档后任务从列表 / 看板移除，可在「显示已归档」视图中恢复。</p>
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
        <button onClick={() => void go()} disabled={submitting} data-sb-scope="arch-go"
          className="h-[34px] px-3.5 bg-red-500 text-white rounded-md text-[13px] hover:bg-red-600 disabled:opacity-50"
        >归档</button>
      </div>
    </ModalShell>
  );
}

/* ═══════════ M-BLOCKED 完成被拦截对话框（C.44 / TASK-005 §3.3；看板/详情双入口）═══════════ */

export interface BlockerItem { issue_key: string; name: string; id?: string }

export function BlockedCompleteDialog({ issueName, blockers, isAdmin, onClose, onForce, onJump }: {
  issueName: string;
  /** 409 details[]（issue_key + message="key name"）；跳转需要目标 id 时由调用方补充 */
  blockers: BlockerItem[];
  /** 仅 PROJ_ADMIN 可见（与后端 403 同口径） */
  isAdmin: boolean;
  onClose: () => void;
  /** 强制完成（force=true + comment ≥5 字）——由调用方重发原 PATCH */
  onForce: (comment: string) => Promise<void>;
  /** 阻塞项跳转（返回后对话框已关闭、原任务留在原地） */
  onJump?: (b: BlockerItem) => void;
}) {
  const [forceOpen, setForceOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const valid = comment.trim().length >= 5;

  return (
    <ModalShell
      title={<span className="text-amber-700">⛔ 无法完成「{issueName}」</span>}
      onClose={onClose} width="w-[480px]" alertLike labelledBy="blocked-modal-title"
    >
      <p className="text-[13px] text-neutral-600 mb-2.5">以下前置任务尚未完成：</p>
      {/* C.44 阻塞项列表（圆点+编号+标题+跳转→）；aria-describedby 指向本列表 */}
      <div className="flex flex-col gap-1.5" id="blocked-desc" data-sb-scope="blocked-list">
        {blockers.map((b) => (
          <div key={b.issue_key} className="flex items-center gap-2 border border-neutral-200 rounded-lg px-2.5 py-[7px] text-[13px] hover:border-brand-200 hover:bg-brand-50/50" data-sb-scope="blocked-row">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "#f59e0b" }} aria-hidden="true" />
            <span className="font-mono text-[12px] text-neutral-400">{b.issue_key}</span>
            <span className="flex-1 truncate">{b.name}</span>
            {onJump && (
              <button aria-label={`跳转 ${b.issue_key}`} onClick={() => { onClose(); onJump(b); }}
                className="w-[26px] h-[26px] inline-flex items-center justify-center text-neutral-400 hover:text-brand-600">→</button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3.5 flex items-start gap-2 rounded-lg bg-neutral-100 px-3 py-2 text-[12.5px] text-neutral-500">
        <span aria-hidden="true">ⓘ</span>
        <span>已取消（cancelled）的前置任务不会阻塞完成。</span>
      </div>

      {/* C.44 强制完成：仅管理员可见；点击展开必填 comment（≥5 字符）后重发 force=true */}
      {isAdmin && !forceOpen && (
        <button onClick={() => setForceOpen(true)} data-sb-scope="blocked-force-btn"
          className="mt-3 h-[32px] px-3 border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50"
        >强制完成（管理员）</button>
      )}
      {isAdmin && forceOpen && (
        <div className="mt-3.5 rounded-lg bg-neutral-100 p-3" data-sb-scope="blocked-force-box">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">强制完成说明（必填，≥5 字符）</span>
          <textarea
            rows={2} maxLength={200} className="w-full border border-neutral-300 rounded-md p-2 text-[13px] bg-white focus:outline-none focus:border-brand-500"
            placeholder="说明强制完成的理由（将记入动态）…" aria-label="强制完成说明" data-sb-scope="blocked-force-comment"
            value={comment} onChange={(e) => setComment(e.target.value)}
          />
          <div className="flex justify-end mt-2.5">
            <button
              onClick={() => void (async () => { setSubmitting(true); await onForce(comment.trim()); setSubmitting(false); })()}
              disabled={!valid || submitting} data-sb-scope="blocked-force-go"
              className="h-[28px] px-3 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50"
            >确认强制完成</button>
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">我知道了</button>
      </div>
    </ModalShell>
  );
}

/** 409 RESOURCE_TRANSITION_BLOCKED 的 details[] → 阻塞项（issue_key 结构化，message 仅展示不做解析）。 */
export function blockersFromError(err: ApiError | null | undefined): BlockerItem[] {
  return (err?.details ?? [])
    .filter((d) => d.code === "BLOCKED_BY" && typeof d.issue_key === "string")
    .map((d) => ({ issue_key: String(d.issue_key), name: String(d.message ?? "").replace(`${d.issue_key} `, "") || String(d.issue_key) }));
}

/** 头像首字母（Unicode 码点切分，emoji 安全）。 */
export function initialOf(name?: string | null): string {
  const s = (name ?? "").trim();
  if (!s) return "?";
  return Array.from(s)[0] ?? "?";
}

/** 头像堆叠（C.49 24px 抽屉 / C.51 20px 列表·看板；>3 显 +N，可点开全部浮层）。 */
export function AvatarStack({ names, size = 20, onMore, "aria-label": ariaLabel }: { names: string[]; size?: number; onMore?: () => void; "aria-label"?: string }) {
  const shown = names.slice(0, 3);
  const more = names.length - shown.length;
  const px = size === 24 ? 24 : 20;
  const fs = size === 24 ? "text-[11px]" : "text-[10px]";
  return (
    <span className="inline-flex items-center" data-sb-scope="avatar-stack" title={names.join("、")}
      role="group" aria-label={ariaLabel ?? `执行人 ${names.length} 人${names.length ? `：${names.join("、")}` : ""}`}>
      {shown.map((n, i) => (
        <span
          key={n + i}
          className={`${fs} rounded-full text-white font-semibold flex items-center justify-center shrink-0 border-2 border-white`}
          style={{ width: px, height: px, background: ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"][(n.charCodeAt(0) ?? 0) % 5], marginLeft: i === 0 ? 0 : -8 }}
          aria-hidden="true"
        >{Array.from(n)[0]}</span>
      ))}
      {more > 0 && (onMore ? (
        <button onClick={onMore} aria-haspopup="dialog" aria-label={`展开全部执行人（还有 ${more} 人）`}
          className={`${fs} rounded-full bg-neutral-200 text-neutral-500 flex items-center justify-center shrink-0 border-2 border-white`}
          style={{ width: px, height: px, marginLeft: -8 }}
        >+{more}</button>
      ) : (
        <span className={`${fs} rounded-full bg-neutral-200 text-neutral-500 flex items-center justify-center shrink-0 border-2 border-white`}
          style={{ width: px, height: px, marginLeft: -8 }} aria-hidden="true">+{more}</span>
      ))}
    </span>
  );
}
