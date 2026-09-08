import { useEffect, useRef, useState } from "react";
import {
  AssigneeAPI,
  AttachmentAPI,
  FieldAPI,
  IssueAPI,
  IssueTypeAPI,
  LabelAPI,
  ProjectAPI,
  ProjectMemberAPI,
  RelationAPI,
  WorkLogAPI,
  unwrap,
  type ActivityGroup,
  type AttachmentRow,
  type CustomFieldDef,
  type DeleteSubtreeResult,
  type RelationRow,
  type SubtreeData,
  type WorkLogRow,
} from "../services/api";
import type { ApiError } from "../services/axios";
import { useStores } from "../stores";
import { useIssueRemoteFlash } from "../realtime/useIssueRemoteFlash";
import { StateBadge } from "./StateBadge";
import { GuardDialog, isGuardBlocked, type GuardItem } from "./workflow/GuardDialog";
import { WorkflowAPI, type TransitionAvailableItem } from "../services/api";
import { toast } from "./Toast";
import { CommentThreadTab } from "./CommentThread";
import { IssueTreeDrawer } from "./IssueTreeDrawer";
import {
  AddRelationModal,
  ArchiveConfirmDialog,
  AssigneePickerModal,
  AvatarStack,
  BlockedCompleteDialog,
  DuplicateDialog,
  WorkLogDialog,
  blockersFromError,
  fmtMinutes,
  initialOf,
  type BlockerItem,
} from "./issue-dialogs";
import type { Issue } from "@rp/types";

type DrawerTab = "desc" | "comments" | "activity" | "attachments";

/** 优先级五档（Issue.Priority，apps/api/plane/db/models/issue.py）。 */
const PRIORITY_KEYS = ["none", "low", "medium", "high", "urgent"] as const;
const PRIORITY_LABEL: Record<string, string> = { none: "无", low: "低", medium: "中", high: "高", urgent: "紧急" };
const PRIORITY_COLOR: Record<string, string> = {
  none: "#9ca3af", low: "#a1a1aa", medium: "#3b82f6", high: "#f59e0b", urgent: "#ef4444",
};
const STATE_COLOR: Record<string, string> = {
  unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171",
};

/** C.61 日期分区头：今天 / 昨天 / M月d日（created_at 为 ISO 串）。 */
function dayLabelOf(iso: string | null): string {
  if (!iso) return "";
  const d = iso.slice(0, 10);
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const yestTs = Date.now() - 86400_000;
  const yest = new Date(yestTs);
  const yesterday = `${yest.getFullYear()}-${pad(yest.getMonth() + 1)}-${pad(yest.getDate())}`;
  if (d === today) return "今天";
  if (d === yesterday) return "昨天";
  return `${Number(d.slice(5, 7))}月${Number(d.slice(8, 10))}日`;
}

/** C.61 动作摘要兜底（组记录以 comment 为准；无 comment 时按 verb 归纳）。 */
function verbLabel(verb: string): string {
  const m: Record<string, string> = { created: "创建了任务", updated: "更新了任务", archived: "归档了任务", restored: "恢复了任务", duplicated: "由复制创建" };
  return m[verb] ?? `动作：${verb}`;
}

/** C.23 属性行内编辑的下拉容器：占 grid 第 2 列但不占行高（h-0），菜单绝对定位浮在下方。 */
function PropMenu({ children }: { children: React.ReactNode }) {
  return (
    <div className="col-start-2 relative h-0">
      <div role="menu" data-sb-scope="drawer-prop-menu"
        className="absolute top-0 left-0 z-10 min-w-[180px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[240px] overflow-y-auto">
        {children}
      </div>
    </div>
  );
}

function MenuItem({ on, onClick, children }: { on?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" role="menuitem" aria-checked={on} onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 h-8 text-[13px] text-left hover:bg-neutral-50 ${on ? "bg-brand-50 text-brand-600" : ""}`}>
      {children}
    </button>
  );
}

/** C.46 估算下拉：常用值 0.5h/1h/2h/4h/8h/16h/24h + 清除（自定义走「设估算」prompt 简化路径）。 */
function EstimateMenu({ onPick }: { onPick: (minutes: number | null) => void }) {
  const opts = [30, 60, 120, 240, 480, 960, 1440];
  return (
    <div role="menu" data-sb-scope="drawer-est-menu-list"
      className="absolute left-0 top-8 z-10 min-w-[120px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
      {opts.map((m) => (
        <button key={m} role="menuitem" onClick={() => onPick(m)} data-sb-scope="drawer-est-item"
          className="w-full text-left px-3 h-8 text-[13px] font-mono hover:bg-neutral-50">{fmtMinutes(m)}</button>
      ))}
      <div className="h-px bg-neutral-100 my-1" />
      <button role="menuitem" onClick={() => onPick(null)} className="w-full text-left px-3 h-8 text-[13px] text-neutral-400 hover:bg-neutral-50">清除估算</button>
    </div>
  );
}

/** C.55 控件映射（CONTROL_REGISTRY 类型→控件唯一映射，零字段硬编码）：
 *  text/url/email/phone→Input / textarea→多行 / select→色块下拉 / multi_select→多选 chips /
 *  number/currency→数字 / date→日期 / checkbox→开关 / member→成员选择。 */
function CfRow({ field, value, editable, onSave, members }: {
  field: CustomFieldDef;
  value: unknown;
  editable: boolean;
  onSave: (v: unknown) => void;
  members: Array<{ id: string; user: { id: string; display_name: string } }>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value ?? ""));
  const nameWithReq = (
    <>
      {field.name}{field.required && <span className="text-red-500 ml-0.5">*</span>}
      {field.description && <span className="text-neutral-300 ml-1 cursor-help" title={field.description} aria-label={`${field.name} 帮助说明`}>?</span>}
    </>
  );
  const commit = (v: unknown) => { setEditing(false); onSave(v); };

  let control: React.ReactNode;
  if (!editable || !editing) {
    // 展示态（select=色块文本 / member=头像 / date=yyyy-MM-dd / currency=¥ / checkbox=开·关）
    if (field.type === "checkbox") {
      control = <span className={value ? "text-emerald-600" : "text-neutral-400"}>{value ? "已开启" : "已关闭"}</span>;
    } else if (field.type === "select") {
      const opt = field.options.find((o) => o.value === value);
      control = value != null ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-600">
          <span className="w-2 h-2 rounded-sm" style={{ background: opt?.color ?? "#999" }} />{opt?.label ?? String(value)}
        </span>
      ) : <span className="text-neutral-400">—</span>;
    } else if (field.type === "multi_select") {
      const vals = Array.isArray(value) ? value : value != null ? [value] : [];
      control = vals.length ? (
        <span className="inline-flex items-center gap-1.5 flex-wrap">
          {vals.map((v) => {
            const opt = field.options.find((o) => o.value === v);
            return <span key={String(v)} className="inline-flex items-center gap-1 text-[12px] text-neutral-600"><span className="w-2 h-2 rounded-sm" style={{ background: opt?.color ?? "#999" }} />{opt?.label ?? String(v)}</span>;
          })}
        </span>
      ) : <span className="text-neutral-400">—</span>;
    } else if (field.type === "member") {
      const m = members.find((x) => x.user.id === value);
      control = m ? (
        <span className="inline-flex items-center gap-1.5 text-[13px]">
          <span className="w-5 h-5 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center" aria-hidden="true">{initialOf(m.user.display_name)}</span>
          {m.user.display_name}
        </span>
      ) : <span className="text-neutral-400">—</span>;
    } else if (field.type === "currency") {
      control = value != null ? <span className="font-mono text-[13px]">¥{Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</span> : <span className="text-neutral-400">—</span>;
    } else if (field.type === "date") {
      control = value ? <span className="font-mono text-[13px]">{String(value)}</span> : <span className="text-neutral-400">—</span>;
    } else if (field.type === "date_range") {
      // TASK-012 四高级类型控件（补口轮）：区间双日期
      const dr = (value ?? {}) as { start?: string; end?: string };
      control = dr.start || dr.end ? <span className="font-mono text-[13px]">{dr.start ?? "?"} ~ {dr.end ?? "?"}</span> : <span className="text-neutral-400">—</span>;
    } else if (field.type === "cascade") {
      const path = Array.isArray(value) ? (value as string[]) : [];
      const levels = field.cascade_config?.levels ?? [];
      const labels = path.map((v, i) => levels[i]?.options?.find((o) => o.value === v)?.label ?? v);
      control = path.length ? <span className="text-[13px]">{labels.join(" / ")}</span> : <span className="text-neutral-400">—</span>;
    } else if (field.type === "relation") {
      const n = Array.isArray(value) ? (value as unknown[]).length : 0;
      control = n ? <span className="text-[13px] text-brand-600">🔗 {n} 个关联</span> : <span className="text-neutral-400">—</span>;
    } else if (field.type === "attachment") {
      const n = Array.isArray(value) ? (value as unknown[]).length : 0;
      control = n ? <span className="text-[13px]">📎 {n} 个附件</span> : <span className="text-neutral-400">—</span>;
    } else {
      control = value != null && value !== "" ? <span className="text-[13px]">{String(value)}</span> : <span className="text-neutral-400">—</span>;
    }
    // relation/attachment 的值经「关联分区/附件区」管理（§4.3 受限语义），本行只读呈现
    const noInlineEdit = field.type === "relation" || field.type === "attachment";
    control = editable && !noInlineEdit ? (
      <button className="text-left hover:bg-neutral-50 rounded px-1 -mx-1 min-w-0" data-sb-scope="drawer-cf-value"
        onClick={() => { setDraft(String(value ?? "")); setEditing(true); }}>{control}</button>
    ) : <span className="min-w-0">{control}</span>;
  } else {
    // 编辑态（按类型变形）
    if (field.type === "checkbox") {
      control = (
        <select className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]" data-sb-scope="drawer-cf-edit"
          defaultValue={value ? "true" : "false"}
          onChange={(e) => commit(e.target.value === "true")}>
          <option value="true">已开启</option><option value="false">已关闭</option>
        </select>
      );
    } else if (field.type === "select") {
      control = (
        <select className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]" data-sb-scope="drawer-cf-edit"
          defaultValue={value != null ? String(value) : ""}
          onChange={(e) => commit(e.target.value || null)}>
          <option value="">—</option>
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    } else if (field.type === "multi_select") {
      const cur = new Set(Array.isArray(value) ? (value as unknown[]) : []);
      control = (
        <div className="flex flex-col gap-0.5" data-sb-scope="drawer-cf-edit">
          {field.options.map((o) => (
            <label key={o.value} className="flex items-center gap-1.5 text-[12px]">
              <input type="checkbox" className="accent-brand-500 w-3.5 h-3.5" defaultChecked={cur.has(o.value)}
                onChange={(e) => {
                  const next = new Set(cur);
                  if (e.target.checked) next.add(o.value); else next.delete(o.value);
                  commit(next.size ? Array.from(next) : null);
                }} />
              <span className="w-2 h-2 rounded-sm" style={{ background: o.color ?? "#999" }} />{o.label}
            </label>
          ))}
        </div>
      );
    } else if (field.type === "member") {
      control = (
        <select className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]" data-sb-scope="drawer-cf-edit"
          defaultValue={value != null ? String(value) : ""}
          onChange={(e) => commit(e.target.value || null)}>
          <option value="">—</option>
          {members.map((m) => <option key={m.user.id} value={m.user.id}>{m.user.display_name}</option>)}
        </select>
      );
    } else if (field.type === "date") {
      control = <input type="date" className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]" data-sb-scope="drawer-cf-edit"
        defaultValue={value ? String(value) : ""} onBlur={(e) => commit(e.target.value || null)} />;
    } else if (field.type === "number" || field.type === "currency") {
      control = <input type="number" inputMode="decimal" className="h-7 border border-neutral-300 rounded px-1.5 text-[13px] w-[140px]" data-sb-scope="drawer-cf-edit"
        defaultValue={draft} onBlur={(e) => commit(e.target.value === "" ? null : Number(e.target.value))} />;
    } else if (field.type === "textarea") {
      control = <textarea rows={2} className="border border-neutral-300 rounded p-1.5 text-[13px] w-full" data-sb-scope="drawer-cf-edit"
        defaultValue={draft} onBlur={(e) => commit(e.target.value || null)} />;
    } else if (field.type === "date_range") {
      // TASK-012（补口轮）：区间双日期，任一变更即整段提交（start ≤ end 由服务端 BR-06 校验）
      const dr = (value ?? {}) as { start?: string; end?: string };
      control = (
        <span className="flex items-center gap-1" data-sb-scope="drawer-cf-edit">
          <input type="date" aria-label="区间开始" defaultValue={dr.start ?? ""} className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]"
            onChange={(e) => commit({ start: e.target.value || dr.start || "", end: dr.end ?? e.target.value ?? "" })} />
          <span className="text-neutral-400">~</span>
          <input type="date" aria-label="区间结束" defaultValue={dr.end ?? ""} className="h-7 border border-neutral-300 rounded px-1.5 text-[13px]"
            onChange={(e) => commit({ start: dr.start ?? "", end: e.target.value || dr.start || "" })} />
        </span>
      );
    } else if (field.type === "cascade") {
      // TASK-012（补口轮）：逐级联动下拉（子级选项按 parent_value 过滤，BR-04 连续性）
      const path = Array.isArray(value) ? (value as string[]) : [];
      const levels = field.cascade_config?.levels ?? [];
      control = (
        <span className="flex items-center gap-1 flex-wrap" data-sb-scope="drawer-cf-edit">
          {levels.map((lv, i) => (
            <select key={i} defaultValue={path[i] ?? ""} aria-label={`第 ${i + 1} 级`}
              className="h-7 border border-neutral-300 rounded px-1 text-[12px]"
              onChange={(e) => {
                const next = [...path.slice(0, i), e.target.value].filter(Boolean);
                commit(next.length ? next : null);
              }}>
              <option value="">{lv.name ?? `第 ${i + 1} 级`}</option>
              {(lv.options ?? [])
                .filter((o) => i === 0 || o.parent_value == null || o.parent_value === path[i - 1])
                .map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ))}
        </span>
      );
    } else {
      control = <input type="text" className="h-7 border border-neutral-300 rounded px-1.5 text-[13px] w-full" data-sb-scope="drawer-cf-edit"
        defaultValue={draft} onBlur={(e) => commit(e.target.value || null)} />;
    }
  }

  return (
    <div className="grid grid-cols-[80px_1fr] gap-x-2.5 items-center min-h-[34px] py-0.5">
      <span className="text-[13px] text-neutral-400 truncate" title={field.name}>{nameWithReq}</span>
      <span className="min-w-0">{control}</span>
    </div>
  );
}

/** 任务详情抽屉 720px（TASK-001 §3.3 + TASK-002 §3.2/§3.3/§3.6 + COLLAB-001 + FILE-001）。
 *  - Tab 条终态：「描述｜评论｜动态｜附件」四 Tab（ADR-0011 #1/#20）。
 *  - 属性区七行（状态 / 类型 / 优先级 / 负责人 / 标签 / 开始·截止）—— C.23。
 *  - 描述 Tab：标题 + 描述编辑器 + 子任务区 + 元信息 —— C.24。
 *  - 评论 Tab：列表 + 输入框 + @ 补全 + 编辑/删除（C.32 + C.33）。
 *  - 动态 Tab：操作日志时间线（TASK-002 §3.6 / C.25）。
 *  - 附件 Tab：上传区 + 文件行 + 下载/删除（C.31）。
 *  - ⋯ 菜单：复制链接 / 复制编号 / 删除任务。
 *  - 保存反馈「已保存」2s 淡出。
 *
 *  API 解包约定：CLAUDE.md §"测试脚本规范" — 所有响应统一通过 `unwrap<T>(r)` 取 `data`，
 *  不再用 `(r as any).data`。 */
export function IssueDrawer({ issueId, slug, projectId, onClose, onChanged, layer = "z-50", onOpenIssue }: {
  issueId: string; slug: string; projectId: string; onClose: () => void; onChanged?: () => void;
  /** 层级：普通抽屉 z-50；从全屏树点开的节点抽屉要盖住树（z-[60]）→ z-[70]（TASK-004 §3.3） */
  layer?: string;
  /** C.42 关联行跳转 / C.44 阻塞项跳转：不提供则抽屉内自查自开（嵌套换 issueId）。 */
  onOpenIssue?: (issueId: string) => void;
}) {
  const [issue, setIssue] = useState<Issue | null>(null);
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [tab, setTab] = useState<DrawerTab>("desc");
  const [types, setTypes] = useState<Array<{ id: string; name: string; color: string; is_default: boolean; is_active: boolean }>>([]);
  const [labels, setLabels] = useState<Array<{ id: string; name: string; color: string; is_active: boolean }>>([]);
  const [subIssues, setSubIssues] = useState<Array<{ id: string; issue_key: string; name: string; state_group: string; state_name: string }>>([]);
  const [togglingSubId, setTogglingSubId] = useState<string | null>(null);
  /** 项目状态表（含 cancelled）—— 子任务勾选要把 group 翻译成 state_id，后端写侧只收 state_id。 */
  const [states, setStates] = useState<Array<{ id: string; name: string; group: string }>>([]);
  /** 项目成员（C.23 负责人行内编辑的候选池 —— 后端只给 assignee_ids，姓名要自己解析） */
  const [members, setMembers] = useState<Array<{ id: string; user: { id: string; display_name: string; avatar_url: string | null } }>>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  /** 描述编辑器（contentEditable）—— 初始 HTML 由 dangerouslySetInnerHTML 灌入，
   *  之后交给浏览器接管；`__html` 不变时 React 不会回写，因此不会打断输入光标。 */
  const descRef = useRef<HTMLDivElement | null>(null);
  const descDirtyRef = useRef(false);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [uploadPct, setUploadPct] = useState(0);
  const [attachments, setAttachments] = useState<AttachmentRow[]>([]);
  const [newSubName, setNewSubName] = useState("");
  /** TASK-004 §3.3/C.40：全屏树抽屉（分区头「查看全部 N 个 →」打开）；
   *  treeNodeIssueId = 树里点开的节点详情（盖在树之上，关闭回树、树状态保留）。 */
  const [treeOpen, setTreeOpen] = useState(false);
  const [treeNodeIssueId, setTreeNodeIssueId] = useState<string | null>(null);
  const [delDescCount, setDelDescCount] = useState<number | null>(null);
  const subInputRef = useRef<HTMLInputElement | null>(null);
  /** 日期控件的乐观值：先本地回显、失败再回滚（C.23「乐观更新徽章，失败回滚」）。
   *  若直接受控于 issue.*，用户选完日期到 refresh() 返回前这段会被 React 弹回旧值。 */
  const [startDraft, setStartDraft] = useState("");
  const [targetDraft, setTargetDraft] = useState("");
  const [labelsMenuOpen, setLabelsMenuOpen] = useState(false);
  /** C.23 属性行内编辑当前展开的下拉（状态 / 类型 / 优先级 / 负责人） */
  const [propMenu, setPropMenu] = useState<"state" | "type" | "priority" | "assignee" | null>(null);
  /** Sprint-7（WF-001 §3.4）：受控流转入口——available 边按钮 + 守卫对话框。
   *  fallback=true（无工作流）时保持 V1.0 状态下拉（零回归）。
   *  currentLocks：当前状态字段锁（WF-004 §3.2 锁定灰显，available 响应携带）。 */
  const [flowEdges, setFlowEdges] = useState<TransitionAvailableItem[]>([]);
  const [isControlled, setIsControlled] = useState(false);
  const [currentLocks, setCurrentLocks] = useState<Set<string>>(new Set());
  const [guardDlg, setGuardDlg] = useState<{
    transitionId: string; toStateId: string; transitionName: string; failures: GuardItem[];
  } | null>(null);
  const [flowNotice, setFlowNotice] = useState<string | null>(null);
  const [activityHasMore, setActivityHasMore] = useState(false);
  const labelsMenuRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // ── Sprint-2（TASK-005~010）状态 ──
  const stores = useStores();
  /** AUTH-005 §4.5.1 有效项目角色：20=PROJ_ADMIN（强制完成/字段管理）、15+=CONTRIBUTOR（写）。 */
  const myRole = stores.permission.effectiveProjectRole(projectId, slug);
  const isAdmin = myRole >= 20;
  const canWrite = myRole >= 15;
  const myUserId = stores.session.user?.id ?? null;

  /** Sprint-7：执行受控流转（WF-001 execute）——200 迁移 / 202 审批挂起 /
   *  400 守卫拦截 → GuardDialog（WF-004 BR-05 guard_payload 单请求补齐）。 */
  async function runTransition(edge: TransitionAvailableItem) {
    setFlowNotice(null);
    try {
      const r = await WorkflowAPI.execute(slug, projectId, issueId, {
        to_state_id: edge.to_state.id, transition_id: edge.transition_id });
      const st = r.status;
      if (st === 202) {
        setFlowNotice("已发起审批，等待审批人处理");
        void refresh();
        onChanged?.();
        return;
      }
      void refresh();
      onChanged?.();
    } catch (e) {
      // axios 层 reject 的是 friendly ApiError（code/details 顶层字段——services/axios.ts §解包）
      const err = e as ApiError;
      if (err.details && isGuardBlocked(err.code)) {
        setGuardDlg({
          transitionId: edge.transition_id, toStateId: edge.to_state.id,
          transitionName: edge.name, failures: err.details as unknown as GuardItem[] });
      } else {
        setFlowNotice(err.message || "流转失败");
      }
    }
  }

  /** C.42 关联三分区数据（relations_of 创建时间倒序；三组由前端按 relation_type 分组） */
  const [relations, setRelations] = useState<RelationRow[]>([]);
  const [linkOpen, setLinkOpen] = useState(false);
  /** C.46 工时分区：估算/已耗/记录列表；⊕含子任务开关（默认本任务口径） */
  const [worklogs, setWorklogs] = useState<WorkLogRow[]>([]);
  const [wlDialog, setWlDialog] = useState<{ edit: WorkLogRow | null } | null>(null);
  const [wlMenuRow, setWlMenuRow] = useState<string | null>(null);
  const [wlScopeSelf, setWlScopeSelf] = useState(true);
  const [wlSubtreeStats, setWlSubtreeStats] = useState<{ spent: number; est: number } | null>(null);
  const [estMenuOpen, setEstMenuOpen] = useState(false);
  /** C.49 执行人区 + C.50 转交弹层 */
  const [assignOpen, setAssignOpen] = useState(false);
  const [asgMoreOpen, setAsgMoreOpen] = useState(false);
  /** C.55 动态字段折叠区（field-schema / ETag 缓存语义取一次） */
  const [fieldSchema, setFieldSchema] = useState<CustomFieldDef[] | null>(null);
  const [cfError, setCfError] = useState(false);
  const [cfMoreOpen, setCfMoreOpen] = useState(false);
  /** C.57/C.58 复制与归档弹层 */
  const [dupOpen, setDupOpen] = useState(false);
  const [archOpen, setArchOpen] = useState(false);
  const [archDescCount, setArchDescCount] = useState(0);
  /** C.57 复制弹层的子树规模（subtree stats 口径缓存） */
  const [dupSubCount, setDupSubCount] = useState<number | null>(null);
  /** C.44 完成被拦截（详情状态菜单入口） */
  const [blockedDlg, setBlockedDlg] = useState<{ issueName: string; blockers: BlockerItem[]; stateId: string } | null>(null);
  /** C.61 动态 Tab：epoch 组时间线 + 双过滤器 + 按钮式加载更早 */
  const [activityGroups, setActivityGroups] = useState<ActivityGroup[]>([]);
  const [activityCursor, setActivityCursor] = useState<string | null>(null);
  const [actFieldFilter, setActFieldFilter] = useState("");
  const [actActorFilter, setActActorFilter] = useState("");
  const [actFieldMenu, setActFieldMenu] = useState(false);
  const [actActorMenu, setActActorMenu] = useState(false);

  function refresh() {
    return IssueAPI.detail(slug, projectId, issueId).then((r) => {
      setIssue(unwrap<Issue>(r));
    });
  }
  // ── Sprint-3 Phase 3-C（COLLAB-004 §3.3）：issue.updated → 匹配区 150ms 轻闪 +「已更新」角标 ──
  const { remoteBrief } = useIssueRemoteFlash(issueId, issue?.updated_at ?? null, myUserId);
  /** Sprint-2 分区数据（关联 + 工时记录；spent/estimate 随 issue detail 走）。 */
  function refreshRelations() {
    RelationAPI.list(slug, projectId, issueId)
      .then((r) => setRelations(unwrap<RelationRow[]>(r) ?? []))
      .catch(() => setRelations([]));
  }
  function refreshWorklogs() {
    WorkLogAPI.list(slug, projectId, issueId, { per_page: 20 })
      .then((r) => setWorklogs(unwrap<WorkLogRow[]>(r) ?? []))
      .catch(() => setWorklogs([]));
  }
  /** ⊕含子任务口径（TASK-006 §4.2.4 subtree stats 加字段；归档根 404 → 次级行降级为 —）。 */
  function refreshSubtreeWorklog() {
    if (!issue || issue.archived_at) { setWlSubtreeStats(null); return; }
    IssueAPI.subtree(slug, projectId, issueId)
      .then((r) => {
        const st = unwrap<SubtreeData>(r);
        setWlSubtreeStats({
          spent: st.stats?.subtree_spent_minutes ?? 0,
          est: st.stats?.subtree_estimate_minutes ?? 0,
        });
      })
      .catch(() => setWlSubtreeStats(null));
  }
  /** C.55 Schema（ETag 协商缓存；失败 → 内置字段 + 「自定义字段加载失败 · 重试」条）。 */
  function refreshSchema() {
    setCfError(false);
    FieldAPI.schema(slug, projectId)
      .then((r) => setFieldSchema(unwrap<{ custom: CustomFieldDef[] }>(r)?.custom ?? []))
      .catch(() => { setFieldSchema([]); setCfError(true); });
  }
  useEffect(() => {
    const handle = setTimeout(() => { void refresh(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, slug, projectId]);

  // 拉类型 + 标签 + 子任务
  useEffect(() => {
    if (!issue) return;
    IssueTypeAPI.list(slug, projectId)
      .then((r) => setTypes(unwrap<typeof types>(r) ?? []))
      .catch(() => {});
    LabelAPI.list(slug, projectId)
      .then((r) => setLabels(unwrap<typeof labels>(r) ?? []))
      .catch(() => {});
    IssueAPI.subIssues(slug, projectId, issueId)
      .then((r) => setSubIssues(unwrap<typeof subIssues>(r) ?? []))
      .catch(() => {});
    ProjectAPI.states(slug, projectId, { include_cancelled: "1" })
      .then((r) => setStates(unwrap<typeof states>(r) ?? []))
      .catch(() => {});
    ProjectMemberAPI.list(slug, projectId, { per_page: 100 })
      .then((r) => setMembers(unwrap<typeof members>(r) ?? []))
      .catch(() => {});
    // Sprint-7：当前可用流转（受控项目边按钮；无工作流 fallback 保持状态下拉）
    WorkflowAPI.available(slug, projectId, issueId)
      .then((r) => {
        const d = (r as unknown as { data?: { fallback: boolean; available: TransitionAvailableItem[] | null;
          current_locks?: string[] } }).data;
        if (d && d.fallback === false && d.available) {
          setIsControlled(true);
          setFlowEdges(d.available);
          setCurrentLocks(new Set(d.current_locks ?? []));
        } else {
          setIsControlled(false);
          setFlowEdges([]);
          setCurrentLocks(new Set());
        }
      })
      .catch(() => { /* available 拉不到不阻断抽屉 */ });
    // Sprint-2 分区数据：关联 / 工时 / 字段 Schema / 子树口径工时
    refreshRelations();
    refreshWorklogs();
    refreshSubtreeWorklog();
    refreshSchema();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [issue?.id, slug, projectId, issueId]);

  // 切到对应 tab 时拉数据（C.25 时间线 + C.31 附件；评论 Tab 数据由 CommentThreadTab 自管）
  useEffect(() => {
    if (tab === "activity") {
      loadActivityGroups();
    }
    if (tab === "attachments") {
      AttachmentAPI.list(slug, projectId, issueId)
        .then((r) => setAttachments(unwrap<typeof attachments>(r) ?? []))
        .catch(() => {});
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, slug, projectId, issueId]);

  const saved = () => { setSavedFlash(true); setTimeout(() => setSavedFlash(false), 1800); onChanged?.(); };

  // dropdown 关闭（CLAUDE.md 教训 #4）：mousedown 阶段 + target.closest 判 scope
  useEffect(() => {
    if (!labelsMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="drawer-labels-menu"]')) return;
      setLabelsMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [labelsMenuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="drawer-more-menu"]')) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menuOpen]);

  /** 描述编辑器初始内容：只能用 ref 同步，不能用 dangerouslySetInnerHTML
   *  （原因见该 div 上的注释）。用户正在输入（dirty）时绝不覆盖。 */
  useEffect(() => {
    const el = descRef.current;
    if (!el) return;                    // 非「描述」Tab 时该 div 未挂载
    if (descDirtyRef.current) return;   // 用户正在编辑 —— 覆盖会丢输入
    const server = issue?.description_html ?? "";
    const normalized = !server || server === "<p></p>" ? "" : server;
    if (el.innerHTML === normalized) return;
    el.innerHTML = normalized;
  }, [tab, issue?.description_html]);

  // C.23 属性行内编辑的下拉：同一时刻只开一个，点属性区外即关
  useEffect(() => {
    if (!propMenu) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="drawer-prop-menu"]')) return;
      setPropMenu(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [propMenu]);

  // Sprint-2 弹出层（估算下拉 / +N 执行人浮层 / 工时行菜单 / 动态过滤器）：点外即关（教训 #4 mousedown）
  useEffect(() => {
    if (!estMenuOpen && !asgMoreOpen && !wlMenuRow && !actFieldMenu && !actActorMenu) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      const inScope = t?.closest(
        '[data-sb-scope="drawer-est-menu-list"],[data-sb-scope="drawer-est-set"],[data-sb-scope="drawer-est-menu"],[data-sb-scope="drawer-assignee-more"],[data-sb-scope="drawer-worklog-row-menu"],[data-sb-scope="drawer-worklog-menu"],[data-sb-scope="act-field-menu"],[data-sb-scope="act-field-toggle"],[data-sb-scope="act-actor-menu"],[data-sb-scope="act-actor-toggle"],[data-sb-scope="avatar-stack"]',
      );
      if (inScope) return;
      setEstMenuOpen(false); setAsgMoreOpen(false); setWlMenuRow(null); setActFieldMenu(false); setActActorMenu(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [estMenuOpen, asgMoreOpen, wlMenuRow, actFieldMenu, actActorMenu]);

  /** C.23 属性行内编辑的统一落库：选中即提交，失败回滚并 toast（TASK-002 §3.7）。
   *  返回是否成功，供乐观更新的控件（日期）决定要不要回滚。
   *  TASK-005 §3.3：迁入 completed 被 409 RESOURCE_TRANSITION_BLOCKED 拦截时
   *  不 toast，改弹 M-BLOCKED 对话框（详情入口；管理员可强制完成）。 */
  async function patchIssue(payload: Parameters<typeof IssueAPI.patch>[3], failMsg: string): Promise<boolean> {
    setPropMenu(null);
    if (!issue) return false;
    try {
      await IssueAPI.patch(slug, projectId, issueId, payload);
      await refresh(); saved();
      return true;
    } catch (e: unknown) {
      const err = e as ApiError;
      const targetState = payload.state_id ? states.find((s) => s.id === payload.state_id) : undefined;
      if (err?.code === "RESOURCE_TRANSITION_BLOCKED" && targetState?.group === "completed") {
        setBlockedDlg({ issueName: issue.name, blockers: blockersFromError(err), stateId: payload.state_id! });
        return false;
      }
      toast(e instanceof Error ? e.message : failMsg, "error");
      return false;
    }
  }

  // 服务端值变化时同步回草稿（切换任务 / 外部改动 / 保存后回读）
  useEffect(() => { setStartDraft(issue?.start_date ?? ""); }, [issue?.start_date]);
  useEffect(() => { setTargetDraft(issue?.target_date ?? ""); }, [issue?.target_date]);

  async function saveTitle() {
    setEditing(false);
    if (!issue) return;
    const v = titleDraft.trim();
    if (v && v !== issue.name) {
      await IssueAPI.patch(slug, projectId, issueId, { name: v });
      await refresh(); saved();
    }
  }

  /** 保存描述（失焦 / 切 Tab 时落库）。
   *  后端 IssueWriteSerializer 接收 description_html，Issue.save() 会用 strip_tags
   *  重算 description_stripped —— 所以改描述后 trigram 搜索索引自动跟上。 */
  async function saveDesc() {
    if (!issue || !descDirtyRef.current) return;
    const el = descRef.current;
    if (!el) return;
    const html = el.innerHTML.trim();
    descDirtyRef.current = false;
    // 清空时归一为 TipTap 空文档，与后端 default 一致（TASK-001 FE-37：`<p></p>` 视为空）
    const next = html === "" || html === "<p></p>" || html === "<br>" ? "<p></p>" : html;
    if (next === (issue.description_html || "<p></p>")) return;
    await IssueAPI.patch(slug, projectId, issueId, { description_html: next });
    await refresh(); saved();
    onChanged?.();
  }

  async function toggleSub(s: { id: string; state_group: string }) {
    if (togglingSubId) return;
    // 后端写侧只认 `state_id`（IssueWriteSerializer.state_id）；`state_group` 是读侧
    // SerializerMethodField，PATCH 传它会被 DRF 静默忽略 → 返回 200 但库里没变。
    // 所以必须先把目标 group 翻译成项目里真实的 state_id。
    const newGroup = s.state_group === "completed" ? "started" : "completed";
    const target =
      states.find((x) => x.group === newGroup) ??
      states.find((x) => x.group === "unstarted") ??
      states.find((x) => x.group === "backlog");
    if (!target) {
      toast("项目缺少可用状态，无法切换子任务", "error");
      return;
    }
    const prevGroup = s.state_group;
    setTogglingSubId(s.id);
    setSubIssues((cur) => cur.map((x) => (x.id === s.id ? { ...x, state_group: newGroup, state_name: target.name } : x)));
    try {
      await IssueAPI.patch(slug, projectId, s.id, { state_id: target.id });
      // 以服务端为准回读一次，避免乐观更新与真实 state_group/state_name 漂移
      const r = await IssueAPI.subIssues(slug, projectId, issueId);
      setSubIssues(unwrap<typeof subIssues>(r) ?? []);
      toast(newGroup === "completed" ? "子任务已完成" : "子任务已恢复");
      onChanged?.();
    } catch (e: unknown) {
      setSubIssues((cur) => cur.map((x) => (x.id === s.id ? { ...x, state_group: prevGroup } : x)));
      toast(e instanceof Error ? e.message : "网络异常，子任务未切换", "error");
    } finally {
      setTogglingSubId(null);
    }
  }

  async function uploadFile(f: File) {
    if (uploadingFile) return;
    if (f.size > 25 * 1024 * 1024) { toast("文件超过 25MB", "error"); return; }
    setUploadingFile(true); setUploadPct(0);
    try {
      const pre = await AttachmentAPI.presign(slug, projectId, issueId, {
        file_name: f.name, file_size: f.size, content_type: f.type || "application/octet-stream",
      });
      const p = unwrap<{ asset_id: string; upload_url: string; fields: Record<string, string>; expires_at: string }>(pre);
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", p.upload_url);
        xhr.setRequestHeader("Content-Type", f.type || "application/octet-stream");
        xhr.upload.onprogress = (e) => { if (e.lengthComputable) setUploadPct(Math.round((e.loaded / e.total) * 100)); };
        xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`直传失败：HTTP ${xhr.status}（网关未反代 /uploads/？）`)));
        xhr.onerror = () => reject(new Error("直传失败：网络错误"));
        xhr.send(f);
      });
      await AttachmentAPI.complete(slug, projectId, issueId, p.asset_id);
      // 回读列表而不是手工拼接：complete 的响应不含 download_url，拼进去会让下载按钮跳 undefined
      const r = await AttachmentAPI.list(slug, projectId, issueId);
      setAttachments(unwrap<AttachmentRow[]>(r) ?? []);
      toast(`已上传 ${f.name}`);
    } catch (e: unknown) {
      // 透出后端真实原因（对象存储不可用 / 类型不支持 / 大小不符），否则用户只看到「没反应」
      toast(e instanceof Error ? e.message : "附件上传失败", "error");
    } finally {
      setUploadingFile(false); setUploadPct(0);
    }
  }

  /** 打开删除确认：有子任务时取 subtree stats 的后代数（total-1，与 DELETE 回传的
   *  deleted_count 同源——TASK-004 §2.4「确认弹层明示将删除的后代数量」）。 */
  async function openDeleteConfirm() {
    setConfirmDel(true);
    setDelDescCount(null);
    if (subIssues.length > 0) {
      try {
        const r = await IssueAPI.subtree(slug, projectId, issueId);
        const st = unwrap<SubtreeData>(r);
        setDelDescCount((st.stats?.total ?? subIssues.length + 1) - 1);
      } catch {
        setDelDescCount(subIssues.length); // 兜底：直接子级数
      }
    } else {
      setDelDescCount(0);
    }
  }

  async function del() {
    setConfirmDel(false); onClose();
    try {
      // TASK-004 §4.2.5：DELETE 200 + {deleted_count, descendant_ids}（级联软删回传受影响数）
      const r = await IssueAPI.del(slug, projectId, issueId);
      const res = unwrap<DeleteSubtreeResult>(r);
      toast(res && res.deleted_count > 1
        ? `已删除 ${issue?.issue_key ?? "任务"}（整树 ${res.deleted_count} 个任务）`
        : `已删除 ${issue?.issue_key ?? "任务"}`);
    } catch { toast("删除失败", "error"); }
    onChanged?.();
  }

  /** C.61 动态 Tab：epoch 组时间线（服务端预聚合；?field= / ?actor_id= 过滤；游标锚定 epoch）。 */
  function loadActivityGroups(append = false) {
    const params: { per_page?: number; field?: string; actor_id?: string; cursor?: string } = {};
    if (actFieldFilter) params.field = actFieldFilter;
    if (actActorFilter) params.actor_id = actActorFilter;
    if (append && activityCursor) params.cursor = activityCursor;
    IssueAPI.activityGroups(slug, projectId, issueId, params)
      .then((r) => {
        const rows = unwrap<ActivityGroup[]>(r) ?? [];
        setActivityGroups((cur) => (append ? [...cur, ...rows] : rows));
        setActivityCursor(((r as unknown as { meta?: { next_cursor?: string | null } }).meta?.next_cursor) ?? null);
        setActivityHasMore(Boolean((r as unknown as { meta?: { next_cursor?: string | null } }).meta?.next_cursor));
      })
      .catch(() => {});
  }
  useEffect(() => {
    if (tab !== "activity") return;
    loadActivityGroups(false);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [actFieldFilter, actActorFilter, issueId, slug, projectId]);

  async function addSubIssue() {
    const name = newSubName.trim();
    if (!name) return;
    try {
      await IssueAPI.createSubIssue(slug, projectId, issueId, { name });
      const r = await IssueAPI.subIssues(slug, projectId, issueId);
      setSubIssues(unwrap<typeof subIssues>(r) ?? []);
      setNewSubName("");
      onChanged?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "创建失败";
      toast(msg, "error");
    }
  }

  async function setIssueLabel(labelIds: string[]) {
    if (!issue) return;
    try {
      await IssueAPI.setLabels(slug, projectId, issueId, labelIds);
      await refresh(); saved();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "更新标签失败";
      toast(msg, "error");
    }
  }

  /** C.42 关联跳转 / C.57 副本查看 / C.44 阻塞项跳转：宿主提供回调则交宿主，否则嵌套抽屉自开。 */
  function openNested(id: string) {
    if (onOpenIssue) { onOpenIssue(id); return; }
    setTreeNodeIssueId(id);
  }

  /** C.49 认领：点击即 POST 无需确认，乐观插入自己头像；409 STATE 已被认领 → Toast + 回读。 */
  async function claimIssue() {
    try {
      await AssigneeAPI.claim(slug, projectId, issueId);
      toast("已认领该任务", "ok");
      await refresh(); onChanged?.();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "认领失败", "error");
      await refresh();
    }
  }

  /** C.49 退出任务（+N 浮层中自己行的次级动作；自退 DELETE assignees/{user_id}/）。 */
  async function exitIssue() {
    if (!myUserId) return;
    try {
      await AssigneeAPI.removeSelf(slug, projectId, issueId, myUserId);
      toast("已退出任务");
      await refresh(); onChanged?.();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "退出失败", "error");
    }
  }

  /** C.42 关联删除（悬浮显现 ⓧ + 二次确认；镜像行同事务删除）。 */
  async function delRelation(linkId: string) {
    if (!confirm("删除该关联？")) return;
    try {
      await RelationAPI.del(slug, projectId, issueId, linkId);
      toast("已删除关联", "ok");
      refreshRelations(); onChanged?.();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "删除关联失败", "error");
    }
  }

  /** C.46 估算下拉常用值（onBlur/选中即 PATCH estimate_minutes；0.5h 步进 + 自定义分钟）。 */
  async function setEstimate(minutes: number | null) {
    setEstMenuOpen(false);
    try {
      await IssueAPI.patch(slug, projectId, issueId, minutes == null ? { estimate_minutes: null } : { estimate_minutes: minutes });
      await refresh(); saved();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "设置估算失败", "error");
    }
  }

  /** C.55 动态字段值落库（PATCH 合并语义；显式清空传 null）。 */
  async function setFieldValue(key: string, value: unknown) {
    try {
      const r = await IssueAPI.patch(slug, projectId, issueId, { custom_fields: { [key]: value } });
      // TASK-012 §4.4（补口轮）：BR-16 静默丢弃 readonly/hidden 的提示钩子
      const dropped = (r as unknown as { meta?: { warning?: { dropped_fields?: string[] } } })
        ?.meta?.warning?.dropped_fields;
      if (dropped?.length) {
        toast(`权限不足，未写入：${dropped.join("、")}`, "warning");
      }
      await refresh(); saved();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "字段保存失败", "error");
    }
  }

  /** C.58 归档入口：先取 subtree stats 后代数（含 0；subtree 对归档根 404 —— 此时不应进入）。 */
  async function openArchiveConfirm() {
    setArchDescCount(0);
    setArchOpen(true);
    try {
      const r = await IssueAPI.subtree(slug, projectId, issueId);
      const st = unwrap<SubtreeData>(r);
      setArchDescCount(Math.max(0, (st.stats?.total ?? 1) - 1));
    } catch { setArchDescCount(subIssues.length); }
  }

  /** C.57 打开复制弹层（subCount 取 subtree stats 口径，失败退直接子级数）。 */
  async function openDuplicate() {
    setDupOpen(true);
    if (dupSubCount == null) {
      try {
        const r = await IssueAPI.subtree(slug, projectId, issueId);
        const st = unwrap<SubtreeData>(r);
        setDupSubCount(Math.max(0, (st.stats?.total ?? 1) - 1));
      } catch { setDupSubCount(subIssues.length); }
    }
  }

  /** C.60/C.58 恢复归档（整树，动作幂等）。 */
  async function restoreArchive() {
    try {
      await IssueAPI.unarchive(slug, projectId, issueId);
      toast("已恢复归档", "ok");
      await refresh(); onChanged?.();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "恢复失败", "error");
    }
  }

  /** C.46 工时记录删除（软删，本人 / PROJ_ADMIN）。 */
  async function delWorklog(logId: string) {
    try {
      await WorkLogAPI.del(slug, projectId, issueId, logId);
      toast("记录已删除", "ok");
      refreshWorklogs(); await refresh(); onChanged?.();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  // C.31 文件大小人类可读（KB/MB/GB 二进制自适应 —— FILE-001 §3.2）
  function humanSize(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  // C.31 MIME → 图标映射（image 🖼 / video 🎬 / pdf 📕 / zip 🗜 / text log 📄 / 未知 📎）
  function mimeIcon(mime: string): string {
    if (mime.startsWith("image/")) return "🖼";
    if (mime.startsWith("video/")) return "🎬";
    if (mime === "application/pdf") return "📕";
    if (mime.includes("zip") || mime.includes("compressed")) return "🗜";
    if (mime.startsWith("text/") || mime === "text/plain" || mime.includes("log")) return "📄";
    return "📎";
  }

  if (!issue) return null;
  const issueLabelIds = new Set<string>((issue as unknown as { label_ids?: string[] }).label_ids ?? []);
  const activeLabels = labels.filter((l) => l.is_active);
  const typeId = (issue as unknown as { type_id?: string }).type_id;
  const issueTypeName = types.find((t) => t.id === typeId)?.name ?? "—";
  const issueTypeColor = types.find((t) => t.id === typeId)?.color ?? "#9ca3af";
  // 后端只下发 assignee_ids（见 @rp/types 里 Issue.assignee 的 @deprecated 说明），
  // 姓名必须拿成员表解析 —— 直接读 issue.assignee 会恒为「未分配」（C.49 堆叠呈现同源）。
  const assigneeIds = issue.assignee_ids ?? [];
  const nameOfMember = (uid: string) => members.find((m) => m.user.id === uid)?.user.display_name ?? "…";
  const assigneeNames = assigneeIds.map(nameOfMember);
  const issueType = types.find((t) => t.id === typeId);
  const priority = issue.priority ?? null;

  // ── C.42 关联三分组（固定顺序；空组渲染时过滤）──普通派生（`if (!issue) return null` 早返回之后不得再挂 hook）
  const relGroups = {
    pre: relations.filter((r) => r.relation_type === "is_blocked_by"),
    post: relations.filter((r) => r.relation_type === "blocks"),
    rel: relations.filter((r) => r.relation_type === "relates_to" || r.relation_type === "duplicates"),
  };
  const relTotal = relations.length;
  /** C.43 已关联预判：搜索行内灰字「已关联」且不可选 */
  const relLinkedIds = new Set(relations.map((r) => r.related_issue_id));

  // ── C.46 工时派生（spent 由 issue detail 下发 annotate；子树口径取 subtree stats） ──
  const spentTotal = (issue as unknown as { spent_minutes?: number }).spent_minutes ?? 0;
  const wlCurrent = wlScopeSelf ? spentTotal : (wlSubtreeStats?.spent ?? spentTotal);
  const wlPct = issue.estimate_minutes != null && issue.estimate_minutes > 0 ? Math.round((wlCurrent / issue.estimate_minutes) * 100) : 0;
  const wlOver = issue.estimate_minutes != null && issue.estimate_minutes > 0 && wlPct > 100;

  // ── C.55 动态字段（停用字段不渲染——数据在响应中，UI 过滤） ──
  const activeFields = (fieldSchema ?? []).filter((f) => f.is_active !== false).sort((a, b) => a.sort_order - b.sort_order);
  const cfValues = ((issue as unknown as { custom_fields?: Record<string, unknown> }).custom_fields ?? {}) as Record<string, unknown>;

  return (
    <div className={`fixed inset-0 ${layer} flex justify-end`}>
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <aside className="relative w-[720px] max-w-[calc(100vw-64px)] bg-white border-l border-neutral-200 shadow-lg flex flex-col" role="dialog" aria-modal="true" aria-label={`任务详情 ${issue.issue_key}`}>
        {/* 头部 */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-neutral-200">
          <button className="font-mono text-[13px] text-neutral-500 hover:text-brand-600"
            onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); }}
            title="点击复制编号">{issue.issue_key}</button>
          {/* C.60：已归档徽标（头部状态旁） */}
          {issue.archived_at && (
            <span className="inline-flex items-center gap-1 text-[12px] text-neutral-400 bg-neutral-100 rounded px-1.5 py-0.5" data-sb-scope="drawer-arch-badge">🗄 已归档</span>
          )}
          {/* COLLAB-004 §3.3：远端 issue.updated 提示角标（4s 消隐） */}
          {remoteBrief && (
            <span className="inline-flex items-center gap-1 text-[12px] text-brand-700 bg-brand-50 border border-brand-200 rounded px-1.5 py-0.5" role="status" data-sb-scope="drawer-live-badge">
              ✦ 已更新{remoteBrief && remoteBrief !== "updated" ? `（${remoteBrief}）` : ""}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1">
            <div className="relative" ref={menuRef} data-sb-scope="drawer-more-menu">
              <button aria-label="更多操作" onClick={() => setMenuOpen(!menuOpen)}
                className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:bg-neutral-100 rounded-md">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>
              </button>
              {menuOpen && (
                <div className="absolute top-9 right-0 w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 z-10">
                  {/* C.57 入口：详情 ⋯ 菜单「创建副本」 */}
                  {canWrite && !issue.archived_at && (
                    <button data-sb-scope="drawer-menu-dup" className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50"
                    onClick={() => { setMenuOpen(false); void openDuplicate(); }}>创建副本</button>
                  )}
                  {/* C.58 入口：详情 ⋯ → 归档任务 / 恢复 */}
                  {canWrite && !issue.archived_at && (
                    <button data-sb-scope="drawer-menu-archive" className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50"
                    onClick={() => { setMenuOpen(false); void openArchiveConfirm(); }}>归档任务</button>
                  )}
                  {issue.archived_at && canWrite && (
                    <button data-sb-scope="drawer-menu-restore" className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50"
                    onClick={() => { setMenuOpen(false); void restoreArchive(); }}>恢复</button>
                  )}
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(location.origin + location.pathname + `?peekIssue=${issueId}`); toast("已复制链接"); setMenuOpen(false); }}>复制链接</button>
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); setMenuOpen(false); }}>复制编号</button>
                  <div className="h-px bg-neutral-200 my-1" />
                  <button className="w-full text-left px-3 h-8 text-[13px] text-red-600 hover:bg-red-50" onClick={() => { setMenuOpen(false); void openDeleteConfirm(); }}>删除任务</button>
                </div>
              )}
            </div>
            <button onClick={onClose} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
          </div>
        </div>

        {/* C.60 归档只读横幅：「已归档于 yyyy-MM-dd · [恢复]」顶部 role=status；编辑控件全部禁用 */}
        {issue.archived_at && (
          <div className="mx-5 mt-2.5 mb-0.5 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 px-3.5 py-2 text-[13px]"
            role="status" data-sb-scope="drawer-arch-banner">
            <span aria-hidden="true">🗄</span>
            已归档于 {(issue.archived_at ?? "").slice(0, 10)}
            {canWrite && (
              <button className="text-brand-600 hover:text-brand-700" data-sb-scope="drawer-arch-restore"
                onClick={() => void restoreArchive()}>恢复</button>
            )}
          </div>
        )}

        {/* Tab 条（C.25 / C.31 / C.32）—— 四 Tab 全部可点 */}
        <div role="tablist" aria-label="任务详情视图" className="flex border-b border-neutral-200 px-5">
          {([
            ["desc", "描述"],
            ["comments", "💬 评论"],
            ["activity", "动态"],
            ["attachments", `附件${attachments.length ? " " + attachments.length : ""}`],
          ] as const).map(([k, label]) => (
            <button
              key={k}
              role="tab"
              aria-selected={tab === k}
              // 切 Tab 会卸载描述区，focusout 不保证触发 —— 先落库再切
              onClick={() => { if (tab === "desc") void saveDesc(); setTab(k as DrawerTab); }}
              data-sb-scope="drawer-tab"
              data-tab-key={k}
              className={`h-9 px-3 text-[13px] -mb-px border-b-2 ${tab === k ? "border-brand-500 text-brand-600 font-medium" : "border-transparent text-neutral-500 hover:text-neutral-900"}`}
            >{label}</button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {savedFlash && <div className="float-right text-xs text-emerald-600 flex items-center gap-1">✓ 已保存</div>}

          {tab === "desc" && (
            <>
              {/* 标题（C.23；归档只读——C.60 编辑控件全部禁用） */}
              {editing ? (
                <input autoFocus aria-label="任务标题" data-sb-scope="drawer-title-input"
                  className="w-full text-lg font-semibold border-b-2 border-brand-500 outline-none bg-transparent py-1"
                  value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={saveTitle}
                  onKeyDown={(e) => { if (e.key === "Enter") saveTitle(); if (e.key === "Escape") setEditing(false); }} />
              ) : (
                <h2 className={`text-lg font-semibold py-1 rounded -mx-2 px-2 ${issue.archived_at || !canWrite ? "" : "cursor-text hover:bg-neutral-50"}`} title={issue.archived_at ? "已归档，恢复后才能编辑" : "点击编辑标题"}
                  onClick={() => { if (!issue.archived_at && canWrite) { setTitleDraft(issue.name); setEditing(true); } }}>{issue.name}</h2>
              )}

              {/* 描述编辑器（C.7 装饰外壳工具条常驻 —— ADR-0011） */}
              <div className="border border-neutral-200 rounded-lg mt-3">
                <div className="flex gap-0.5 px-2 py-1.5 border-b border-neutral-200 bg-neutral-50 rounded-t-lg text-[13px] text-neutral-500" aria-hidden>
                  {["B", "I", "U", "≡", "☰", "⌗", "</>", "🔗"].map((t, i) => (
                    <span key={i} className="min-w-[26px] h-6 inline-flex items-center justify-center rounded px-1"
                      style={t === "B" ? { fontWeight: 700 } : t === "I" ? { fontStyle: "italic" } : t === "U" ? { textDecoration: "underline" } : undefined}>{t}</span>
                  ))}
                </div>
                {/* 描述区必须可编辑：test-cases.md §"描述编辑器" 登记的形态是
                    「工具条装饰外壳 + contenteditable『添加描述…』」，此前只渲染了只读
                    dangerouslySetInnerHTML，导致抽屉里改不了描述。
                    工具条保持装饰外壳（真实 TipTap 命令属 COLLAB-002，登记在册的边界）。 */}
                {/* 注意：这里**不能**用 dangerouslySetInnerHTML 灌初始内容。
                    React 19 对该 prop 按对象身份比较（react-dom-client setValueForProperty
                    里直接 `domElement.innerHTML = key`，不与 prevValue 比较），
                    每次渲染新建的 {__html} 都会无条件重写 innerHTML —— 于是任何一次
                    重渲染（比如点标题触发 setEditing）都会把用户正在输入的内容清空。
                    改为由下面的 effect 用 ref 同步。 */}
                <div
                  ref={descRef}
                  contentEditable
                  suppressContentEditableWarning
                  role="textbox"
                  aria-multiline="true"
                  aria-label="任务描述"
                  data-sb-scope="drawer-desc-input"
                  data-ph="添加描述…"
                  className="min-h-[110px] p-2.5 text-[13px] leading-relaxed outline-none focus:bg-brand-50/30 empty:before:content-[attr(data-ph)] empty:before:text-neutral-400"
                  onInput={() => { descDirtyRef.current = true; }}
                  onBlur={() => { void saveDesc(); }}
                />
              </div>

              {/* 属性区七行（C.23）—— label 80px + 控件 行式布局。
                  COLLAB-004 §3.3：远端 brief 命中字段区 → 150ms 背景轻闪（rp-live-flash）。 */}
              <div className={`mt-4 border-t border-neutral-200 pt-4 grid grid-cols-[80px_1fr] gap-x-3 gap-y-3 items-center ${remoteBrief ? "rp-live-flash" : ""}`}
                data-sb-scope="drawer-props">
                <style>{`@keyframes rpliveflash{0%{background:rgba(63,118,255,0)}30%{background:rgba(63,118,255,.12)}100%{background:rgba(63,118,255,0)}}.rp-live-flash{animation:rpliveflash .15s ease}`}</style>
                {/* 以下六行均为「行内编辑 · 选中即提交」（C.23 / TASK-002 §3.2）。
                    此前全部渲染成纯文本，导致优先级 / 负责人 / 开始 / 截止（以及状态 / 类型）
                    在抽屉里根本改不了 —— 只有标签那一行是可用的。 */}

                <label className="text-[13px] text-neutral-500">状态</label>
                {isControlled ? (
                  /* Sprint-7（WF-001 §3.4 / WF-004 §3.1）：受控流转——边名按钮 + 守卫对话框 */
                  <div className="flex flex-wrap items-center gap-1.5" data-sb-scope="drawer-transitions">
                    <StateBadge group={issue.state_group ?? "unstarted"} name={issue.state_name ?? "—"} />
                    {flowEdges.filter((e) => e.allowed).map((e) => (
                      <button key={e.transition_id} type="button" data-flow-name={e.name}
                        onClick={() => void runTransition(e)}
                        className="h-6 px-2 rounded border border-brand-200 text-brand-600 text-xs hover:bg-brand-50 transition">
                        {e.name}{e.has_approval ? " ·审" : ""}
                      </button>
                    ))}
                    {flowNotice && <span className="text-xs text-amber-600" data-sb-scope="flow-notice">{flowNotice}</span>}
                  </div>
                ) : (
                <>
                <button type="button" data-sb-scope="drawer-prop-menu" aria-label="修改状态"
                  onClick={() => setPropMenu(propMenu === "state" ? null : "state")}
                  
                  className="group justify-self-start inline-flex items-center gap-1.5 text-[13px] hover:bg-brand-50 hover:text-brand-600 border border-transparent hover:border-brand-200 rounded px-1.5 py-0.5 -mx-1 transition">
                  <StateBadge group={issue.state_group ?? "unstarted"} name={issue.state_name ?? "—"} />
                  <span aria-hidden="true" className="text-neutral-400 text-[11px] group-hover:scale-110 transition">▾</span>
                </button>
                {propMenu === "state" && (
                  <PropMenu>{states.map((s) => (
                    <MenuItem key={s.id} on={s.id === issue.state_id}
                      onClick={() => void patchIssue({ state_id: s.id }, "更新状态失败")}>
                      <span className="w-2 h-2 rounded-full" style={{ background: STATE_COLOR[s.group] ?? "#9ca3af" }} aria-hidden="true" />
                      {s.name}
                    </MenuItem>
                  ))}</PropMenu>
                )}
                </>
                )}

                <label className="text-[13px] text-neutral-500">类型</label>
                <button type="button" data-sb-scope="drawer-prop-menu" data-sb-attr="drawer-attr-type" aria-label="修改类型"
                  onClick={() => setPropMenu(propMenu === "type" ? null : "type")}
                  className="group justify-self-start inline-flex items-center gap-1.5 text-[13px] hover:bg-brand-50 hover:text-brand-600 border border-transparent hover:border-brand-200 rounded px-1.5 py-0.5 -mx-1 transition">
                  <span className="w-2 h-2 rounded-full" style={{ background: issueTypeColor }} aria-hidden="true" />
                  {issueTypeName}
                  {issueType && !issueType.is_active && <span className="text-[11px] text-neutral-400 ml-1">（已停用）</span>}
                  <span aria-hidden="true" className="text-neutral-400 text-[11px] group-hover:scale-110 transition">▾</span>
                </button>
                {propMenu === "type" && (
                  <PropMenu>{types.filter((t) => t.is_active).map((t) => (
                    <MenuItem key={t.id} on={t.id === typeId}
                      onClick={() => void patchIssue({ type_id: t.id }, "更新类型失败")}>
                      <span className="w-2 h-2 rounded-full" style={{ background: t.color }} aria-hidden="true" />
                      {t.name}
                    </MenuItem>
                  ))}</PropMenu>
                )}

                <label className="text-[13px] text-neutral-500">优先级</label>
                <button type="button" data-sb-scope="drawer-prop-menu" data-sb-attr="drawer-attr-priority" aria-label="修改优先级"
                  onClick={() => setPropMenu(propMenu === "priority" ? null : "priority")}
                  className="group justify-self-start inline-flex items-center gap-1.5 text-[13px] hover:bg-brand-50 hover:text-brand-600 border border-transparent hover:border-brand-200 rounded px-1.5 py-0.5 -mx-1 transition">
                  <span className="inline-flex items-center gap-1.5">
                    <span aria-hidden="true">⚑</span>
                    {priority && priority !== "none"
                      ? <span>{PRIORITY_LABEL[priority] ?? priority}</span>
                      : <span className="text-neutral-400">无</span>}
                  </span>
                  <span aria-hidden="true" className="text-neutral-400 text-[11px] group-hover:scale-110 transition">▾</span>
                </button>
                {propMenu === "priority" && (
                  <PropMenu>{PRIORITY_KEYS.map((k) => (
                    <MenuItem key={k} on={(priority ?? "none") === k}
                      onClick={() => void patchIssue({ priority: k }, "更新优先级失败")}>
                      <span className="w-2 h-2 rounded-full" style={{ background: PRIORITY_COLOR[k] }} aria-hidden="true" />
                      {PRIORITY_LABEL[k]}
                    </MenuItem>
                  ))}</PropMenu>
                )}

                {/* 负责人行已升级为独立「执行人」分区（C.49【变更 · 基线=C.23】，
                    位于属性区与子任务区之间——O1 分区次序）；行内下拉菜单随之移除。 */}

                <label className="text-[13px] text-neutral-500">标签</label>
                <div className="relative" ref={labelsMenuRef} data-sb-scope="drawer-labels-menu">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {activeLabels.filter((l) => issueLabelIds.has(l.id)).map((l) => (
                      <span key={l.id} className="inline-flex items-center gap-1 text-[12px] px-2 h-6 rounded-full text-white" style={{ background: l.color }}>
                        {l.name}
                        <button
                          aria-label={`摘除标签 ${l.name}`}
                          onClick={() => setIssueLabel(Array.from(issueLabelIds).filter((x) => x !== l.id))}
                          className="opacity-70 hover:opacity-100"
                        >✕</button>
                      </span>
                    ))}
                    <button
                      onClick={() => setLabelsMenuOpen(!labelsMenuOpen)}
                      data-sb-scope="drawer-labels-toggle"
                      aria-label="管理标签"
                      className="inline-flex items-center gap-1 text-[12px] px-2 h-6 rounded-full border border-neutral-300 text-neutral-500 hover:bg-neutral-50"
                    >＋</button>
                  </div>
                  {labelsMenuOpen && (
                    <div data-sb-scope="drawer-labels-menu" className="absolute top-[calc(100%+4px)] left-0 z-10 w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[240px] overflow-y-auto">
                      {activeLabels.length === 0 ? (
                        <div className="px-3 py-2 text-[13px] text-neutral-500">该项目暂无标签</div>
                      ) : activeLabels.map((l) => {
                        const on = issueLabelIds.has(l.id);
                        return (
                          <button
                            key={l.id}
                            role="menuitemcheckbox"
                            aria-checked={on}
                            onClick={() => {
                              const next = new Set(issueLabelIds);
                              if (on) next.delete(l.id); else next.add(l.id);
                              setIssueLabel(Array.from(next));
                            }}
                            className={`w-full flex items-center gap-2 px-3 h-8 text-[13px] hover:bg-neutral-50 ${on ? "bg-brand-50" : ""}`}
                          >
                            <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[10px] ${on ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300 bg-white"}`}>{on ? "✓" : ""}</span>
                            <span className="w-2.5 h-2.5 rounded-full" style={{ background: l.color }} aria-hidden="true" />
                            {l.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <label className="text-[13px] text-neutral-500">开始{currentLocks.has("start_date") && " 🔒"}</label>
                <input type="date" aria-label="开始日期" data-sb-scope="drawer-attr-start"
                  disabled={currentLocks.has("start_date")}
                  title={currentLocks.has("start_date") ? "当前状态锁定该字段（WF-004 字段锁）" : undefined}
                  className="justify-self-start h-7 border border-neutral-300 rounded px-1.5 text-[13px] bg-white hover:border-neutral-400 disabled:bg-neutral-50 disabled:text-neutral-400"
                  value={startDraft}
                  onChange={(e) => {
                    const v = e.target.value;
                    setStartDraft(v);
                    void patchIssue({ start_date: v || null }, "更新开始日期失败")
                      .then((ok) => { if (!ok) setStartDraft(issue.start_date ?? ""); });
                  }} />

                <label className="text-[13px] text-neutral-500">截止{currentLocks.has("target_date") && " 🔒"}</label>
                <input type="date" aria-label="截止日期" data-sb-scope="drawer-attr-target"
                  disabled={currentLocks.has("target_date")}
                  title={currentLocks.has("target_date") ? "当前状态锁定该字段（WF-004 字段锁）" : undefined}
                  className="justify-self-start h-7 border border-neutral-300 rounded px-1.5 text-[13px] bg-white hover:border-neutral-400 disabled:bg-neutral-50 disabled:text-neutral-400"
                  value={targetDraft}
                  onChange={(e) => {
                    const v = e.target.value;
                    setTargetDraft(v);
                    void patchIssue({ target_date: v || null }, "更新截止日期失败")
                      .then((ok) => { if (!ok) setTargetDraft(issue.target_date ?? ""); });
                  }} />
              </div>

              {/* 执行人区（C.49【变更 · 基线=C.23 负责人行】/ TASK-007 §3.1）：24px 头像堆叠 + 名称行 + [＋ 编辑] + 空态认领 */}
              <div className="mt-5 border-t border-neutral-200 pt-3" data-sb-scope="drawer-assignee-section">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-500"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                    执行人
                  </span>
                  <div className="ml-auto">
                    {canWrite && !issue.archived_at && (
                      <button onClick={() => setAssignOpen(true)} data-sb-scope="drawer-assignee-edit"
                        className="h-[28px] px-2.5 border border-neutral-300 rounded-md text-[12px] text-neutral-600 hover:bg-neutral-50 inline-flex items-center gap-1">
                        <span aria-hidden="true">＋</span> 编辑
                      </button>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 min-h-[34px]" role="group"
                  aria-label={`执行人 ${assigneeNames.length} 人${assigneeNames.length ? `：${assigneeNames.join("、")}` : ""}`}>
                  {assigneeIds.length === 0 ? (
                    /* C.49 空态：👤 未指派 虚线框 +「🖐 认领」（≥CONTRIBUTOR；点击即 POST 无需确认） */
                    canWrite && !issue.archived_at ? (
                      <span className="inline-flex items-center gap-2 border-[1.5px] border-dashed border-neutral-300 rounded-lg pl-1.5 pr-2.5 py-[3px] text-[12px] text-neutral-400" data-sb-scope="drawer-assignee-empty">
                        <span aria-hidden="true">👤</span> 未指派
                        <button onClick={() => void claimIssue()} aria-label="认领该任务" data-sb-scope="drawer-claim"
                          className="h-[24px] px-2 bg-brand-500 text-white rounded-md text-[12px] inline-flex items-center gap-1 hover:bg-brand-600">
                          <span aria-hidden="true">🖐</span> 认领
                        </button>
                      </span>
                    ) : (
                      <span className="text-[12px] text-neutral-400" data-sb-scope="drawer-assignee-empty">未指派</span>
                    )
                  ) : (
                    <>
                      <AvatarStack names={assigneeNames} size={24} {...(assigneeNames.length > 3 ? { onMore: () => setAsgMoreOpen((v) => !v) } : {})} />
                      <span className="text-[12px] text-neutral-500 truncate flex-1" title={assigneeNames.join("、")}>
                        {assigneeNames.join("、")}{assigneeNames.length > 3 ? ` +${assigneeNames.length - 3} 人` : ""}
                      </span>
                    </>
                  )}
                  {/* C.49 +N 浮层：全部执行人 + 自己行「退出任务」 */}
                  {asgMoreOpen && assigneeNames.length > 3 && (
                    <div className="relative w-full" data-sb-scope="drawer-assignee-more">
                      <div role="dialog" className="absolute left-0 top-0 z-10 w-full bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                        {assigneeIds.map((id) => {
                          const isMe = id === myUserId;
                          return (
                            <div key={id} className="flex items-center gap-2 px-3 h-9 text-[13px]">
                              <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center" style={{ background: "#3b82f6" }} aria-hidden="true">
                                {initialOf(nameOfMember(id))}
                              </span>
                              <span className="flex-1">{nameOfMember(id)}{isMe ? "（我）" : ""}</span>
                              {isMe && canWrite && !issue.archived_at && (
                                <button onClick={() => void exitIssue()} data-sb-scope="drawer-assignee-exit"
                                  className="text-[12px] text-neutral-500 hover:text-red-600">退出任务</button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* 子任务区（C.24 基线 + TASK-004 §3.4/C.41 升级）：分区头「子任务 ◔ x/y」+「＋」+ 前 20 条 +「查看全部 N 个 →」 */}
              <div className="mt-5 border-t border-neutral-200 pt-3" data-sb-scope="drawer-sub-section">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-500"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
                    子任务
                  </span>
                  {/* ◔ 圆环计数（C.41 分区头：16px 圆环 + x/y；BR-05 cancelled 不进分母） */}
                  {(() => {
                    const valid = subIssues.filter((s) => s.state_group !== "cancelled");
                    const done = valid.filter((s) => s.state_group === "completed").length;
                    const full = valid.length > 0 && done === valid.length;
                    return valid.length > 0 ? (
                      <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-600 tabular-nums"
                        role="img" aria-label={`子任务 ${valid.length} 个，已完成 ${done} 个`}>
                        <span className="w-4 h-4 rounded-full inline-block"
                          style={full
                            ? { border: "2px solid #10b981", background: "#10b981" }
                            : { border: "2px solid #e5e5e5", borderTopColor: done > 0 ? "#10b981" : "#9ca3af" }} />
                        <span data-sb-scope="drawer-sub-progress">{done}/{valid.length}</span>
                      </span>
                    ) : null;
                  })()}
                  <div className="ml-auto flex items-center gap-1.5">
                    {subIssues.length > 20 && <span className="text-[12px] text-neutral-400">前 20 条</span>}
                    {/* C.60/C.41 归档只读态：无「＋」与完成勾选；「查看整棵树」入口隐藏（subtree 对归档根 404） */}
                    {canWrite && !issue.archived_at && (
                      <button
                        onClick={() => subInputRef.current?.focus()}
                        aria-label="添加子任务"
                        data-sb-scope="drawer-sub-add"
                        className="w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:bg-neutral-100 rounded-md"
                      >＋</button>
                    )}
                    {subIssues.length > 0 && !issue.archived_at && (
                      <button onClick={() => setTreeOpen(true)} data-sb-scope="drawer-sub-view-all"
                        className="text-[13px] text-brand-600 hover:text-brand-700">查看全部 {subIssues.length} 个 →</button>
                    )}
                  </div>
                </div>
                <ul className="flex flex-col gap-1">
                  {subIssues.length === 0 ? (
                    <li className="text-[13px] text-neutral-400 py-1" data-sb-scope="drawer-sub-empty">暂无子任务，添加一个开始拆解</li>
                  ) : subIssues.slice(0, 20).map((s) => (
                    <li key={s.id} className="flex items-center gap-2 text-[13px] group/row hover:bg-neutral-50 rounded px-1 py-0.5" data-sb-scope="drawer-sub-row">
                      <input
                        type="checkbox"
                        checked={s.state_group === "completed"}
                        aria-label={`完成子任务 ${s.name}`}
                        disabled={togglingSubId === s.id || Boolean(issue.archived_at) || !canWrite}
                        onChange={() => void toggleSub(s)}
                        className="accent-brand-500 w-[15px] h-[15px] shrink-0"
                      />
                      <span className={`flex-1 min-w-0 truncate ${s.state_group === "completed" ? "line-through text-neutral-400" : ""}`}>{s.name}</span>
                      {/* 状态圆点（C.41：标题 + 状态圆点 + 复选完成） */}
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: STATE_COLOR[s.state_group] ?? "#9ca3af" }} aria-label={`状态 ${s.state_name ?? s.state_group}`} />
                      <span className="font-mono text-[11px] text-neutral-400">{s.issue_key}</span>
                    </li>
                  ))}
                </ul>
                {/* 添加行（C.24 文案沿用，R3 裁决）：回车保存；TASK-004 起多层可挂（深度 ≤5 由后端 409 兜底） */}
                {canWrite && !issue.archived_at && (
                <div className="flex items-center gap-1.5 border border-dashed border-neutral-300 h-8 mt-2 px-2.5 rounded-md text-neutral-500 focus-within:border-brand-500">
                  <span>+</span>
                  <input
                    ref={subInputRef}
                    className="flex-1 bg-transparent outline-none text-[13px]"
                    placeholder="添加子任务，回车保存…"
                    aria-label="子任务标题"
                    value={newSubName}
                    onChange={(e) => setNewSubName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSubIssue(); } }}
                  />
                </div>
                )}
              </div>

              {/* 关联分区（C.42 / TASK-005 §3.1）：三分组固定顺序；空组不渲染；计数 (N) ≥40 变 amber */}
              <div className="mt-5 border-t border-neutral-200 pt-3" data-sb-scope="drawer-rel-section">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-500"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                    关联 {relTotal > 0 ? <span className={relTotal >= 40 ? "text-amber-700 font-semibold" : "text-neutral-400"}>({relTotal})</span> : <span className="text-neutral-400">—</span>}
                  </span>
                  {relTotal >= 40 && <span className="text-[12px] text-amber-700">接近 50 上限</span>}
                  <div className="ml-auto">
                    {canWrite && !issue.archived_at && (
                      <button onClick={() => setLinkOpen(true)} data-sb-scope="drawer-rel-add"
                        className="h-[28px] px-2.5 border border-neutral-300 rounded-md text-[12px] text-neutral-600 hover:bg-neutral-50 inline-flex items-center gap-1">
                        <span aria-hidden="true">＋</span> 添加关联
                      </button>
                    )}
                  </div>
                </div>
                {/* C.42 空态：无任何关联收缩为一行「关联 — [+ 添加]」 */}
                {relTotal === 0 ? (
                  canWrite && !issue.archived_at ? (
                    <div className="text-[13px] text-neutral-400">
                      关联 — <button className="text-brand-600 hover:text-brand-700" onClick={() => setLinkOpen(true)} data-sb-scope="drawer-rel-add-inline">＋ 添加</button>
                    </div>
                  ) : <div className="text-[13px] text-neutral-400">关联 —</div>
                ) : (
                  <>
                    {/* C.42 三分组：阻塞于此（前置）/ 阻塞（后置）/ 相关（relates+duplicates 合并，duplicates 加角标） */}
                    {([
                      { key: "pre", title: "阻塞于此（前置）", rows: relGroups.pre },
                      { key: "post", title: "阻塞（后置）", rows: relGroups.post },
                      { key: "rel", title: "相关", rows: relGroups.rel },
                    ] as const).filter((g) => g.rows.length > 0).map((g) => (
                      <div key={g.key} className="mb-2.5" role="group" aria-label={g.title} data-sb-scope="drawer-rel-group" data-group={g.key}>
                        <div className="text-[12px] font-semibold text-neutral-600 mb-1">{g.title}</div>
                        {g.rows.map((x) => {
                          const unfinished = x.related_issue.state_group !== "completed" && x.related_issue.state_group !== "cancelled";
                          return (
                            <div key={x.id} className="flex items-center gap-2 min-h-8 px-2 rounded-md text-[13px] hover:bg-neutral-50 group/rel" data-sb-scope="drawer-rel-row" data-rel-id={x.id}>
                              {/* C.42 阻塞语义强化：未完成前置 alert-triangle(amber)；已完成 check(green) */}
                              {x.relation_type === "is_blocked_by" && (
                                <span className={unfinished ? "text-amber-500" : "text-emerald-500"} aria-hidden="true">{unfinished ? "⚠" : "✓"}</span>
                              )}
                              <span className="font-mono text-[12px] text-neutral-400">{x.related_issue.issue_key}</span>
                              <span className="flex-1 min-w-0 truncate">
                                {x.related_issue.name}
                                {x.relation_type === "duplicates" && <span className="ml-1.5 text-[11px] text-neutral-400 bg-neutral-100 rounded px-1.5" data-sb-scope="drawer-rel-dup-badge">重复于</span>}
                              </span>
                              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: STATE_COLOR[x.related_issue.state_group] ?? "#9ca3af" }} aria-label={`状态 ${x.related_issue.state_group}`} />
                              {/* C.42 跳转箭头：点击跳目标详情，保留返回栈（嵌套抽屉 / 宿主回调） */}
                              <button aria-label={`打开 ${x.related_issue.name}`} title={x.related_issue.name}
                                onClick={() => openNested(x.related_issue_id)}
                                className="w-6 h-6 inline-flex items-center justify-center text-neutral-400 hover:text-brand-600">→</button>
                              {canWrite && !issue.archived_at && (
                                <button aria-label={`删除关联 ${x.related_issue.issue_key}`} data-sb-scope="drawer-rel-del"
                                  onClick={() => void delRelation(x.id)}
                                  className="opacity-0 group-hover/rel:opacity-100 w-6 h-6 inline-flex items-center justify-center text-neutral-400 hover:text-red-600 hover:bg-red-50 rounded">✕</button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </>
                )}
              </div>

              {/* 工时分区（C.46 / TASK-006 §3.1）：估算 + 已耗主数字 + ⊕含子任务开关 + 进度条 + 记录列表 */}
              <div className="mt-5 border-t border-neutral-200 pt-3" data-sb-scope="drawer-worklog-section">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-500"><circle cx="12" cy="14" r="8"/><path d="M12 14l3-3M10 2h4"/></svg>
                    工时
                  </span>
                  <div className="ml-auto">
                    {canWrite && !issue.archived_at && (
                      <button onClick={() => setWlDialog({ edit: null })} data-sb-scope="drawer-worklog-add"
                        className="h-[28px] px-2.5 border border-neutral-300 rounded-md text-[12px] text-neutral-600 hover:bg-neutral-50 inline-flex items-center gap-1">
                        <span aria-hidden="true">⏱</span> 记工时
                      </button>
                    )}
                  </div>
                </div>
                {/* Sprint-5（INTG-001 §3.3）：任务详情 GitHub 区块——commits/PRs 内联聚合（无内容整块不渲染） */}
                {(issue.github_context?.commits?.length || issue.github_context?.prs?.length) ? (
                  <div className="mb-2 -mt-1 rounded-lg border border-neutral-200 bg-neutral-50/60 px-3 py-2" data-sb-scope="drawer-github-section">
                    <div className="text-[12px] font-medium text-neutral-600 flex items-center gap-1.5 mb-1">
                      <span aria-hidden="true" className="inline-block w-4 h-4 rounded bg-neutral-900 text-white text-[10px] text-center leading-4">G</span>
                      GitHub
                      {issue.github_context?.number != null && (
                        <a className="ml-auto text-neutral-400 hover:text-brand-600 font-mono" href={`https://github.com/issues/${issue.github_context.number}`}
                          target="_blank" rel="noreferrer">#{issue.github_context.number}</a>
                      )}
                    </div>
                    <ul className="space-y-0.5">
                      {(issue.github_context?.prs ?? []).map((pr) => (
                        <li key={`pr-${pr.number}`} className="text-[12.5px] text-neutral-600 flex items-center gap-1.5" data-sb-scope="drawer-github-pr">
                          <span aria-hidden="true">⇄</span>
                          <span className="truncate">#{pr.number} {pr.title}</span>
                          {pr.merged_at && <span className="text-purple-600 text-[11px] shrink-0">已合并</span>}
                        </li>
                      ))}
                      {(issue.github_context?.commits ?? []).map((c) => (
                        <li key={c.sha} className="text-[12.5px] text-neutral-600 flex items-center gap-1.5" data-sb-scope="drawer-github-commit" data-sha={c.sha}>
                          <span aria-hidden="true">▣</span>
                          <span className="font-mono text-[11.5px] text-neutral-400 shrink-0">{c.sha}</span>
                          <span className="truncate">{c.message}</span>
                          {c.author && <span className="text-neutral-400 text-[11px] shrink-0">{c.author}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {/* C.46 空态：无估算无记录 → 分区一行「工时 — [⏱ 记工时]」；估算 placeholder「设估算」 */}
                {spentTotal === 0 && issue.estimate_minutes == null && worklogs.length === 0 ? (
                  /* C.46 空态：无估算无记录 → 分区一行「工时 — [设估算] [⏱ 记工时]」 */
                  <div className="text-[13px] text-neutral-400 flex items-center gap-2">
                    工时 —
                    {canWrite && !issue.archived_at && (
                      <span className="relative">
                        <button onClick={() => setEstMenuOpen((v) => !v)} data-sb-scope="drawer-est-set"
                          className="h-7 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50">设估算</button>
                        {estMenuOpen && <EstimateMenu onPick={(m) => void setEstimate(m)} />}
                      </span>
                    )}
                    {canWrite && !issue.archived_at && <button className="text-brand-600 hover:text-brand-700" onClick={() => setWlDialog({ edit: null })} data-sb-scope="drawer-worklog-add-inline">⏱ 记工时</button>}
                  </div>
                ) : (
                  <div className="grid grid-cols-[80px_1fr] gap-x-2.5 gap-y-1 items-center">
                    <span className="text-[13px] text-neutral-400">估算</span>
                    <span>
                      {issue.estimate_minutes == null ? (
                        canWrite && !issue.archived_at ? (
                          <div className="relative">
                            <button onClick={() => setEstMenuOpen((v) => !v)} data-sb-scope="drawer-est-set"
                              className="h-7 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50">设估算</button>
                            {estMenuOpen && <EstimateMenu onPick={(m) => void setEstimate(m)} />}
                          </div>
                        ) : <span className="text-neutral-400">—</span>
                      ) : (
                        <div className="relative">
                          <button onClick={() => setEstMenuOpen((v) => !v)} data-sb-scope="drawer-est-menu"
                            className="h-7 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50 inline-flex items-center gap-1 font-mono">
                            {fmtMinutes(issue.estimate_minutes)} <span aria-hidden="true" className="text-neutral-400">▾</span>
                          </button>
                          {estMenuOpen && <EstimateMenu onPick={(m) => void setEstimate(m)} />}
                        </div>
                      )}
                    </span>
                    <span className="text-[13px] text-neutral-400">已耗</span>
                    <span className="flex items-baseline gap-2.5 flex-wrap">
                      {/* C.46 已耗主数字：fmtMinutes（1d=8h）；子树口径为次级行 */}
                      <span className={`text-[22px] font-semibold tabular-nums ${wlOver ? "text-red-600" : ""}`} data-sb-scope="drawer-worklog-spent">
                        {fmtMinutes(wlCurrent)}
                      </span>
                      {/* C.46 ⊕含子任务开关（默认本任务口径；切换显「本任务 X · 子树估算 Y」） */}
                      {!issue.archived_at && (
                        <label className="inline-flex items-center gap-1 text-[12px] text-neutral-400 cursor-pointer select-none">
                          <input type="checkbox" className="accent-brand-500" checked={!wlScopeSelf}
                            onChange={(e) => setWlScopeSelf(!e.target.checked)} data-sb-scope="drawer-worklog-scope" />
                          ⊕含子任务
                        </label>
                      )}
                      {!wlScopeSelf && (
                        <span className="text-[12px] text-neutral-400">
                          本任务 {fmtMinutes(spentTotal)} · 子树估算 {fmtMinutes(wlSubtreeStats?.est ?? null)}
                        </span>
                      )}
                      {/* C.46 超耗红显：超耗 +X + aria-label「超出估算 N%」 */}
                      {wlOver && (
                        <span className="text-[12px] text-red-600" role="img" aria-label={`超出估算 ${wlPct - 100}%`} data-sb-scope="drawer-worklog-over">
                          超耗 +{fmtMinutes(spentTotal - (issue.estimate_minutes ?? 0))}
                        </span>
                      )}
                    </span>
                  </div>
                )}
                {/* C.46 进度条：spent/estimate；estimate 空时隐藏；>100% 红色；role=progressbar */}
                {issue.estimate_minutes != null && issue.estimate_minutes > 0 && (spentTotal > 0 || worklogs.length > 0) && (
                  <div className="ml-[90px]">
                    <div className="h-2 rounded bg-neutral-100 overflow-hidden mt-2" role="progressbar"
                      aria-valuenow={spentTotal} aria-valuemin={0} aria-valuemax={issue.estimate_minutes}
                      aria-label={`工时进度 ${wlPct}%`}>
                      <div className={`h-full rounded transition-all ${wlOver ? "bg-red-500" : "bg-brand-500"}`} style={{ width: `${Math.min(100, wlPct)}%` }} />
                    </div>
                    <div className="text-[11px] text-neutral-400 mt-0.5">
                      {wlPct}%{wlOver ? `（超出 ${wlPct - 100}%）` : ""}
                    </div>
                  </div>
                )}
                {/* C.46 记录列表：日期/人/时长/备注(truncate)；⋯ 菜单 编辑/删除（仅本人或 PROJ_ADMIN） */}
                <div className="mt-2.5">
                  {worklogs.length > 0 && <div className="text-[12px] font-semibold text-neutral-400 mb-1">▾ 记录（{worklogs.length}）</div>}
                  {worklogs.length === 0 ? (
                    <div className="text-[13px] text-neutral-400">暂无记录</div>
                  ) : worklogs.map((w) => {
                    const mine = w.actor_id === myUserId;
                    const rowMenuAllowed = (mine || isAdmin) && canWrite && !issue.archived_at;
                    return (
                      <div key={w.id} className="flex items-center gap-2.5 min-h-[34px] px-1.5 rounded-md text-[13px] hover:bg-neutral-50 group/wl" data-sb-scope="drawer-worklog-row">
                        <span className="font-mono text-[12px] text-neutral-400 w-[86px] shrink-0">{w.worked_on}</span>
                        <span className="w-5 h-5 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">{initialOf(nameOfMember(w.actor_id))}</span>
                        <span className="w-[52px] shrink-0 truncate">{nameOfMember(w.actor_id)}</span>
                        <span className="font-mono font-medium w-11 text-right shrink-0 tabular-nums">{fmtMinutes(w.minutes)}</span>
                        <span className="flex-1 min-w-0 truncate text-neutral-400" title={w.note}>{w.note}</span>
                        {rowMenuAllowed && (
                          <span className="relative">
                            <button aria-label="记录操作" data-sb-scope="drawer-worklog-menu"
                              className="opacity-0 group-hover/wl:opacity-100 w-[26px] h-[26px] inline-flex items-center justify-center text-neutral-400 hover:text-neutral-700"
                              onClick={() => setWlMenuRow(wlMenuRow === w.id ? null : w.id)}>⋯</button>
                            {wlMenuRow === w.id && (
                              <div role="menu" className="absolute right-0 top-6 z-10 w-[120px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" data-sb-scope="drawer-worklog-row-menu">
                                <button role="menuitem" data-sb-scope="drawer-worklog-edit"
                                  onClick={() => { setWlMenuRow(null); setWlDialog({ edit: w }); }}
                                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">编辑</button>
                                <button role="menuitem" data-sb-scope="drawer-worklog-del"
                                  onClick={() => { setWlMenuRow(null); void delWorklog(w.id); }}
                                  className="w-full text-left px-3 h-8 text-[13px] text-red-600 hover:bg-red-50">删除</button>
                              </div>
                            )}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 动态字段区（C.55 / TASK-008 §3.4）：按 sort_order 渲染；超出首屏折叠「更多属性 ▾」 */}
              <div className="mt-5 border-t border-neutral-200 pt-3" data-sb-scope="drawer-cf-section">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-500"><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/></svg>
                    自定义字段
                  </span>
                </div>
                {/* C.55 Schema 加载失败 → 内置字段 +「自定义字段加载失败 · 重试」条 */}
                {cfError ? (
                  <div className="text-[13px] text-neutral-500">
                    自定义字段加载失败 · <button className="text-brand-600 hover:text-brand-700" onClick={() => refreshSchema()} data-sb-scope="drawer-cf-retry">重试</button>
                  </div>
                ) : activeFields.length === 0 ? (
                  <div className="text-[13px] text-neutral-400">暂无自定义字段</div>
                ) : (
                  <>
                    {(cfMoreOpen ? activeFields : activeFields.slice(0, 2)).map((f) => (
                      <CfRow key={f.id} field={f} value={cfValues[f.key]} editable={canWrite && !issue.archived_at} onSave={(v) => void setFieldValue(f.key, v)} members={members} />
                    ))}
                    {activeFields.length > 2 && (
                      <button className="text-brand-600 hover:text-brand-700 text-[13px] py-1" data-sb-scope="drawer-cf-more"
                        onClick={() => setCfMoreOpen((v) => !v)}>
                        {cfMoreOpen ? "收起属性 ▴" : `更多属性 ▾（${activeFields.length - 2}）`}
                      </button>
                    )}
                  </>
                )}
              </div>

              {/* 元信息（C.6） */}
              <div className="mt-5 border-t border-neutral-200 pt-3 text-xs text-neutral-400 flex gap-2 flex-wrap">
                <span>创建者 {issue.created_by?.name ?? "—"}</span>
                <span>· 创建于 {issue.created_at?.slice(0, 16).replace("T", " ")}</span>
                <span>· 最后更新 {issue.updated_at?.slice(0, 16).replace("T", " ")}</span>
              </div>
            </>
          )}

          {tab === "comments" && (
            /* COLLAB-002 §3.1 评论 Tab 线程化（C.84~C.88）：两层结构/折叠/反应栏/
               图片网格+灯箱/回复态 Composer/父删子留（C.32/C.33 锚点与 ⌘Enter 沿用）。 */
            <CommentThreadTab slug={slug} projectId={projectId} issueId={issueId}
              canComment={myRole >= 10} members={members} myUserId={myUserId} />
          )}

          {tab === "activity" && (
            <div data-sb-scope="drawer-activity">
              {/* C.61 过滤器：字段（全部▾）与操作人（⋯▾）双下拉；过滤态 URL 同源（本迭代不落 URL，菜单内选中态回显） */}
              <div className="flex justify-end gap-2 pt-2.5 pb-1">
                <div className="relative">
                  <button className="h-7 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50"
                    aria-haspopup="menu" aria-label="字段过滤器" data-sb-scope="act-field-toggle"
                    onClick={() => { setActFieldMenu((v) => !v); setActActorMenu(false); }}>
                    {actFieldFilter || "全部"} ▾
                  </button>
                  {actFieldMenu && (
                    <div role="menu" className="absolute right-0 top-8 z-10 w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" data-sb-scope="act-field-menu">
                      {([["全部", ""], ["状态", "state"], ["优先级", "priority"], ["工时", "worklog"], ["关联", "relation"]] as Array<[string, string]>).map(([n, v]) => (
                        <button key={n} role="menuitem" data-sb-scope="act-field-item"
                          onClick={() => { setActFieldFilter(v); setActFieldMenu(false); }}
                          className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 ${actFieldFilter === v ? "bg-brand-50 text-brand-600 font-medium" : ""}`}>{n}</button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="relative">
                  <button className="h-7 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50"
                    aria-haspopup="menu" aria-label="操作人过滤器" data-sb-scope="act-actor-toggle"
                    onClick={() => { setActActorMenu((v) => !v); setActFieldMenu(false); }}>
                    ⋯ ▾
                  </button>
                  {actActorMenu && (
                    <div role="menu" className="absolute right-0 top-8 z-10 w-[160px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" data-sb-scope="act-actor-menu">
                      <button role="menuitem" data-sb-scope="act-actor-item"
                        onClick={() => { setActActorFilter(""); setActActorMenu(false); }}
                        className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 ${!actActorFilter ? "bg-brand-50 text-brand-600 font-medium" : ""}`}>全部操作人</button>
                      {activityGroups.length > 0 && (
                        <div className="h-px bg-neutral-100 my-1" />
                      )}
                      {/* 操作人候选 = 当前时间线里出现过的 actor（C.61 ⋯▾；含 ⚙系统） */}
                      {Array.from(new Set(activityGroups.map((g) => g.actor?.display_name ?? "系统"))).map((n) => (
                        <button key={n} role="menuitem" data-sb-scope="act-actor-item"
                          onClick={() => { setActActorFilter(n); setActActorMenu(false); }}
                          className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 ${actActorFilter === n ? "bg-brand-50 text-brand-600 font-medium" : ""}`}>{n}</button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* C.61 空态：暂无动态——第一次修改将出现在这里 */}
              {activityGroups.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-10 text-neutral-400" data-sb-scope="drawer-activity-empty">
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
                  <div className="text-[15px] font-semibold text-neutral-600">暂无动态——第一次修改将出现在这里</div>
                </div>
              ) : (
                <>
                  <ol className="flex flex-col" data-sb-scope="drawer-activity-list">
                    {activityGroups.map((g, gi) => {
                      const day = dayLabelOf(g.created_at);
                      const prev = gi > 0 ? activityGroups[gi - 1] : undefined;
                      const prevDay = prev ? dayLabelOf(prev.created_at) : null;
                      const sys = !g.actor?.id;
                      const actorName = g.actor?.display_name ?? "系统";
                      return (
                        <li key={g.id} aria-label={g.comment ?? g.verb}>
                          {/* C.61 日期分区 sticky 头（今天 / 昨天 / M月d日） */}
                          {day !== prevDay && (
                            <div className="sticky top-0 bg-white text-[12px] text-neutral-400 border-b border-neutral-200 py-2.5 mt-2 first:mt-0 z-[1]" data-sb-scope="act-day">{day}</div>
                          )}
                          {/* C.61 epoch 组：组头行（时间 mono + 头像 + 操作者 + 动作摘要）+ 缩进字段行 */}
                          <div className="relative ml-2 pl-5 border-l-2 border-neutral-200 py-2" data-sb-scope="act-group">
                            <div className="flex items-center gap-2 text-[13px]">
                              <span className="font-mono text-[12px] text-neutral-400 w-11 shrink-0">{(g.created_at ?? "").slice(11, 16)}</span>
                              {sys ? (
                                <span className="text-neutral-400" title="系统事件" aria-label="系统事件" data-sb-scope="act-sys-avatar">⚙系统</span>
                              ) : (
                                <span className="w-5 h-5 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">{initialOf(actorName)}</span>
                              )}
                              <span className={`font-medium ${sys ? "text-neutral-400" : "text-neutral-900"}`}>{actorName}</span>
                              <span className="text-neutral-600">{g.comment || verbLabel(g.verb)}</span>
                            </div>
                            {(g.items ?? []).map((it, i) => (
                              <div key={i} className="flex flex-wrap items-baseline gap-2 pt-0.5 pl-[22px] text-[12.5px] text-neutral-400" data-sb-scope="act-field-row">
                                <span className="text-neutral-600">{it.field_label ?? it.field}</span>
                                {it.old_value != null && it.old_value !== "" ? <span className="line-through">{it.old_value}</span> : <span>—</span>}
                                <span aria-hidden="true">→</span>
                                <span className="font-semibold text-neutral-900">{it.new_value ?? "—"}</span>
                              </div>
                            ))}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                  {/* C.61 按钮式「加载更早的动态」（审计翻阅场景，不做无限滚动）【R4】 */}
                  {activityHasMore && (
                    <div className="flex justify-center py-4">
                      <button onClick={() => loadActivityGroups(true)} data-sb-scope="act-load-earlier"
                        className="h-[28px] px-3 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50">加载更早的动态</button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {tab === "attachments" && (
            <div data-sb-scope="drawer-attachments">
              {/* C.31 附件 Tab：上传区 + 文件行 + 下载/删除 */}
              {/* 区块头（C.31）：「附件 N」+「＋ 上传附件」 */}
              <div className="flex items-center justify-between mb-3">
                <span className="text-[13px] font-medium" data-sb-scope="drawer-attachments-count">
                  附件 {attachments.length}
                </span>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="h-[30px] px-3 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600"
                  aria-label="上传附件"
                  data-sb-scope="drawer-attachments-upload"
                >＋ 上传附件</button>
              </div>
              {/* 拖拽区（C.31）：常驻虚线框 + 文案 */}
              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) void uploadFile(f); }}
                className="border-2 border-dashed border-neutral-300 rounded-md py-6 text-center text-[13px] text-neutral-500 hover:border-brand-400 cursor-pointer"
                role="button"
                aria-label="上传附件"
                data-sb-scope="drawer-attachments-drop"
              >
                {uploadingFile ? `上传中… ${uploadPct}%` : "拖拽文件到此处，或点击选择（单文件 ≤ 25MB）"}
              </div>
              <input ref={fileInputRef} type="file" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadFile(f); e.target.value = ""; }} />
              {/* 文件行（C.31） */}
              {attachments.length === 0 ? (
                <ul className="mt-3 flex flex-col gap-1 text-[13px] text-neutral-400">
                  <li data-sb-scope="drawer-attachments-empty">暂无附件</li>
                </ul>
              ) : (
                <ul className="mt-3 flex flex-col gap-1.5">
                  {attachments.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-center gap-2 px-2 py-1.5 border border-neutral-200 rounded-md hover:bg-neutral-50"
                      data-sb-scope="drawer-attachments-row"
                    >
                      {/* MIME → 图标（C.31） */}
                      <span aria-hidden="true" className="w-5 h-5 text-neutral-500 inline-flex items-center justify-center text-[16px]">{mimeIcon(a.mime)}</span>
                      <span className="flex-1 truncate text-[13px]" title={a.name}>{a.name}</span>
                      <span className="text-[12px] text-neutral-500">{humanSize(a.size)}</span>
                      <span className="text-[12px] text-neutral-400">{a.created_at?.slice(0, 16).replace("T", " ")}</span>
                      <button
                        aria-label={`下载 ${a.name}`}
                        className="w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-brand-600 hover:bg-neutral-100 rounded"
                        onClick={() => {
                          // C.31：换发下载端点 → 浏览器跟随 302
                          window.location.href = a.download_url;
                        }}
                      >⬇</button>
                      <button
                        aria-label={`删除 ${a.name}`}
                        className="w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-red-600 hover:bg-red-50 rounded"
                        onClick={async () => {
                          if (!confirm(`删除 ${a.name}？`)) return;
                          try {
                            await AttachmentAPI.del(slug, projectId, issueId, a.id);
                            setAttachments((cur) => cur.filter((x) => x.id !== a.id));
                          } catch { toast("删除失败", "error"); }
                        }}
                      >🗑</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* 全屏树抽屉（TASK-004 §3.3/C.40）：分区头「查看全部 N 个 →」打开；z-[60] 盖住本抽屉 */}
      {treeOpen && (
        <IssueTreeDrawer
          issueId={issueId}
          issueName={issue.name}
          slug={slug}
          projectId={projectId}
          onClose={() => setTreeOpen(false)}
          onOpenIssue={(id) => setTreeNodeIssueId(id)}
          onAddFirst={() => { setTreeOpen(false); subInputRef.current?.focus(); }}
        />
      )}

      {/* 树里点开的节点详情（z-[70] 盖住树；关闭回树，树状态保留——§3.3「返回时树状态保留」） */}
      {treeNodeIssueId && (
        <IssueDrawer
          issueId={treeNodeIssueId}
          slug={slug}
          projectId={projectId}
          layer="z-[70]"
          onClose={() => setTreeNodeIssueId(null)}
          onChanged={() => { void refresh(); onChanged?.(); }}
        />
      )}

      {/* Sprint-7（WF-004 §3.1）：守卫拦截补齐对话框（400 结构化 → guard 键分区渲染） */}
      {guardDlg && (
        <GuardDialog
          ws={slug} projectId={projectId} issueId={issueId}
          transitionId={guardDlg.transitionId} toStateId={guardDlg.toStateId}
          transitionName={guardDlg.transitionName} failures={guardDlg.failures}
          members={members}
          onClose={() => setGuardDlg(null)}
          onDone={() => { void refresh(); onChanged?.(); }} />
      )}

      {/* ── Sprint-2 弹层 ── */}
      {/* M-LINK 添加关联（C.43） */}
      {linkOpen && (
        <AddRelationModal
          slug={slug} projectId={projectId} issueId={issueId} issueName={issue.name}
          excludedIds={relLinkedIds}
          onClose={() => setLinkOpen(false)}
          onCreated={() => { refreshRelations(); onChanged?.(); }}
        />
      )}
      {/* M-WORKLOG 工时填报/编辑（C.47） */}
      {wlDialog && (
        <WorkLogDialog
          slug={slug} projectId={projectId} issueId={issueId} issueName={issue.name}
          edit={wlDialog.edit ? { id: wlDialog.edit.id, minutes: wlDialog.edit.minutes, worked_on: wlDialog.edit.worked_on, note: wlDialog.edit.note } : null}
          onClose={() => setWlDialog(null)}
          onSaved={() => { refreshWorklogs(); void refresh(); onChanged?.(); }}
        />
      )}
      {/* M-ASSIGN 转交弹层（C.50） */}
      {assignOpen && (
        <AssigneePickerModal
          slug={slug} projectId={projectId} issueId={issueId} issueName={issue.name}
          currentIds={assigneeIds} myUserId={myUserId}
          onClose={() => setAssignOpen(false)}
          onSaved={() => { void refresh(); onChanged?.(); }}
        />
      )}
      {/* M-DUP 复制选项（C.57） */}
      {dupOpen && (
        <DuplicateDialog
          slug={slug} projectId={projectId} issueId={issueId}
          subCount={dupSubCount ?? subIssues.length}
          issue={{
            issue_key: issue.issue_key, name: issue.name, assigneeIds,
            labelCount: issueLabelIds.size,
            cfCount: Object.keys(cfValues).filter((k) => cfValues[k] != null && cfValues[k] !== false && cfValues[k] !== "").length,
            start: issue.start_date ?? null, target: issue.target_date ?? null,
          }}
          onClose={() => setDupOpen(false)}
          onCreated={(newId) => { onChanged?.(); openNested(newId); }}
        />
      )}
      {/* M-ARCH 归档确认 + 撤销 Toast（C.58） */}
      {archOpen && (
        <ArchiveConfirmDialog
          slug={slug} projectId={projectId} issueId={issueId}
          issueKey={issue.issue_key} issueName={issue.name}
          descendantCount={archDescCount}
          onClose={() => setArchOpen(false)}
          onArchived={() => { onClose(); onChanged?.(); }}
          onRestored={() => { onChanged?.(); }}
        />
      )}
      {/* M-BLOCKED 完成被拦截（C.44；详情状态菜单入口，管理员可强制完成） */}
      {blockedDlg && (
        <BlockedCompleteDialog
          issueName={blockedDlg.issueName}
          blockers={blockedDlg.blockers}
          isAdmin={isAdmin}
          onClose={() => setBlockedDlg(null)}
          onForce={async (comment) => {
            try {
              await IssueAPI.patch(slug, projectId, issueId, { state_id: blockedDlg.stateId, force: true, comment });
              toast("已强制完成（管理员通道）· 已记录说明", "warning");
              setBlockedDlg(null);
              await refresh(); saved(); onChanged?.();
            } catch (e: unknown) {
              const err = e as ApiError;
              toast(err?.details?.[0]?.message ?? err?.message ?? "强制完成失败", "error");
            }
          }}
          onJump={(b) => {
            const hit = relations.find((r) => r.related_issue.issue_key === b.issue_key);
            if (hit) openNested(hit.related_issue_id);
          }}
        />
      )}

      {confirmDel && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[60]">
          <div className="bg-white rounded-xl shadow-lg w-[420px] p-6">
            <div className="text-base font-semibold mb-3">删除任务</div>
            <div className="text-[13px] text-neutral-600 mb-5">
              {/* C.24 + TASK-004 §2.4：删除父任务整树级联，确认弹层明示后代数量（subtree stats 同源） */}
              {(delDescCount ?? subIssues.length) > 0
                ? `将同时删除 ${delDescCount ?? subIssues.length} 个子任务。确定删除 ${issue.issue_key}「${issue.name}」？此操作不可撤销。`
                : `确定删除 ${issue.issue_key}「${issue.name}」？此操作不可撤销。`
              }
            </div>
            <div className="flex justify-end gap-2.5">
              <button onClick={() => setConfirmDel(false)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
              <button onClick={del} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}