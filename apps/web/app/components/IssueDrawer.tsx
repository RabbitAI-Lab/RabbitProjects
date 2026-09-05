import { useEffect, useMemo, useRef, useState } from "react";
import {
  AttachmentAPI,
  CommentAPI,
  IssueAPI,
  IssueTypeAPI,
  LabelAPI,
  ProjectAPI,
  ProjectMemberAPI,
  unwrap,
  type ActivityRow,
  type AttachmentRow,
  type CommentRow,
} from "../services/api";
import { StateBadge } from "./StateBadge";
import { toast } from "./Toast";
import { MentionPop, useMentionTrigger, type MentionCandidate } from "./MentionPop";
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

/** 头像首字母：按 Unicode 码点切（Array.from），避免 emoji / 代理对被 slice(0,1) 切成半个乱码字符。 */
function initialOf(name?: string | null): string {
  const s = (name ?? "").trim();
  if (!s) return "?";
  return Array.from(s)[0] ?? "?";
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
export function IssueDrawer({ issueId, slug, projectId, onClose, onChanged }: {
  issueId: string; slug: string; projectId: string; onClose: () => void; onChanged?: () => void;
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
  const [comments, setComments] = useState<CommentRow[]>([]);
  const [activities, setActivities] = useState<ActivityRow[]>([]);
  const [attachments, setAttachments] = useState<AttachmentRow[]>([]);
  const [commentDraft, setCommentDraft] = useState("");
  const [newSubName, setNewSubName] = useState("");
  /** 日期控件的乐观值：先本地回显、失败再回滚（C.23「乐观更新徽章，失败回滚」）。
   *  若直接受控于 issue.*，用户选完日期到 refresh() 返回前这段会被 React 弹回旧值。 */
  const [startDraft, setStartDraft] = useState("");
  const [targetDraft, setTargetDraft] = useState("");
  const [labelsMenuOpen, setLabelsMenuOpen] = useState(false);
  /** C.23 属性行内编辑当前展开的下拉（状态 / 类型 / 优先级 / 负责人） */
  const [propMenu, setPropMenu] = useState<"state" | "type" | "priority" | "assignee" | null>(null);
  const [activityPage, setActivityPage] = useState(1);
  const [activityHasMore, setActivityHasMore] = useState(false);
  const labelsMenuRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // 候选池（C.33）：项目成员缓存（本文件内做最小占位实现：issue.assignee 视为 1 条；
  // 真实 PROJ-002 列表接入由后续 PR 补，本组件保持接口稳定）
  const mentionCandidates: MentionCandidate[] = useMemo(() => {
    const list: MentionCandidate[] = [];
    if (issue?.assignee) list.push({ id: issue.assignee.id, name: issue.assignee.name, email: "" });
    return list;
  }, [issue?.assignee]);

  const mention = useMentionTrigger({
    value: commentDraft,
    setValue: setCommentDraft,
    allCandidates: mentionCandidates,
  });

  function refresh() {
    return IssueAPI.detail(slug, projectId, issueId).then((r) => {
      setIssue(unwrap<Issue>(r));
    });
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
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [issue?.id, slug, projectId, issueId]);

  // 切到对应 tab 时拉数据（C.25 时间线 + C.31 附件）
  useEffect(() => {
    if (tab === "comments") {
      CommentAPI.list(slug, projectId, issueId)
        .then((r) => setComments(unwrap<typeof comments>(r) ?? []))
        .catch(() => {});
    }
    if (tab === "activity") {
      setActivityPage(1);
      IssueAPI.activities(slug, projectId, issueId, { per_page: 30 })
        .then((r) => {
          // 响应是裸数组；分页游标在 meta.next_cursor（由 axios 拦截器挂到 r.meta）
          setActivities(unwrap<typeof activities>(r) ?? []);
          setActivityHasMore(Boolean((r as unknown as { meta?: { next_cursor?: string | null } }).meta?.next_cursor));
        })
        .catch(() => {});
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

  /** C.23 属性行内编辑的统一落库：选中即提交，失败回滚并 toast（TASK-002 §3.7）。
   *  返回是否成功，供乐观更新的控件（日期）决定要不要回滚。 */
  async function patchIssue(payload: Parameters<typeof IssueAPI.patch>[3], failMsg: string): Promise<boolean> {
    setPropMenu(null);
    if (!issue) return false;
    try {
      await IssueAPI.patch(slug, projectId, issueId, payload);
      await refresh(); saved();
      return true;
    } catch (e: unknown) {
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

  async function del() {
    setConfirmDel(false); onClose();
    try {
      await IssueAPI.del(slug, projectId, issueId);
      toast(`已删除 ${issue?.issue_key ?? "任务"}`);
    } catch { toast("删除失败", "error"); }
    onChanged?.();
  }

  async function postComment() {
    const text = commentDraft.trim();
    if (!text) return;
    const optimistic: CommentRow = {
      id: "__opt__",
      actor: { id: null, display_name: "我", avatar_url: null },
      comment_html: text,
      is_edited: false,
      is_deleted: false,
      created_at: new Date().toISOString(),
      updated_at: null,
    };
    setComments((c) => [...c, optimistic]);
    setCommentDraft("");
    try {
      await CommentAPI.create(slug, projectId, issueId, { comment_html: text });
      // 重新拉以拿到真实 id + 时间
      const r = await CommentAPI.list(slug, projectId, issueId);
      setComments(unwrap<typeof comments>(r) ?? []);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "发表失败";
      toast(msg, "error");
      setComments((c) => c.filter((x) => x.id !== "__opt__"));
      setCommentDraft(text);
    }
  }

  async function loadMoreActivities() {
    const next = activityPage + 1;
    IssueAPI.activities(slug, projectId, issueId, { per_page: 30 * next })
      .then((r) => {
        setActivities(unwrap<typeof activities>(r) ?? []);
        setActivityHasMore(Boolean((r as unknown as { meta?: { next_cursor?: string | null } }).meta?.next_cursor));
        setActivityPage(next);
      })
      .catch(() => {});
  }

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
  // 姓名必须拿成员表解析 —— 直接读 issue.assignee 会恒为「未分配」。
  const assigneeIds = issue.assignee_ids ?? [];
  const assigneeMember = members.find((m) => m.user.id === assigneeIds[0]) ?? null;
  const assigneeName = assigneeMember?.user.display_name ?? (assigneeIds.length ? "…" : "未分配");
  const issueType = types.find((t) => t.id === typeId);
  const priority = issue.priority ?? null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <aside className="relative w-[720px] max-w-[calc(100vw-64px)] bg-white border-l border-neutral-200 shadow-lg flex flex-col" role="dialog" aria-modal="true" aria-label={`任务详情 ${issue.issue_key}`}>
        {/* 头部 */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-neutral-200">
          <button className="font-mono text-[13px] text-neutral-500 hover:text-brand-600"
            onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); }}
            title="点击复制编号">{issue.issue_key}</button>
          <div className="ml-auto flex items-center gap-1">
            <div className="relative" ref={menuRef} data-sb-scope="drawer-more-menu">
              <button aria-label="更多操作" onClick={() => setMenuOpen(!menuOpen)}
                className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:bg-neutral-100 rounded-md">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>
              </button>
              {menuOpen && (
                <div className="absolute top-9 right-0 w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 z-10">
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(location.origin + location.pathname + `?peekIssue=${issueId}`); toast("已复制链接"); setMenuOpen(false); }}>复制链接</button>
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); setMenuOpen(false); }}>复制编号</button>
                  <div className="h-px bg-neutral-200 my-1" />
                  <button className="w-full text-left px-3 h-8 text-[13px] text-red-600 hover:bg-red-50" onClick={() => { setMenuOpen(false); setConfirmDel(true); }}>删除任务</button>
                </div>
              )}
            </div>
            <button onClick={onClose} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
          </div>
        </div>

        {/* Tab 条（C.25 / C.31 / C.32）—— 四 Tab 全部可点 */}
        <div role="tablist" aria-label="任务详情视图" className="flex border-b border-neutral-200 px-5">
          {([
            ["desc", "描述"],
            ["comments", `💬 评论${comments.length ? " " + comments.length : ""}`],
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
              {/* 标题（C.23） */}
              {editing ? (
                <input autoFocus aria-label="任务标题" data-sb-scope="drawer-title-input"
                  className="w-full text-lg font-semibold border-b-2 border-brand-500 outline-none bg-transparent py-1"
                  value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={saveTitle}
                  onKeyDown={(e) => { if (e.key === "Enter") saveTitle(); if (e.key === "Escape") setEditing(false); }} />
              ) : (
                <h2 className="text-lg font-semibold py-1 cursor-text hover:bg-neutral-50 rounded -mx-2 px-2" title="点击编辑标题"
                  onClick={() => { setTitleDraft(issue.name); setEditing(true); }}>{issue.name}</h2>
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

              {/* 属性区七行（C.23）—— label 80px + 控件 行式布局 */}
              <div className="mt-4 border-t border-neutral-200 pt-4 grid grid-cols-[80px_1fr] gap-x-3 gap-y-3 items-center">
                {/* 以下六行均为「行内编辑 · 选中即提交」（C.23 / TASK-002 §3.2）。
                    此前全部渲染成纯文本，导致优先级 / 负责人 / 开始 / 截止（以及状态 / 类型）
                    在抽屉里根本改不了 —— 只有标签那一行是可用的。 */}

                <label className="text-[13px] text-neutral-500">状态</label>
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

                <label className="text-[13px] text-neutral-500">负责人</label>
                <button type="button" data-sb-scope="drawer-prop-menu" aria-label="修改负责人"
                  onClick={() => setPropMenu(propMenu === "assignee" ? null : "assignee")}
                  
                  className="group justify-self-start inline-flex items-center gap-1.5 text-[13px] hover:bg-brand-50 hover:text-brand-600 border border-transparent hover:border-brand-200 rounded px-1.5 py-0.5 -mx-1 transition">
                  {assigneeMember ? (
                    <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center"
                      style={{ background: "#3b82f6" }} aria-hidden="true">
                      {assigneeMember.user.display_name.slice(0, 1)}
                    </span>
                  ) : null}
                  <span className={assigneeMember ? "" : "text-neutral-400"}>{assigneeName}</span>
                  <span aria-hidden="true" className="text-neutral-400 text-[11px] group-hover:scale-110 transition">▾</span>
                </button>
                {propMenu === "assignee" && (
                  <PropMenu>
                    {members.map((m) => (
                      <MenuItem key={m.id} on={assigneeIds.includes(m.user.id)}
                        onClick={() => void patchIssue({ assignee_ids: [m.user.id] }, "更新负责人失败")}>
                        <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center"
                          style={{ background: "#3b82f6" }} aria-hidden="true">
                          {m.user.display_name.slice(0, 1)}
                        </span>
                        {m.user.display_name}
                      </MenuItem>
                    ))}
                    <MenuItem on={assigneeIds.length === 0}
                      onClick={() => void patchIssue({ assignee_ids: [] }, "更新负责人失败")}>
                      <span className="text-neutral-400">未分配</span>
                    </MenuItem>
                  </PropMenu>
                )}

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

                <label className="text-[13px] text-neutral-500">开始</label>
                <input type="date" aria-label="开始日期" data-sb-scope="drawer-attr-start"
                  className="justify-self-start h-7 border border-neutral-300 rounded px-1.5 text-[13px] bg-white hover:border-neutral-400"
                  value={startDraft}
                  onChange={(e) => {
                    const v = e.target.value;
                    setStartDraft(v);
                    void patchIssue({ start_date: v || null }, "更新开始日期失败")
                      .then((ok) => { if (!ok) setStartDraft(issue.start_date ?? ""); });
                  }} />

                <label className="text-[13px] text-neutral-500">截止</label>
                <input type="date" aria-label="截止日期" data-sb-scope="drawer-attr-target"
                  className="justify-self-start h-7 border border-neutral-300 rounded px-1.5 text-[13px] bg-white hover:border-neutral-400"
                  value={targetDraft}
                  onChange={(e) => {
                    const v = e.target.value;
                    setTargetDraft(v);
                    void patchIssue({ target_date: v || null }, "更新截止日期失败")
                      .then((ok) => { if (!ok) setTargetDraft(issue.target_date ?? ""); });
                  }} />
              </div>

              {/* 子任务区（C.24） */}
              <div className="mt-5 border-t border-neutral-200 pt-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium">子任务</span>
                  <span className="text-[12px] text-neutral-500" data-sb-scope="drawer-sub-progress">
                    {subIssues.filter((s) => s.state_group === "completed").length}/{subIssues.length}
                  </span>
                  {/* 8px 进度微条（C.24）—— 全完成变绿 */}
                  {subIssues.length > 0 && (
                    <div className="ml-2 h-1 w-[120px] bg-neutral-200 rounded-full overflow-hidden" aria-hidden="true">
                      <div
                        className="h-full bg-emerald-500 transition-all"
                        style={{ width: `${(subIssues.filter((s) => s.state_group === "completed").length / subIssues.length) * 100}%` }}
                      />
                    </div>
                  )}
                </div>
                <ul className="flex flex-col gap-1">
                  {subIssues.length === 0 ? (
                    <li className="text-[13px] text-neutral-400 py-1" data-sb-scope="drawer-sub-empty">暂无子任务，添加一个开始拆解</li>
                  ) : subIssues.map((s) => (
                    <li key={s.id} className="flex items-center gap-2 text-[13px]" data-sb-scope="drawer-sub-row">
                      <input
                        type="checkbox"
                        checked={s.state_group === "completed"}
                        aria-label={`完成子任务 ${s.name}`}
                        disabled={togglingSubId === s.id}
                        onChange={() => void toggleSub(s)}
                        className="accent-brand-500"
                      />
                      <span className={s.state_group === "completed" ? "line-through text-neutral-400" : ""}>{s.name}</span>
                      <span className="ml-auto font-mono text-[11px] text-neutral-400">{s.issue_key}</span>
                    </li>
                  ))}
                </ul>
                {/* 输入行常驻（C.24「＋ 添加子任务」）—— 仅父任务层级展示，MVP 阶段子任务仅支持一层 */}
                {(issue as unknown as { parent_issue_id?: string | null }).parent_issue_id == null && (
                  <div className="flex items-center gap-1.5 border border-dashed border-neutral-300 h-8 mt-2 px-2.5 rounded-md text-neutral-500 focus-within:border-brand-500">
                    <span>+</span>
                    <input
                      className="flex-1 bg-transparent outline-none text-[13px]"
                      placeholder="添加子任务，回车保存…"
                      value={newSubName}
                      onChange={(e) => setNewSubName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSubIssue(); } }}
                    />
                  </div>
                )}
                {(issue as unknown as { parent_issue_id?: string | null }).parent_issue_id != null && (
                  <div className="mt-2 px-2.5 py-1.5 bg-amber-50 border border-amber-200 text-amber-800 text-[12px] rounded" data-sb-scope="drawer-sub-limit">
                    MVP 阶段子任务仅支持一层
                  </div>
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
            <div>
              {/* 评论列表（C.32） */}
              <ul className="flex flex-col gap-3" data-sb-scope="drawer-comments-list">
                {comments.length === 0 && (
                  <li className="text-[13px] text-neutral-500 py-4" data-sb-scope="drawer-comments-empty">
                    还没有评论
                  </li>
                )}
                {comments.map((c) => (
                  <li key={c.id} className="flex gap-2" data-sb-scope="drawer-comment-row">
                    {/* actor 只有 display_name / avatar_url（见 CommentSerializer.get_actor）；
                        旧代码读 actor.name → undefined → 兜底渲染成「?」 */}
                    {c.actor?.avatar_url ? (
                      <img src={c.actor.avatar_url} alt="" className="w-7 h-7 rounded-full object-cover shrink-0" />
                    ) : (
                      <span className="w-7 h-7 rounded-full bg-neutral-200 text-neutral-700 text-[11px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">
                        {initialOf(c.actor?.display_name)}
                      </span>
                    )}
                    <div className="flex-1">
                      <div className="text-[12px] text-neutral-500">
                        <b className="text-neutral-900">{c.actor?.display_name ?? "已注销用户"}</b>
                        <span className="ml-2">{c.created_at?.slice(0, 16).replace("T", " ")}</span>
                        {c.updated_at && <span className="ml-2" data-sb-scope="drawer-comment-edited">已编辑</span>}
                      </div>
                      {c.is_deleted ? (
                        <div className="text-[13px] text-neutral-400 italic" data-sb-scope="drawer-comment-deleted">该评论已删除</div>
                      ) : (
                        <div
                          className="text-[13px] mt-0.5"
                          // C.33 锚点渲染：<span data-mention-id data-primary-600 蓝字>
                          dangerouslySetInnerHTML={{ __html: c.comment_html }}
                        />
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              {/* 输入框 + ⌘Enter + @ 补全（C.32 + C.33） */}
              <div className="mt-4 border-t border-neutral-200 pt-3 relative">
                {/* @ 补全浮层（C.33）—— 父容器 relative，浮层 absolute bottom-full */}
                {mention.isOpen && (
                  <MentionPop
                    query={mention.query}
                    candidates={mention.candidates}
                    onPick={mention.onPick}
                  />
                )}
                {mention.filteredEmpty && (
                  <div
                    data-sb-scope="mention-pop-empty"
                    className="absolute bottom-full left-0 mb-1.5 w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-3 text-[13px] text-neutral-500 text-center z-30"
                  >无成员</div>
                )}
                {/* 工具条（精简：@ B I 💬 🔗 —— C.32） */}
                <div className="flex gap-0.5 px-2 py-1.5 mb-1.5 border border-neutral-200 rounded-md bg-neutral-50 text-[13px] text-neutral-500" aria-hidden>
                  {["@", "B", "I", "💬", "🔗"].map((t, i) => (
                    <span key={i} className="min-w-[26px] h-6 inline-flex items-center justify-center rounded px-1">{t}</span>
                  ))}
                </div>
                <textarea
                  aria-label="评论"
                  data-sb-scope="drawer-comment-input"
                  className="w-full border border-neutral-200 rounded-md p-2 text-[13px] min-h-[60px] focus:outline-none focus:border-brand-500"
                  placeholder="评论…（⌘Enter 发表）"
                  value={commentDraft}
                  onChange={(e) => {
                    const ta = e.target as HTMLTextAreaElement;
                    mention.onChangeWithMention(ta.value, ta.selectionStart);
                  }}
                  onKeyDown={(e) => {
                    if (mention.onKeyDown(e)) return;
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                      e.preventDefault();
                      postComment();
                    }
                  }}
                />
                <div className="flex items-center justify-between mt-2">
                  <span className="text-[12px] text-neutral-400" data-sb-scope="drawer-comment-counter">
                    {commentDraft.length}/5000
                  </span>
                  <button
                    onClick={postComment}
                    disabled={!commentDraft.trim()}
                    className="h-[30px] px-3 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50"
                    data-sb-scope="drawer-comment-submit"
                  >发表</button>
                </div>
              </div>
            </div>
          )}

          {tab === "activity" && (
            <div>
              {/* 时间线聚合（C.25） */}
              {activities.length === 0 ? (
                <div className="text-[13px] text-neutral-500 py-8 text-center" data-sb-scope="drawer-activity-empty">暂无操作记录</div>
              ) : (
                <>
                  <ol className="flex flex-col gap-2" data-sb-scope="drawer-activity-list">
                    {activities.map((a) => (
                      <li key={a.id} className="flex gap-2 text-[13px]">
                        {/* 后端行内是平铺的 actor_name（不是嵌套 actor 对象），
                            epoch 是毫秒浮点而非 ISO 串——旧代码两处都取错，导致恒显示「系 / 系统」 */}
                        <span className="w-6 h-6 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">
                          {initialOf(a.actor_name)}
                        </span>
                        <div className="flex-1">
                          <span className="text-neutral-900">{a.actor_name ?? "系统"}</span>
                          <span className="text-neutral-500 ml-1.5">{a.verb}{a?.field ? ` ${a.field}` : ""}{a?.old_value != null && a?.new_value != null ? ` 从 ${a.old_value} 改为 ${a.new_value}` : ""}</span>
                          <span className="ml-2 text-neutral-400 text-[12px]">{a.created_at?.slice(0, 16).replace("T", " ")}</span>
                        </div>
                      </li>
                    ))}
                  </ol>
                  {/* 游标 30 条/页 + 底部「── 加载更多 ──」 */}
                  {activityHasMore && (
                    <div className="text-center mt-3">
                      <button
                        onClick={() => void loadMoreActivities()}
                        className="text-[13px] text-neutral-500 hover:text-neutral-900 px-4 py-1.5 border-t border-b border-dashed border-neutral-200"
                        data-sb-scope="drawer-activity-more"
                      >── 加载更多 ──</button>
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

      {confirmDel && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[60]">
          <div className="bg-white rounded-xl shadow-lg w-[420px] p-6">
            <div className="text-base font-semibold mb-3">删除任务</div>
            <div className="text-[13px] text-neutral-600 mb-5">
              {/* C.24 删除父任务：提示「将同时删除 N 个子任务」 */}
              {subIssues.length > 0
                ? `将同时删除 ${subIssues.length} 个子任务。确定删除 ${issue.issue_key}「${issue.name}」？此操作不可撤销。`
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