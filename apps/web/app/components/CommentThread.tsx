import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { liveEventBus } from "@rp/shared-state";
import type { CommentCreatedPayload, LiveEnvelope } from "@rp/types";
import {
  AttachmentAPI,
  CommentAPI,
  unwrap,
  type CommentImageMeta,
  type CommentReactionAgg,
  type CommentRow,
} from "../services/api";
import type { ApiError } from "../services/axios";
import { toast } from "./Toast";
import { MentionPop, useMentionTrigger, type MentionCandidate } from "./MentionPop";

/** COLLAB-002 §3.1~§3.3 评论 Tab 线程化（C.84~C.88）——升级 COLLAB-001 扁平评论区。
 *  - 两层结构：顶层 32px 头像 / replies 区 32px 缩进 + 2px 引导线 + 浅底圆角 / 回复行
 *    24px 头像 +「回复」徽标 + reply_to_actor 归并语境 + sr-only 归并提示（§1.3）。
 *  - 折叠条：>3 条回复默认显示前 2 + 「⊕ 查看另外 N 条回复…」（阈值 3，会话记忆）。
 *  - 反应栏：chips 乐观 toggle（失败静默回滚重试一次）+ ➕ 选择器 24 枚 + 名单浮层
 *    （?expand=reactions 拉 user_ids，前 5 人 + 等 N 人 +（你））。
 *  - 图片：粘贴/拖入/🖼 选择 → presign entity_type=comment_image → 直传进度 → 发表时
 *    插入 image 节点（src 受控锚定 download/?variant=thumb）；缩略网格 96px（2/3 列）+
 *    GIF 角标 + 灯箱（全屏/←→/Esc/底部信息）。
 *  - 回复态 Composer：↩ 回复@xx ▾（切换目标/清除）+ @ 预填可删（BR-04）+ ⌘Enter 乐观插入
 *    失败回滚草稿保留 + 0/5000 计数。父删子留：is_deleted 行 + replies 保留（BR-06）。 */

/** COLLAB-002 §4.3.1 EMOJI_WHITELIST（24 枚，常驻 8 + 展开 16——§2.6）。 */
export const EMOJI_WHITELIST = [
  "👍", "👎", "❤️", "😂", "🎉", "🚀", "👀", "✅",
  "😕", "😡", "🤔", "👏", "🔥", "💯", "😢", "🙏",
  "⛔", "⏰", "🍀", "📌", "🔁", "❓", "💤", "🎯",
] as const;
/** 折叠阈值（§2.6：线程 > 3 条回复折叠）。 */
const FOLD_THRESHOLD = 3;
/** 评论图片域收紧（BR-08：≤5MB png/jpg/jpeg/gif/webp，第 10 张拒绝）。 */
const IMG_MAX_BYTES = 5 * 1024 * 1024;
const IMG_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".webp"];
const MAX_IMAGES = 9;
/** 上传节点 key 生成（模块级：组件体内调用 impure 函数会被 purity 规则拦截）。 */
let imgKeySeq = 0;
function nextImgKey(): string {
  imgKeySeq += 1;
  return `img-${Date.now()}-${imgKeySeq}`;
}

/** 从净化后的 comment_html 解析图片（src 受控 download/?variant=thumb；alt=文件名）。 */
export function parseCommentImages(html: string): CommentImageMeta[] {
  const out: CommentImageMeta[] = [];
  const re = /<img\s[^>]*>/g;
  for (const tag of html.match(re) ?? []) {
    const src = /src="([^"]*)"/.exec(tag)?.[1] ?? "";
    const alt = /alt="([^"]*)"/.exec(tag)?.[1] ?? "";
    const m = /\/attachments\/([0-9a-fA-F-]{36})\/download\//.exec(src);
    if (!m?.[1]) continue;
    out.push({ assetId: m[1], name: alt || "图片", gif: /\.gif$/i.test(alt || src), thumb: src.includes("variant=thumb"), src });
  }
  return out;
}

/** 正文渲染 = comment_html 去掉 img（图片进缩略网格，§3.1 mockup 布局）。 */
function stripImgTags(html: string): string {
  return html.replace(/<img\s[^>]*>/g, "");
}

interface DrawerMember { id: string; user: { id: string; display_name: string; avatar_url: string | null } }

/** 回复态上下文（COLLAB-002 §4.4 ReplyContext）：rootId + 被回复人。 */
interface ReplyCtx { rootId: string; replyToUserId: string | null; replyToName: string }
/** Composer 图片上传节点（§2.3 时序：presign → 直传 → complete；file 留作重试）。 */
interface PendingImage { key: string; name: string; progress: number; file: File; assetId?: string; done?: boolean; failed?: boolean }

export function CommentThreadTab({ slug, projectId, issueId, canComment, members, myUserId }: {
  slug: string;
  projectId: string;
  issueId: string;
  /** comment.create（COMMENTER+，BR-01）；无权限只读（➕/↩ 不渲染）。 */
  canComment: boolean;
  members: DrawerMember[];
  myUserId: string | null;
}) {
  const [comments, setComments] = useState<CommentRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [replyCtx, setReplyCtx] = useState<ReplyCtx | null>(null);
  /** 展开的线程（会话记忆；>3 回复默认折叠——§3.1 折叠条行）。 */
  const [expandedThreads, setExpandedThreads] = useState<Set<string>>(new Set());
  /** 灯箱（C.87）：同评论图片 + 索引。 */
  const [lightbox, setLightbox] = useState<{ imgs: CommentImageMeta[]; idx: number } | null>(null);
  /** 表情选择器挂在哪条评论（C.85）。 */
  const [emojiPopFor, setEmojiPopFor] = useState<string | null>(null);
  /** 名单浮层数据（?expand=reactions；hover chip 时懒拉一次）。 */
  const [rxExpanded, setRxExpanded] = useState<Record<string, CommentReactionAgg[]>>({});
  const [whoPop, setWhoPop] = useState<{ commentId: string; emoji: string } | null>(null);
  /** Composer 上传中图片（C.88）。 */
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const replySwitchRef = useRef<HTMLDivElement | null>(null);

  const nameOfUser = useCallback((uid: string) =>
    members.find((m) => m.user.id === uid)?.user.display_name ?? "成员", [members]);

  const mentionCandidates: MentionCandidate[] = useMemo(
    () => members.map((m) => ({ id: m.user.id, name: m.user.display_name, email: "" })),
    [members],
  );
  const mention = useMentionTrigger({ value: draft, setValue: setDraft, allCandidates: mentionCandidates });

  const refresh = useCallback(() => {
    return CommentAPI.list(slug, projectId, issueId)
      .then((r) => { setComments(unwrap<CommentRow[]>(r) ?? []); setLoaded(true); })
      .catch(() => { /* 鉴权跳转由拦截器负责 */ });
  }, [slug, projectId, issueId]);

  // ── Sprint-3 Phase 3-C（COLLAB-004 §3.3）：comment.created 实时化 ──
  // 非本人评论 → 静默拉取补齐正文（「推送负责知道、拉取负责最终一致」）+ 底部
  // 「N 条新回复 ↓」浮条（aria-live=polite，不抢滚动位置——点击才滚到线程底部）。
  const [pendingReplies, setPendingReplies] = useState(0);
  const threadBottomRef = useRef<HTMLDivElement | null>(null);
  /** 事件去重表（comment_id）：多房间订阅（issue+project）会收到逐房间帧（live §4.3.1
   *  单房间单播），同一条评论可能到达两次——按载荷实体 ID 幂等。 */
  const seenLiveCommentIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    const off = liveEventBus.on("comment.created", (env: LiveEnvelope) => {
      const p = env.payload as unknown as CommentCreatedPayload;
      if (p?.issue_id !== issueId) return;
      if (p.actor_id === myUserId) return; // 自己的乐观更新已就位（BR-08 语义）
      if (p.comment_id) {
        if (seenLiveCommentIds.current.has(p.comment_id)) return;
        seenLiveCommentIds.current.add(p.comment_id);
        if (seenLiveCommentIds.current.size > 500) seenLiveCommentIds.current = new Set();
      }
      setPendingReplies((n) => n + 1);
      void refresh();
    });
    return off;
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, myUserId, refresh]);

  useEffect(() => {
    // 换 issue 时复位本地态（setTimeout(0) 避开 set-state-in-effect 级联渲染——仓库既有范式）
    const handle = setTimeout(() => {
      setComments([]); setLoaded(false); setReplyCtx(null); setDraft(""); setExpandedThreads(new Set());
      setRxExpanded({}); setPendingImages([]);
    }, 0);
    return () => clearTimeout(handle);
  }, [refresh]);

  useEffect(() => {
    void refresh();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  // 表情选择器 / 回复目标下拉：点外即关（教训 #4 mousedown + closest）
  useEffect(() => {
    if (!emojiPopFor && !replyCtx) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="cmt-emoji-pop"]') || t?.closest('[data-sb-scope="cmt-rx-plus"]')) return;
      if (t?.closest('[data-sb-scope="cmt-reply-switch"]') || t?.closest('[data-sb-scope="cmt-reply-ctx"]')) return;
      setEmojiPopFor(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [emojiPopFor, replyCtx]);

  // 灯箱键盘（C.87：←→ 循环翻页 + Esc；Esc 优先级最高）
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setLightbox(null); }
      if (e.key === "ArrowLeft") setLightbox((lb) => (lb ? { ...lb, idx: (lb.idx - 1 + lb.imgs.length) % lb.imgs.length } : lb));
      if (e.key === "ArrowRight") setLightbox((lb) => (lb ? { ...lb, idx: (lb.idx + 1) % lb.imgs.length } : lb));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [lightbox]);

  // 名单浮层：hover chip 时懒拉 ?expand=reactions（每 issue 一次）
  const ensureRxExpanded = useCallback(async (commentId: string) => {
    if (rxExpanded[commentId]) return;
    try {
      const r = await CommentAPI.list(slug, projectId, issueId, { expand: "reactions" });
      const rows = unwrap<CommentRow[]>(r) ?? [];
      const map: Record<string, CommentReactionAgg[]> = {};
      for (const top of rows) {
        map[top.id] = top.reactions ?? [];
        for (const rep of top.replies ?? []) map[rep.id] = rep.reactions ?? [];
      }
      setRxExpanded((cur) => ({ ...cur, ...map }));
    } catch { /* 名单降级：不弹浮层 */ }
  }, [slug, projectId, issueId, rxExpanded]);

  /** 定位评论（顶层或 replies 内）并应用变换（list 注入：函数式 setState 读最新态）。 */
  function mapComment(list: CommentRow[], cid: string, fn: (c: CommentRow) => CommentRow): CommentRow[] {
    return list.map((c) => {
      if (c.id === cid) return fn(c);
      if (c.replies?.some((r) => r.id === cid)) {
        return { ...c, replies: (c.replies ?? []).map((r) => (r.id === cid ? fn(r) : r)) };
      }
      return c;
    });
  }

  /** 表情 toggle（C.85）：乐观 ±1；失败静默回滚 + 重试一次。 */
  async function toggleReaction(cid: string, emoji: string, retried = false) {
    const cur = findComment(cid)?.reactions ?? [];
    const mine = cur.find((r) => r.emoji === emoji)?.reacted_by_me ?? false;
    // 乐观更新
    setComments((cs) => mapComment(cs, cid, (c) => ({ ...c, reactions: applyOptimistic(c.reactions ?? [], emoji, !mine) })));
    try {
      const r = mine
        ? await CommentAPI.reactOff(slug, projectId, issueId, cid, emoji)
        : await CommentAPI.reactOn(slug, projectId, issueId, cid, emoji);
      const agg = unwrap<{ emoji: string; count: number; reacted_by_me: boolean }>(r);
      // 以服务端聚合为准（changed/count）
      setComments((cs) => mapComment(cs, cid, (c) => ({
        ...c,
        reactions: mergeServerAgg(c.reactions ?? [], agg),
      })));
    } catch {
      setComments((cs) => mapComment(cs, cid, (c) => ({ ...c, reactions: applyOptimistic(c.reactions ?? [], emoji, mine) })));
      if (!retried) setTimeout(() => { void toggleReaction(cid, emoji, true); }, 400);
    }
  }

  /** 换 emoji（§2.2）：已点其他表情时，DELETE 旧 + POST 新（两次幂等调用前端串联）。 */
  async function pickEmoji(cid: string, emoji: string) {
    setEmojiPopFor(null);
    const cur = findComment(cid)?.reactions ?? [];
    const others = cur.filter((r) => r.reacted_by_me && r.emoji !== emoji);
    if (others.length) {
      for (const o of others) await toggleReaction(cid, o.emoji);
      toast("换表情：DELETE 旧 + POST 新（前端串联两次幂等调用）", "info", { ttl: 2600 });
    }
    const target = cur.find((r) => r.emoji === emoji);
    if (target?.reacted_by_me) return; // 白名单内已点过该 emoji（off 灰显防呆）
    await toggleReaction(cid, emoji);
  }

  function findComment(cid: string): CommentRow | null {
    for (const c of comments) {
      if (c.id === cid) return c;
      const hit = (c.replies ?? []).find((r) => r.id === cid);
      if (hit) return hit;
    }
    return null;
  }

  /** 进入回复态（§3.3 交互表）：Composer 切回复态 + 焦点 + @ 预填（可删，BR-04）。 */
  function enterReply(rootId: string, target: { id: string | null; name: string } | null) {
    const root = comments.find((c) => c.id === rootId);
    if (root && (root.replies?.length ?? 0) >= 100) {
      toast("该评论回复已达 100 条上限，请直接发表新评论（409）", "warning");
      return;
    }
    setReplyCtx({ rootId, replyToUserId: target?.id ?? null, replyToName: target?.name ?? "" });
    const pre = target ? `<span data-mention-id="${target.id}">@${target.name}</span>&nbsp;` : "";
    setDraft(pre);
    setTimeout(() => {
      const ta = composerRef.current;
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    }, 30);
  }

  function clearReply() {
    setReplyCtx(null);
    setDraft("");
  }

  /** 发表（C.88 ⌘Enter 乐观插入）：回复划入线程底部 + 计数 +1；失败回滚 + 草稿保留。 */
  async function submit() {
    const text = draft.trim();
    if (!text) return;
    const doneImgs = pendingImages.filter((p) => p.done && p.assetId);
    if (pendingImages.some((p) => !p.done && !p.failed)) {
      toast("图片仍在上传中，稍候再发表", "warning");
      return;
    }
    // 组装 comment_html：正文 + image 节点（src 受控锚定 download/?variant=thumb，§4.2.1）
    const imgTags = doneImgs.map((p) =>
      `<img src="/api/v1/workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/${p.assetId}/download/?variant=thumb" alt="${p.name.replace(/"/g, "&quot;")}">`).join("");
    const html = imgTags ? `<p>${text}</p>${imgTags}` : text;
    const commentJson = doneImgs.length ? {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text }] },
        ...doneImgs.map((p) => ({ type: "image", attrs: { asset_id: p.assetId, alt: p.name } })),
      ],
    } : {};
    const ctx = replyCtx;
    const myName = members.find((m) => m.user.id === myUserId)?.user.display_name ?? "我";
    const optimistic: CommentRow = {
      id: "__opt__",
      parent_id: ctx?.rootId ?? null,
      actor: { id: myUserId, display_name: myName, avatar_url: null },
      comment_html: html,
      reactions: [],
      replies: [],
      reply_count: 0,
      reply_to_actor: ctx && ctx.replyToUserId ? { id: ctx.replyToUserId, display_name: ctx.replyToName } : null,
      is_edited: false,
      is_deleted: false,
      created_at: new Date().toISOString(),
      updated_at: null,
    };
    // 乐观插入（回复 → 目标线程底部；顶层 → 列表尾）；§2.1「回复划入线程底部」——
    // 折叠中的线程自动展开，保证乐观插入对用户可见
    if (ctx) setExpandedThreads((s) => new Set(s).add(ctx.rootId));
    setComments((cs) => ctx
      ? cs.map((c) => (c.id === ctx.rootId
        ? { ...c, replies: [...(c.replies ?? []), optimistic], reply_count: (c.reply_count ?? 0) + 1 }
        : c))
      : [...cs, optimistic]);
    const snapshotDraft = draft;
    const snapshotCtx = replyCtx;
    setDraft(""); setReplyCtx(null); setPendingImages([]);
    try {
      await CommentAPI.create(slug, projectId, issueId, {
        comment_html: html,
        comment_json: commentJson,
        parent_id: ctx?.rootId ?? null,
      });
      toast(ctx ? "回复已发表（⌘Enter · 归并至顶层线程）" : "评论已发表", "ok");
      await refresh();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "发表失败", "error");
      // 失败回滚：乐观行移除 + 草稿/回复态/图片节点保留
      setComments((cs) => cs
        .map((c) => (c.id === snapshotCtx?.rootId
          ? { ...c, replies: (c.replies ?? []).filter((r) => r.id !== "__opt__"), reply_count: Math.max(0, (c.reply_count ?? 1) - 1) }
          : c))
        .filter((c) => c.id !== "__opt__"));
      setDraft(snapshotDraft);
      setReplyCtx(snapshotCtx);
    }
  }

  /** 删除（父删子留 BR-06）：🗑 + 确认 → 父转占位（is_deleted）+ replies 保留。 */
  async function delComment(cid: string) {
    if (!confirm("删除该评论？")) return;
    try {
      await CommentAPI.del(slug, projectId, issueId, cid);
      await refresh();
      toast("父评论已软删 · 回复保留（父删子留）", "info");
    } catch (e: unknown) {
      toast((e as ApiError)?.message ?? "删除失败", "error");
    }
  }

  /** 图片上传（C.88）：粘贴/拖入/🖼 → presign(entity_type=comment_image) → 直传 → complete。 */
  async function uploadImage(file: File) {
    const ext = `.${(file.name.split(".").pop() ?? "").toLowerCase()}`;
    if (!IMG_EXTS.includes(ext)) { toast(`评论图片仅支持 ${IMG_EXTS.join(" / ")}`, "error"); return; }
    if (file.size > IMG_MAX_BYTES) { toast("图片超过 5MB（评论图域上限）", "error"); return; }
    if (pendingImages.filter((p) => !p.failed).length >= MAX_IMAGES) { toast("单条评论最多 9 张图片", "error"); return; }
    const key = nextImgKey();
    setPendingImages((ps) => [...ps, { key, name: file.name, progress: 0, file }]);
    try {
      const pre = await AttachmentAPI.presign(slug, projectId, issueId, {
        file_name: file.name, file_size: file.size, content_type: file.type || "application/octet-stream",
        entity_type: "comment_image",
      });
      const p = unwrap<{ asset_id: string; upload_url: string }>(pre);
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", p.upload_url);
        xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            setPendingImages((ps) => ps.map((x) => (x.key === key ? { ...x, progress: Math.round((e.loaded / e.total) * 100) } : x)));
          }
        };
        xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`直传失败：HTTP ${xhr.status}`)));
        xhr.onerror = () => reject(new Error("直传失败：网络错误"));
        xhr.send(file);
      });
      await AttachmentAPI.complete(slug, projectId, issueId, p.asset_id);
      setPendingImages((ps) => ps.map((x) => (x.key === key ? { ...x, assetId: p.asset_id, done: true, progress: 100 } : x)));
    } catch (e: unknown) {
      setPendingImages((ps) => ps.map((x) => (x.key === key ? { ...x, failed: true } : x)));
      toast(e instanceof Error ? e.message : "图片上传失败", "error");
    }
  }

  function onPaste(e: React.ClipboardEvent) {
    const files = [...(e.clipboardData?.files ?? [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) { e.preventDefault(); for (const f of files) void uploadImage(f); }
  }

  const topN = comments.length;
  const repN = comments.reduce((s, c) => s + (c.replies?.length ?? 0), 0);

  return (
    <div data-sb-scope="cmt-thread-tab">
      {/* C.84 评论计数头：💬 评论 N · 回复 M */}
      <div className="flex items-center gap-2 text-[13px] font-semibold text-neutral-600 py-3" data-sb-scope="cmt-head">
        💬 评论 {topN} <span className="text-[12px] font-normal text-neutral-400">· 回复 {repN}</span>
        {pendingReplies > 0 && (
          <button type="button" aria-live="polite" data-sb-scope="cmt-new-replies"
            onClick={() => {
              setPendingReplies(0);
              threadBottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
            }}
            className="ml-auto h-6 px-2.5 rounded-full bg-brand-500 text-white text-[11.5px] font-normal inline-flex items-center gap-1">
            {pendingReplies} 条新回复 ↓
          </button>
        )}
      </div>

      {/* 线程列表（C.84） */}
      {!loaded ? (
        <div className="flex flex-col gap-3 py-2" aria-label="评论加载中">
          {[0, 1].map((i) => (
            <div key={i} className="flex gap-2.5">
              <div className="w-8 h-8 rounded-full bg-neutral-100 animate-pulse shrink-0" />
              <div className="flex-1">
                <div className="h-3 w-24 rounded bg-neutral-100 animate-pulse mb-2" />
                <div className="h-3 w-full rounded bg-neutral-100 animate-pulse mb-1.5" />
                <div className="h-14 rounded-lg bg-neutral-50 animate-pulse mt-2" />
              </div>
            </div>
          ))}
        </div>
      ) : comments.length === 0 ? (
        <div className="text-[13px] text-neutral-500 py-4" data-sb-scope="drawer-comments-empty">还没有评论</div>
      ) : (
        <ul className="flex flex-col" data-sb-scope="drawer-comments-list">
          {comments.map((c) => (
            <li key={c.id} className="flex gap-2.5 py-3 border-b border-neutral-100 last:border-b-0" data-sb-scope="drawer-comment-row">
              {/* 顶层 32px 头像（C.84 线程容器行） */}
              <Avatar32 actor={c.actor} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 text-[12.5px] text-neutral-400">
                  <b className="text-neutral-900 text-[13px]">{c.actor?.display_name ?? "已注销用户"}</b>
                  <span className="tabular-nums">{fmtTime(c.created_at)}</span>
                  {c.is_edited && !c.is_deleted && <span data-sb-scope="drawer-comment-edited">已编辑</span>}
                  {canComment && !c.is_deleted && (
                    <span className="ml-auto flex gap-1">
                      <button aria-label="删除评论（父删子留）" title="删除（父删子留）" data-sb-scope="cmt-del"
                        onClick={() => void delComment(c.id)}
                        className="w-6 h-6 inline-flex items-center justify-center rounded text-neutral-400 hover:text-red-500 hover:bg-red-50">🗑</button>
                    </span>
                  )}
                </div>
                {c.is_deleted ? (
                  /* C.84 父删子留占位：灰底行 + 回复 N 条保留（BR-06） */
                  <div className="text-[13px] text-neutral-400 bg-neutral-100 rounded-lg px-3 py-2 mt-1" data-sb-scope="cmt-deleted">
                    该评论已删除（回复 {(c.replies ?? []).length} 条保留 ↓）
                  </div>
                ) : (
                  <>
                    <div className="text-[13.5px] text-neutral-900 mt-1 leading-relaxed break-words"
                      data-sb-scope="cmt-body"
                      dangerouslySetInnerHTML={{ __html: stripImgTags(c.comment_html) }} />
                    <ImageGrid html={c.comment_html} onOpen={(imgs, idx) => setLightbox({ imgs, idx })} />
                  </>
                )}
                {!c.is_deleted && <ReactionBar row={c} canComment={canComment}
                  onToggle={(e) => void toggleReaction(c.id, e)}
                  onPick={(e) => void pickEmoji(c.id, e)}
                  onOpenPicker={() => setEmojiPopFor(emojiPopFor === c.id ? null : c.id)}
                  pickerOpen={emojiPopFor === c.id}
                  rxExpanded={rxExpanded[c.id]}
                  ensureRxExpanded={() => void ensureRxExpanded(c.id)}
                  whoPop={whoPop}
                  setWhoPop={setWhoPop}
                  nameOfUser={nameOfUser}
                  myUserId={myUserId} />}
                {/* replies 区：32px 缩进 + 2px 引导线 + 浅底圆角行（C.84） */}
                {(c.replies ?? []).length > 0 && (() => {
                  const replies = c.replies ?? [];
                  const folded = replies.length > FOLD_THRESHOLD && !expandedThreads.has(c.id);
                  const shown = folded ? replies.slice(0, 2) : replies;
                  return (
                    <div className="ml-8 pl-3.5 border-l-2 border-neutral-200 flex flex-col gap-2.5 mt-2.5" data-sb-scope="cmt-replies">
                      {shown.map((r) => (
                        <div key={r.id} className="flex gap-2 bg-neutral-50 rounded-[10px] px-3 py-2" data-sb-scope="cmt-reply">
                          <Avatar24 actor={r.actor} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5 text-[12px] text-neutral-400 flex-wrap">
                              <b className="text-neutral-700">{r.actor?.display_name ?? "已注销用户"}</b>
                              <span className="tabular-nums">{fmtTime(r.created_at)}</span>
                              {/* 「回复」徽标（C.84 回复行行） */}
                              <span className="inline-flex items-center gap-0.5 text-[10.5px] text-brand-600 bg-brand-50 rounded px-1.5">↩ 回复</span>
                              {r.reply_to_actor && (
                                <span className="inline-flex items-center gap-0.5 text-[10.5px] text-brand-600 bg-brand-50 rounded px-1.5" data-sb-scope="cmt-reply-to">
                                  @{r.reply_to_actor.display_name}
                                  {/* 归并提示 sr-only（§3.5：视觉弱化但读屏可闻） */}
                                  <span className="sr-only">（已归并至本线程）</span>
                                </span>
                              )}
                            </div>
                            {r.is_deleted ? (
                              <div className="text-[13px] text-neutral-400 italic">该评论已删除</div>
                            ) : (
                              <div className="text-[13px] text-neutral-900 mt-0.5 leading-relaxed break-words"
                                dangerouslySetInnerHTML={{ __html: stripImgTags(r.comment_html) }} />
                            )}
                            <div className="flex items-center gap-2.5 mt-1 text-[11.5px]">
                              <ReactionBar row={r} canComment={canComment} mini
                                onToggle={(e) => void toggleReaction(r.id, e)}
                                onPick={(e) => void pickEmoji(r.id, e)}
                                onOpenPicker={() => setEmojiPopFor(emojiPopFor === r.id ? null : r.id)}
                                pickerOpen={emojiPopFor === r.id}
                                rxExpanded={rxExpanded[r.id]}
                                ensureRxExpanded={() => void ensureRxExpanded(r.id)}
                                whoPop={whoPop}
                                setWhoPop={setWhoPop}
                                nameOfUser={nameOfUser}
                                myUserId={myUserId} />
                              {canComment && (
                                <button className="text-neutral-400 hover:text-brand-600 inline-flex items-center gap-1" data-sb-scope="cmt-reply-btn"
                                  data-root-id={c.id} data-reply-name={r.actor?.display_name ?? ""}
                                  onClick={() => enterReply(c.id, { id: r.actor?.id ?? null, name: r.actor?.display_name ?? "" })}>↩ 回复</button>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                      {/* 折叠条（C.84：>3 默认折叠前 2 + ⊕ 查看另外 N 条；会话记忆） */}
                      {folded ? (
                        <button className="ml-8 pl-3.5 text-[12.5px] text-brand-600 inline-flex items-center gap-1 self-start hover:underline"
                          aria-expanded="false" data-sb-scope="cmt-fold" data-fold="open"
                          onClick={() => setExpandedThreads((s) => new Set(s).add(c.id))}>
                          ⊕ 查看另外 {replies.length - 2} 条回复…
                        </button>
                      ) : replies.length > FOLD_THRESHOLD ? (
                        <button className="ml-8 pl-3.5 text-[12.5px] text-brand-600 inline-flex items-center gap-1 self-start hover:underline"
                          aria-expanded="true" data-sb-scope="cmt-fold" data-fold="close"
                          onClick={() => setExpandedThreads((s) => { const n = new Set(s); n.delete(c.id); return n; })}>
                          ⊖ 收起
                        </button>
                      ) : null}
                    </div>
                  );
                })()}
                {canComment && !c.is_deleted && (
                  <button className="text-[12px] text-neutral-400 hover:text-brand-600 inline-flex items-center gap-1 mt-1.5" data-sb-scope="cmt-reply-btn"
                    data-root-id={c.id} data-reply-name={c.actor?.display_name ?? ""}
                    onClick={() => enterReply(c.id, { id: c.actor?.id ?? null, name: c.actor?.display_name ?? "" })}>↩ 回复</button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {/* COLLAB-004 §3.3：「N 条新回复 ↓」浮条滚动锚点（线程底部哨兵） */}
      <div ref={threadBottomRef} aria-hidden="true" />

      {/* ── Composer（C.88：回复态顶部条 + 工具条 + ⌘Enter + 计数）── */}
      <div className="mt-3 border-t border-neutral-200 pt-3 relative" data-sb-scope="cmt-composer-wrap">
        {mention.isOpen && (
          <MentionPop query={mention.query} candidates={mention.candidates} onPick={mention.onPick}
            className="absolute bottom-full left-0 mb-1.5 z-30" />
        )}
        {mention.filteredEmpty && (
          <div data-sb-scope="mention-pop-empty"
            className="absolute bottom-full left-0 mb-1.5 w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-3 text-[13px] text-neutral-500 text-center z-30">无成员</div>
        )}
        <div className="border border-neutral-300 rounded-[10px] bg-white focus-within:border-brand-500"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
            if (files.length) { e.preventDefault(); for (const f of files) void uploadImage(f); }
          }}>
          {/* 回复态顶部条（C.88：↩ 回复 @xx ▾ + sr-only 归并提示） */}
          {replyCtx && (
            <div className="flex items-center gap-2 bg-brand-50 border-b border-brand-100 rounded-[10px_10px_0_0] px-3 py-1.5 text-[12.5px] text-brand-600 relative" data-sb-scope="cmt-reply-ctx">
              ↩ 回复 @{replyCtx.replyToName || nameOfUser(replyCtx.replyToUserId ?? "")}
              <button className="text-brand-600 hover:underline" data-sb-scope="cmt-reply-switch" aria-label="切换回复目标或清除"
                onClick={() => {
                  const menu = replySwitchRef.current;
                  if (menu) menu.classList.toggle("hidden");
                }}>▾（切换目标/清除）</button>
              <span className="sr-only">将回复到该线程（归并对用户透明）</span>
              <div ref={replySwitchRef} className="hidden absolute top-8 left-2 z-20 w-[220px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1" role="menu" aria-label="回复目标">
                {(() => {
                  const root = comments.find((c) => c.id === replyCtx.rootId);
                  const targets = [root, ...(root?.replies ?? [])].filter(Boolean).slice(0, 5) as CommentRow[];
                  return targets.map((t) => (
                    <button key={t.id} role="menuitem" data-sb-scope="cmt-reply-target"
                      onClick={() => { replySwitchRef.current?.classList.add("hidden"); enterReply(replyCtx.rootId, { id: t.actor?.id ?? null, name: t.actor?.display_name ?? "" }); }}
                      className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 truncate">
                      回复 @{t.actor?.display_name ?? ""}
                    </button>
                  ));
                })()}
                <div className="h-px bg-neutral-100 my-1" />
                <button role="menuitem" data-sb-scope="cmt-reply-clear"
                  onClick={() => { replySwitchRef.current?.classList.add("hidden"); clearReply(); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">清除 · 转为顶层评论</button>
              </div>
            </div>
          )}
          {/* 上传进度节点（C.88：文件名 + 进度条；完成变缩略；失败重试/移除） */}
          {pendingImages.length > 0 && (
            <div className="flex flex-col gap-1.5 px-2.5 pt-2">
              {pendingImages.map((p) => (
                <div key={p.key} className="flex items-center gap-2 border border-neutral-200 rounded-lg px-2.5 py-1.5 text-[12.5px] text-neutral-600 bg-neutral-50" data-sb-scope="cmt-upload-node">
                  <span>🖼</span>
                  <span className="truncate max-w-[200px]">{p.name}</span>
                  {p.failed ? (
                    <span className="text-red-500">上传失败 <button className="underline" data-sb-scope="cmt-upload-retry"
                      onClick={() => { const f = p.file; setPendingImages((ps) => ps.filter((x) => x.key !== p.key)); void uploadImage(f); }}>重试</button>
                      <button className="underline ml-1 text-neutral-400" onClick={() => setPendingImages((ps) => ps.filter((x) => x.key !== p.key))}>移除</button></span>
                  ) : p.done ? (
                    <span className="text-emerald-600">已上传</span>
                  ) : (
                    <>
                      <span className="flex-1 h-[5px] rounded bg-neutral-200 overflow-hidden"><span className="block h-full bg-brand-500 rounded transition-all" style={{ width: `${p.progress}%` }} /></span>
                      <span className="font-mono tabular-nums text-neutral-400">{p.progress}%</span>
                    </>
                  )}
                  {p.done && <button className="text-neutral-400 hover:text-red-500" aria-label={`移除图片 ${p.name}`}
                    onClick={() => setPendingImages((ps) => ps.filter((x) => x.key !== p.key))}>✕</button>}
                </div>
              ))}
            </div>
          )}
          <textarea aria-label={replyCtx ? "回复内容" : "评论内容"} data-sb-scope="drawer-comment-input"
            className="w-full border-0 outline-none resize-y min-h-[64px] px-3 pt-2.5 pb-1 text-[13.5px] leading-relaxed bg-transparent"
            placeholder={replyCtx ? "输入回复…（⌘Enter 发表回复）" : "输入评论，@ 可提及成员…（⌘Enter 发表）"}
            maxLength={5000} value={draft}
            ref={composerRef}
            onPaste={onPaste}
            onChange={(e) => { const ta = e.target as HTMLTextAreaElement; mention.onChangeWithMention(ta.value, ta.selectionStart); }}
            onKeyDown={(e) => {
              if (mention.onKeyDown(e)) return;
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); void submit(); }
            }} />
          {/* 工具条（C.88：@ B I 🖼 😊 🔗；🖼 选择文件） */}
          <div className="flex items-center gap-0.5 px-2 py-1 border-t border-neutral-100 text-neutral-400">
            <span className="min-w-[28px] h-7 inline-flex items-center justify-center rounded">@</span>
            <span className="min-w-[28px] h-7 inline-flex items-center justify-center rounded">B</span>
            <span className="min-w-[28px] h-7 inline-flex items-center justify-center rounded">I</span>
            {canComment && (
              <>
                <button type="button" className="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-neutral-100 hover:text-neutral-700"
                  title="图片（≤5MB · entity_type=comment_image）" aria-label="添加图片" data-sb-scope="cmt-img-btn"
                  onClick={() => imgInputRef.current?.click()}>🖼</button>
                <input ref={imgInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple className="hidden"
                  aria-label="选择图片" onChange={(e) => { for (const f of e.target.files ?? []) void uploadImage(f); e.target.value = ""; }} />
              </>
            )}
            <span className="min-w-[28px] h-7 inline-flex items-center justify-center rounded">😊</span>
            <span className="min-w-[28px] h-7 inline-flex items-center justify-center rounded">🔗</span>
            <div className="ml-auto flex items-center gap-2.5 pr-1.5 text-[11.5px] text-neutral-400">
              {replyCtx && <span>将回复到该线程</span>}
              <span className="font-mono tabular-nums" data-sb-scope="drawer-comment-counter">{draft.length}/5000</span>
              {canComment && (
                <button className="h-7 px-2.5 bg-brand-500 text-white rounded-md text-[12px] hover:bg-brand-600 disabled:opacity-50"
                  data-sb-scope="drawer-comment-submit" disabled={!draft.trim()}
                  onClick={() => void submit()}>{replyCtx ? "发表回复" : "发表评论"}</button>
              )}
            </div>
          </div>
        </div>
        {!canComment && (
          <div className="text-[12px] text-neutral-400 mt-1.5" data-sb-scope="cmt-readonly-hint">当前角色为只读（comment.create 权限不足）</div>
        )}
      </div>

      {/* 灯箱（C.87：全屏 88% 黑遮罩 + ←→ + Esc + 底部信息） */}
      {lightbox && (
        <div className="fixed inset-0 bg-black/88 z-[120] flex items-center justify-center" role="dialog" aria-label="图片灯箱"
          data-sb-scope="cmt-lightbox" onClick={() => setLightbox(null)}>
          <button className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 text-white flex items-center justify-center" aria-label="关闭（Esc）"
            data-sb-scope="cmt-lb-close" onClick={(e) => { e.stopPropagation(); setLightbox(null); }}>✕</button>
          {lightbox.imgs.length > 1 && (
            <button className="absolute left-6 w-11 h-11 rounded-full bg-white/10 text-white flex items-center justify-center hover:bg-white/25" aria-label="上一张"
              data-sb-scope="cmt-lb-prev" onClick={(e) => { e.stopPropagation(); setLightbox((lb) => (lb ? { ...lb, idx: (lb.idx - 1 + lb.imgs.length) % lb.imgs.length } : lb)); }}>‹</button>
          )}
          <div className="max-w-[84vw] max-h-[80vh] rounded-lg" onClick={(e) => e.stopPropagation()}>
            <img src={lightbox.imgs[lightbox.idx]?.src.replace("?variant=thumb", "")} alt={lightbox.imgs[lightbox.idx]?.name}
              className="max-w-[84vw] max-h-[80vh] object-contain rounded-lg" />
          </div>
          {lightbox.imgs.length > 1 && (
            <button className="absolute right-6 w-11 h-11 rounded-full bg-white/10 text-white flex items-center justify-center hover:bg-white/25" aria-label="下一张"
              data-sb-scope="cmt-lb-next" onClick={(e) => { e.stopPropagation(); setLightbox((lb) => (lb ? { ...lb, idx: (lb.idx + 1) % lb.imgs.length } : lb)); }}>›</button>
          )}
          <div className="absolute bottom-5 left-1/2 -translate-x-1/2 text-[12.5px] text-white/85 bg-black/40 rounded-full px-3.5 py-1" data-sb-scope="cmt-lb-meta">
            {lightbox.idx + 1} / {lightbox.imgs.length} · {lightbox.imgs[lightbox.idx]?.name}
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════ 子组件 ═══════════ */

function fmtTime(iso: string): string {
  return iso?.slice(0, 16).replace("T", " ") ?? "";
}

function Avatar32({ actor }: { actor?: CommentRow["actor"] }) {
  return actor?.avatar_url
    ? <img src={actor.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
    : (
      <span className="w-8 h-8 rounded-full bg-neutral-200 text-neutral-700 text-[12px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">
        {(actor?.display_name ?? "?").slice(0, 1)}
      </span>
    );
}

function Avatar24({ actor }: { actor?: CommentRow["actor"] }) {
  return actor?.avatar_url
    ? <img src={actor.avatar_url} alt="" className="w-6 h-6 rounded-full object-cover shrink-0" />
    : (
      <span className="w-6 h-6 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center shrink-0" aria-hidden="true">
        {(actor?.display_name ?? "?").slice(0, 1)}
      </span>
    );
}

/** 反应聚合乐观更新：count ±1 + reacted_by_me 翻转。 */
function applyOptimistic(list: CommentReactionAgg[], emoji: string, mineNext: boolean): CommentReactionAgg[] {
  const hit = list.find((r) => r.emoji === emoji);
  if (!hit) return mineNext ? [...list, { emoji, count: 1, reacted_by_me: true }] : list;
  return list.map((r) => (r.emoji === emoji
    ? { ...r, count: Math.max(0, r.count + (mineNext ? 1 : -1)), reacted_by_me: mineNext }
    : r)).filter((r) => r.count > 0);
}

/** 服务端聚合回填（toggle 响应 {emoji,count,reacted_by_me}）。 */
function mergeServerAgg(list: CommentReactionAgg[], agg: { emoji: string; count: number; reacted_by_me: boolean } | undefined): CommentReactionAgg[] {
  if (!agg) return list;
  const hit = list.find((r) => r.emoji === agg.emoji);
  if (!hit) return agg.count > 0 ? [...list, { emoji: agg.emoji, count: agg.count, reacted_by_me: agg.reacted_by_me }] : list;
  return list.map((r) => (r.emoji === agg.emoji ? { ...r, count: agg.count, reacted_by_me: agg.reacted_by_me } : r)).filter((r) => r.count > 0);
}

/** 反应栏（C.85）：chips + ➕ 选择器 + 名单浮层。 */
function ReactionBar({ row, canComment, mini, onToggle, onPick, onOpenPicker, pickerOpen, rxExpanded, ensureRxExpanded, whoPop, setWhoPop, nameOfUser, myUserId }: {
  row: CommentRow;
  canComment: boolean;
  mini?: boolean;
  onToggle: (emoji: string) => void;
  /** 选择器选emoji（含「换 emoji = DELETE 旧 + POST 新」串联语义，§2.2）。 */
  onPick: (emoji: string) => void;
  onOpenPicker: () => void;
  pickerOpen: boolean;
  rxExpanded?: CommentReactionAgg[] | undefined;
  ensureRxExpanded: () => void;
  whoPop: { commentId: string; emoji: string } | null;
  setWhoPop: (v: { commentId: string; emoji: string } | null) => void;
  nameOfUser: (uid: string) => string;
  myUserId: string | null;
}) {
  const rs = (row.reactions ?? []).filter((r) => r.count > 0);
  if (!canComment && rs.length === 0) return null;
  const mineEmojis = rs.filter((r) => r.reacted_by_me).map((r) => r.emoji);
  return (
    <span className={`inline-flex items-center gap-1.5 flex-wrap ${mini ? "" : "mt-2"}`} data-sb-scope="cmt-rxbar">
      {rs.map((r) => (
        <span key={r.emoji} className="relative inline-flex">
          <button type="button"
            className={`inline-flex items-center gap-1 border rounded-full px-2 py-px text-[12px] transition-colors ${r.reacted_by_me ? "bg-brand-50 border-brand-500 text-brand-600 font-semibold" : "bg-white border-neutral-200 text-neutral-600 hover:border-brand-500"}`}
            aria-pressed={r.reacted_by_me}
            aria-label={`${r.emoji}，${r.count} 人${r.reacted_by_me ? "，含你" : ""}`}
            data-sb-scope="cmt-rx-chip" data-emoji={r.emoji}
            onClick={() => onToggle(r.emoji)}
            onMouseEnter={() => { ensureRxExpanded(); setWhoPop({ commentId: row.id, emoji: r.emoji }); }}
            onMouseLeave={(e) => {
              const to = e.relatedTarget as HTMLElement | null;
              if (to?.closest?.('[data-sb-scope="cmt-who-pop"]')) return;
              setWhoPop(null);
            }}>
            {r.emoji} <span className="tabular-nums text-[11px]">{r.count}</span>
          </button>
          {/* 名单浮层（C.85：前 5 人 + 等 N 人 +（你）） */}
          {whoPop?.commentId === row.id && whoPop.emoji === r.emoji && (rxExpanded?.find?.((x) => x.emoji === r.emoji)?.user_ids?.length ?? 0) > 0 && (() => {
            const entry = rxExpanded?.find((x) => x.emoji === r.emoji);
            const uids = entry?.user_ids ?? [];
            return (
              <span className="absolute bottom-full left-0 mb-1.5 z-[60] bg-white border border-neutral-200 rounded-[10px] shadow-lg px-2.5 py-2 text-[12.5px] w-max min-w-[170px] block text-left"
                role="tooltip" data-sb-scope="cmt-who-pop"
                onMouseLeave={() => setWhoPop(null)}>
                <b className="text-[13px] block mb-0.5">{r.emoji} · {entry?.count ?? r.count} 人</b>
                {uids.slice(0, 5).map((uid) => (
                  <span key={uid} className="flex items-center gap-1.5 py-px text-neutral-600">
                    <span className="w-5 h-5 rounded-full bg-brand-500 text-white text-[10px] font-semibold inline-flex items-center justify-center" aria-hidden="true">{nameOfUser(uid).slice(0, 1)}</span>
                    {nameOfUser(uid)}{uid === myUserId ? <span className="text-neutral-400">（你）</span> : null}
                  </span>
                ))}
                {uids.length > 5 ? <span className="text-neutral-400 text-[11.5px] block mt-1">等 {uids.length} 人…</span> : null}
              </span>
            );
          })()}
        </span>
      ))}
      {canComment && (
        <span className="relative inline-flex">
          <button type="button" className="w-[26px] h-[22px] rounded-full border border-dashed border-neutral-300 text-neutral-400 hover:border-brand-500 hover:text-brand-600 inline-flex items-center justify-center text-[12px]"
            aria-label="添加表情" data-sb-scope="cmt-rx-plus" data-own={row.id}
            onClick={onOpenPicker}>➕</button>
          {pickerOpen && (
            <span className="absolute bottom-7 left-0 z-[65] bg-white border border-neutral-200 rounded-xl shadow-lg p-2.5 w-[264px] block"
              role="menu" aria-label="表情选择器" data-sb-scope="cmt-emoji-pop">
              <span className="grid grid-cols-8 gap-0.5">
                {EMOJI_WHITELIST.map((e) => {
                  const mine = mineEmojis.includes(e);
                  return (
                    <button key={e} type="button" role="menuitem" aria-label={e} data-sb-scope="cmt-emoji-cell" data-emoji={e}
                      disabled={mine}
                      onClick={() => onPick(e)}
                      className={`h-7 rounded-md text-[16px] flex items-center justify-center hover:bg-neutral-100 ${mine ? "opacity-25 cursor-not-allowed pointer-events-none" : ""}`}>{e}</button>
                  );
                })}
              </span>
              <span className="text-[11.5px] text-brand-600 mt-1.5 text-center block">共 24 枚 · 常驻 8 + 展开 16</span>
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** 缩略网格（C.86：96px object-cover；≤2 两列 / ≥3 三列；GIF 角标；点击灯箱）。 */
function ImageGrid({ html, onOpen }: { html: string; onOpen: (imgs: CommentImageMeta[], idx: number) => void }) {
  const imgs = useMemo(() => parseCommentImages(html), [html]);
  if (imgs.length === 0) return null;
  const cols = imgs.length <= 2 ? 2 : 3;
  return (
    <div className={`grid gap-1.5 mt-2 ${cols === 2 ? "grid-cols-[repeat(2,96px)]" : "grid-cols-[repeat(3,96px)]"}`}
      data-sb-scope="cmt-imggrid" data-count={imgs.length}>
      {imgs.map((im, i) => (
        <button key={im.assetId} type="button" className="relative w-24 h-24 rounded-lg overflow-hidden border border-neutral-200 bg-neutral-100 group/img"
          aria-label={`放大 ${im.name}`} data-sb-scope="cmt-imgcell" data-asset-id={im.assetId}
          onClick={() => onOpen(imgs, i)}>
          <img src={im.src} alt={im.name} loading="lazy"
            className="w-full h-full object-cover group-hover/img:opacity-80 transition-opacity" />
          <span className="absolute inset-0 flex items-center justify-center bg-black/25 text-white text-[18px] opacity-0 group-hover/img:opacity-100 transition-opacity">⤢</span>
          {im.gif && <span className="absolute right-1 bottom-1 bg-black/55 text-white text-[10px] rounded px-1" data-sb-scope="cmt-gif-badge">▶ GIF</span>}
        </button>
      ))}
    </div>
  );
}
