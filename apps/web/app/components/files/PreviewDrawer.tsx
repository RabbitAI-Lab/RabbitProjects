import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { liveEventBus } from "@rp/shared-state";
import type { LiveEnvelope } from "@rp/types";
import { API_BASE_URL } from "../../config";
import {
  FileLibraryAPI,
  FilePreviewAPI,
  FileVersionsAPI,
  unwrap,
  type FileVersionRow,
  type LibraryFileRow,
  type PreviewDispatchRow,
} from "../../services/api";
import { toast } from "../Toast";
import { ConfirmDialog, PopoverMenu } from "./FileDialogs";
import { fileIcon, fileTypeName, humanSize, shortDate } from "./filelib-shared";

/** FILE-003 §3.2 预览抽屉（C.120/C.121）——五通道 + 排队/失败态 + 版本面板。
 *
 *  - 调度：GET preview/（就绪 200 / 排队 202）；排队态 ⏳ + 预计时长 + 先下载，
 *    WS file.transcode.completed 自动刷新 + 10s 轮询兜底（live 降级期不瞎等）。
 *  - 通道（§2.3 决策链）：image 缩略 + 原图 / pdf iframe（pdf.js 无依赖，iframe 透传
 *    302 换发）/ office 转码 PDF / text ≤2MB / video 原生控件 + 海报；
 *    archive/other → 元数据卡 + 下载查看。
 *  - 版本面板：列表（当前 ●）+ 文本类双栏 diff（轻量 LCS）+ 回滚确认
 *    （「将创建新版本」——BR-09 零拷贝只增账本）；单版本隐藏版本区（§3.4）。
 *  - WS：file.version.created → 版本面板/预览刷新（mutation 锚点）。 */

const QUEUED_POLL_MS = 10_000;

interface FileVersionPayload { asset_id?: string; version_number?: number; actor_id?: string | null }
interface FileTranscodePayload { asset_id?: string; derivative_kind?: string }

/** 后端换发端点路径（reverse 产物，形如 /api/v1/…）——统一绝对化（直接作 src/fetch）。 */
function absUrl(u: string | null | undefined): string | undefined {
  if (!u) return undefined;
  return u.startsWith("/") ? u : `${API_BASE_URL}/${u}`;
}

/** 文本类版本对比判定（diff 通道仅文本类，§1.3）。 */
function isTextLike(f: LibraryFileRow): boolean {
  if (f.content_type?.startsWith("text/")) return true;
  const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
  return [".txt", ".md", ".markdown", ".log", ".json", ".xml", ".csv"].includes(ext);
}

/** 轻量行级 diff（LCS DP，双方各截 2000 行——2MB 上限内的稳健保护）。 */
function diffLines(a: string, b: string): { left: Array<{ t: string; del: boolean }>; right: Array<{ t: string; ins: boolean }>; added: number; removed: number } {
  const MAX = 2000;
  const la = a.split("\n").slice(0, MAX);
  const lb = b.split("\n").slice(0, MAX);
  const n = la.length, m = lb.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    const rowI = dp[i]!;
    const rowIp1 = dp[i + 1]!;
    for (let j = m - 1; j >= 0; j--) {
      const ai = la[i] ?? "";
      const bj = lb[j] ?? "";
      rowI[j] = ai === bj ? (rowIp1[j + 1] ?? 0) + 1 : Math.max(rowIp1[j] ?? 0, rowI[j + 1] ?? 0);
    }
  }
  const left: Array<{ t: string; del: boolean }> = [];
  const right: Array<{ t: string; ins: boolean }> = [];
  let added = 0, removed = 0;
  let i = 0, j = 0;
  while (i < n && j < m) {
    const ai = la[i] ?? "";
    const bj = lb[j] ?? "";
    if (ai === bj) { left.push({ t: ai, del: false }); right.push({ t: bj, ins: false }); i++; j++; }
    else if ((dp[i + 1]?.[j] ?? 0) >= (dp[i]?.[j + 1] ?? 0)) { left.push({ t: ai, del: true }); removed++; i++; }
    else { right.push({ t: bj, ins: true }); added++; j++; }
  }
  while (i < n) { left.push({ t: la[i] ?? "", del: true }); removed++; i++; }
  while (j < m) { right.push({ t: lb[j] ?? "", ins: true }); added++; j++; }
  return { left, right, added, removed };
}

export function PreviewDrawer({ slug, projectId, file, canWrite, memberName, onClose, onChanged }: {
  slug: string; projectId: string; file: LibraryFileRow;
  canWrite: boolean;
  memberName: (uid: string | null | undefined) => string;
  onClose: () => void;
  /** 版本演进（回滚/新版本）后由宿主重拉列表（行级镜像 size/name 更新）。 */
  onChanged: () => void;
}) {
  const [dispatch, setDispatch] = useState<PreviewDispatchRow | null>(null);
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  const [textBody, setTextBody] = useState<string | null>(null);
  const [versions, setVersions] = useState<FileVersionRow[]>([]);
  const [verMenuOpen, setVerMenuOpen] = useState(false);
  const [rollbackFor, setRollbackFor] = useState<FileVersionRow | null>(null);
  const [compareWith, setCompareWith] = useState<FileVersionRow | null>(null);
  const [diff, setDiff] = useState<{ left: Array<{ t: string; del: boolean }>; right: Array<{ t: string; ins: boolean }>; added: number; removed: number } | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  /** 确认弹层开合镜像（Esc 语义分层：弹层开着时 Esc 只关弹层，不下钻关抽屉）。 */
  const confirmOpenRef = useRef(false);
  useEffect(() => { confirmOpenRef.current = rollbackFor != null; }, [rollbackFor]);

  const reloadDispatch = useCallback(() => {
    setDispatchError(null);
    FilePreviewAPI.dispatch(slug, projectId, file.id)
      .then((r) => { setDispatch((r as unknown as { data: PreviewDispatchRow }).data); })
      .catch((e: unknown) => setDispatchError(e instanceof Error ? e.message : "预览调度失败"));
  }, [slug, projectId, file.id]);

  const reloadVersions = useCallback(() => {
    FileVersionsAPI.list(slug, projectId, file.id)
      .then((r) => { setVersions(unwrap<FileVersionRow[]>(r) ?? []); })
      .catch(() => setVersions([]));
  }, [slug, projectId, file.id]);

  // 初载 + 文件身份变化重载
  useEffect(() => {
    setDispatch(null); setDispatchError(null); setTextBody(null);
    setCompareWith(null); setDiff(null); setRollbackFor(null);
    reloadDispatch(); reloadVersions();
  }, [reloadDispatch, reloadVersions]);

  // 排队态轮询兜底（WS 之外的保险——live 降级期仍能转就绪）
  const queued = dispatch != null && !dispatch.ready && dispatch.state === "transcoding";
  useEffect(() => {
    if (pollTimer.current) { clearInterval(pollTimer.current); pollTimer.current = null; }
    if (queued) {
      pollTimer.current = setInterval(reloadDispatch, QUEUED_POLL_MS);
    }
    return () => { if (pollTimer.current) clearInterval(pollTimer.current); };
  }, [queued, reloadDispatch]);

  // WS：file.transcode.completed → 排队态转就绪；file.version.created → 版本面板/预览刷新
  useEffect(() => {
    const onTranscode = (env: LiveEnvelope) => {
      const p = env.payload as unknown as FileTranscodePayload;
      if (p?.asset_id !== file.id) return;
      reloadDispatch();
    };
    const onVersion = (env: LiveEnvelope) => {
      const p = env.payload as unknown as FileVersionPayload;
      if (p?.asset_id !== file.id) return;
      reloadVersions(); reloadDispatch(); onChanged();
    };
    const offs = [
      liveEventBus.on("file.transcode.completed", onTranscode),
      liveEventBus.on("file.version.created", onVersion),
    ];
    return () => offs.forEach((off) => off());
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [file.id, reloadDispatch, reloadVersions]);

  // 焦点陷阱 + Esc（§3.5：role=dialog + 焦点陷阱；Esc 关闭）
  useEffect(() => {
    const el = dialogRef.current;
    const focusables = el?.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])');
    (focusables?.[0] ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (confirmOpenRef.current) return; // 确认弹层的 Escape 监听自行处理
        e.stopPropagation(); onClose(); return;
      }
      if (e.key !== "Tab" || !el) return;
      const items = [...el.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])')]
        .filter((x) => !x.hasAttribute("disabled"));
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // 文本通道正文（≤2MB；preview_url 是换发端点路径——fetch 走 302 至预签名）
  const textUrl = absUrl(dispatch?.preview_url);
  useEffect(() => {
    if (dispatch?.kind !== "text" || !dispatch.ready || !textUrl) return;
    let alive = true;
    fetch(textUrl, { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); })
      .then((t) => { if (alive) setTextBody(t); })
      .catch(() => { if (alive) setTextBody("（正文加载失败——可下载后查看）"); });
    return () => { alive = false; };
  }, [dispatch?.kind, dispatch?.ready, textUrl]);

  async function download() {
    const get = () => FileLibraryAPI.downloadUrl(slug, projectId, file.id)
      .then((r) => unwrap<{ download_url: string }>(r));
    try {
      let url: string;
      try { url = (await get()).download_url; }
      catch { url = (await get()).download_url; } // C.118：失败自动重申一次预签名
      window.open(url, "_blank", "noopener");
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "下载失败，请重试", "error");
    }
  }

  async function doRollback() {
    if (!rollbackFor) return;
    const target = rollbackFor;
    setRollbackFor(null);
    try {
      const r = await FileVersionsAPI.rollback(slug, projectId, file.id, target.version_id);
      const row = unwrap<{ version_number: number; source_version_number: number | null }>(r);
      toast(`已回滚：新版本 v${row.version_number} 指向 v${target.version_number} 对象（未删除旧版本）`, "ok", { ttl: 3200 });
      reloadVersions(); reloadDispatch(); onChanged();
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "回滚失败", "error");
    }
  }

  async function openCompare(v: FileVersionRow) {
    const current = versions.find((x) => x.is_current) ?? versions[0];
    if (!current || current.version_id === v.version_id) return;
    setCompareWith(v);
    setDiff(null);
    try {
      const [a, b] = await Promise.all([
        fetch(absUrl(FileVersionsAPI.contentPath(slug, projectId, file.id, v.version_id))!, { credentials: "same-origin" }).then((r) => r.text()),
        fetch(absUrl(FileVersionsAPI.contentPath(slug, projectId, file.id, current.version_id))!, { credentials: "same-origin" }).then((r) => r.text()),
      ]);
      setDiff(diffLines(a, b));
    } catch {
      toast("版本正文拉取失败，无法对比", "error");
      setCompareWith(null);
    }
  }

  // ── 通道渲染（§2.3 决策链）──

  const stage = (): { cls: string; node: React.ReactNode } => {
    if (dispatchError) {
      return { cls: "", node: (
        <div className="flex flex-col items-center gap-2.5 text-neutral-700 text-[14px]" data-sb-scope="preview-failed">
          <div className="text-[34px]" aria-hidden="true">⚠</div>
          <div>预览生成失败</div>
          <div className="flex gap-2.5 mt-2">
            <button type="button" data-sb-scope="preview-retry" onClick={reloadDispatch}
              className="h-7 px-3 rounded-md border border-neutral-300 text-[12.5px] text-neutral-700 hover:bg-neutral-50">重试转码</button>
            <button type="button" data-sb-scope="preview-failed-dl" onClick={() => { void download(); }}
              className="h-7 px-3 rounded-md bg-brand-500 hover:bg-brand-600 text-[12.5px] text-white">下载</button>
          </div>
        </div>
      ) };
    }
    if (!dispatch) {
      return { cls: "", node: <div className="w-[560px] h-40 rounded-lg bg-neutral-100 animate-pulse" aria-label="预览加载中" /> };
    }
    if (!dispatch.ready && dispatch.state === "transcoding") {
      return { cls: "", node: (
        <div className="flex flex-col items-center gap-2.5 text-neutral-700 text-[14px]" data-sb-scope="preview-queued">
          <div className="text-[34px]" aria-hidden="true">⏳</div>
          <div>正在转码预览… 预计约 {dispatch.eta_seconds ?? 30} 秒</div>
          <div className="text-[12px] text-neutral-400">Office 文档经 LibreOffice 异步转 PDF（202 排队 · 完成经 WebSocket 自动刷新）</div>
          <button type="button" data-sb-scope="preview-queued-dl" onClick={() => { void download(); }}
            className="mt-2.5 h-7 px-3 rounded-md border border-neutral-300 text-[12.5px] text-neutral-700 hover:bg-neutral-50">先下载</button>
        </div>
      ) };
    }
    if (dispatch.state === "too_large") {
      return { cls: "", node: (
        <div className="flex flex-col items-center gap-2 text-neutral-700 text-[14px]" data-sb-scope="preview-too-large">
          <div className="text-[34px]" aria-hidden="true">📄</div>
          <div>文件较大，请下载查看（文本预览上限 2MB）</div>
          <button type="button" data-sb-scope="preview-toolarge-dl" onClick={() => { void download(); }}
            className="mt-2.5 h-7 px-3 rounded-md bg-brand-500 hover:bg-brand-600 text-[12.5px] text-white">下载查看</button>
        </div>
      ) };
    }
    if (dispatch.state === "unsupported") {
      return { cls: "", node: (
        <div className="flex flex-col items-center gap-2 text-neutral-700 text-[14px]" data-sb-scope="preview-unsupported">
          <div className="text-[34px]" aria-hidden="true">📄</div>
          <div>Office 文档超过转码上限（100MB），无法在线预览</div>
          <button type="button" data-sb-scope="preview-unsupported-dl" onClick={() => { void download(); }}
            className="mt-2.5 h-7 px-3 rounded-md bg-brand-500 hover:bg-brand-600 text-[12.5px] text-white">下载查看</button>
        </div>
      ) };
    }
    switch (dispatch.kind) {
      case "image":
        return { cls: "", node: (
          <div className="flex flex-col items-center gap-3" data-sb-scope="preview-image">
            <img src={absUrl(dispatch.preview_url)} alt={`${file.name} 预览`}
              className="max-w-full max-h-[420px] rounded-lg shadow-md bg-neutral-100 object-contain"
              data-sb-scope="preview-image-img" />
            {originalUrl && (
              <button type="button" data-sb-scope="preview-original" onClick={() => window.open(originalUrl, "_blank", "noopener")}
                className="text-[12.5px] text-brand-600 hover:underline">查看原图</button>
            )}
          </div>
        ) };
      case "pdf":
        return { cls: "", node: (
          <div className="w-[640px] max-w-full flex flex-col gap-1.5" data-sb-scope="preview-pdf">
            <iframe src={absUrl(dispatch.preview_url)} title={`${file.name} PDF 预览`}
              className="w-full h-[460px] rounded-lg border border-neutral-200 bg-white shadow-md"
              data-sb-scope="preview-pdf-frame" />
            <div className="text-[11px] text-neutral-400 self-center">iframe 透传 302 预签名（5 分钟有效）· 文本层可读</div>
          </div>
        ) };
      case "text":
        return { cls: "", node: (
          <div className="w-[640px] max-w-full" data-sb-scope="preview-text">
            <div className="bg-white border border-neutral-200 rounded-lg px-7 py-6 text-[13.5px] leading-[1.9] text-neutral-900 max-h-[460px] overflow-auto whitespace-pre-wrap break-words font-mono"
              data-sb-scope="preview-text-body">
              {textBody ?? "加载正文…"}
            </div>
            <div className="text-[11px] text-neutral-400 mt-1.5 text-center">文本只读预览 · ≤2MB</div>
          </div>
        ) };
      case "video":
        return { cls: "", node: (
          <div className="w-[640px] max-w-full" data-sb-scope="preview-video">
            {/* controls = 原生控件（§3.5）；poster 海报帧就位则挂（BR-11） */}
            <video controls preload="metadata" src={absUrl(dispatch.preview_url)}
              poster={absUrl(dispatch.poster_url)}
              className="w-full rounded-lg shadow-md bg-neutral-900 max-h-[460px]"
              aria-label={`${file.name} 视频预览`} data-sb-scope="preview-video-el" />
            <div className="text-[11px] text-neutral-400 mt-1.5 text-center">边下边播（Range）· 原生控件</div>
          </div>
        ) };
      default:
        // archive / other：元数据卡 + 下载查看（§2.3 L）
        return { cls: "", node: (
          <div className="w-[360px] border border-neutral-200 rounded-xl px-6 py-6 text-center bg-white" data-sb-scope="preview-meta">
            <div className="text-[44px]" aria-hidden="true">{fileIcon(file.type_category, file.name)}</div>
            <div className="text-[15px] font-semibold text-neutral-800 mt-2">{file.name}</div>
            <div className="text-[12px] text-neutral-400 mt-0.5">
              {fileTypeName(file.type_category, file.name)} · {humanSize(file.size_bytes)} · 不支持在线预览
            </div>
            <button type="button" data-sb-scope="preview-meta-dl" onClick={() => { void download(); }}
              className="mt-3.5 h-8 px-3.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white">下载查看</button>
          </div>
        ) };
    }
  };

  // 版本面板正文换发地址（原图查看/对比 fetch——换发端点 302 至 5 分钟预签名）；
  // 必须先于 stage() 求值——image 通道闭包引用 originalUrl（TDZ）
  const currentVersion = useMemo(() => versions.find((v) => v.is_current) ?? null, [versions]);
  const originalUrl = currentVersion
    ? absUrl(FileVersionsAPI.contentPath(slug, projectId, file.id, currentVersion.version_id))
    : null;

  const ch = stage();

  return (
    <>
      <div className="fixed inset-0 bg-black/25 z-[70]" onMouseDown={onClose} data-sb-scope="preview-mask" aria-hidden="true" />
      <div className="fixed top-0 right-0 bottom-0 w-[960px] max-w-[92vw] bg-white shadow-2xl z-[71] flex flex-col"
        role="dialog" aria-modal="true" aria-label={`文件预览 ${file.name}`} ref={dialogRef} tabIndex={-1} data-sb-scope="preview-drawer">
        {/* 头部：名 + vN · 大小 · 人 · 时间 + [版本▾][下载] ✕（C.120） */}
        <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-neutral-200 shrink-0" data-sb-scope="preview-head">
          <span className="text-[18px]" role="img" aria-label={fileTypeName(file.type_category, file.name)}>{fileIcon(file.type_category, file.name)}</span>
          <b className="text-[14px] text-neutral-900">{file.name}</b>
          <span className="text-[12px] text-neutral-400" data-sb-scope="preview-head-meta">
            v{currentVersion?.version_number ?? 1} · {humanSize(file.size_bytes)} · {memberName(file.uploaded_by)} · {shortDate(file.updated_at)}
          </span>
          <div className="ml-auto flex gap-2 items-center">
            <div className="relative">
              <button type="button" data-sb-scope="preview-vermenu" aria-haspopup="menu" aria-expanded={verMenuOpen}
                onClick={() => setVerMenuOpen(!verMenuOpen)}
                className="h-8 px-2.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50 bg-white">版本 ▾</button>
              {verMenuOpen && (
                <PopoverMenu
                  items={versions.map((v) => ({
                    key: v.version_id,
                    label: `v${v.version_number}${v.is_current ? " · 当前" : ""}${v.source_version_number ? `（回滚自 v${v.source_version_number}）` : ""}`,
                  }))}
                  onPick={() => setVerMenuOpen(false)}
                  close={() => setVerMenuOpen(false)} />
              )}
            </div>
            <button type="button" data-sb-scope="preview-dl" onClick={() => { void download(); }}
              className="h-8 px-3 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50 bg-white">下载</button>
            <button type="button" aria-label="关闭（Esc）" data-sb-scope="preview-close" onClick={onClose}
              className="w-[30px] h-[30px] rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700">✕</button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-auto bg-neutral-50" data-sb-scope="preview-body">
          <div className={`min-h-[340px] flex items-center justify-center p-6 relative ${ch.cls}`}>{ch.node}</div>

          {/* 版本面板（C.121）：单版本隐藏版本区（§3.4） */}
          {versions.length > 1 && (compareWith ? (
            <div className="mx-6 mb-6 border border-neutral-200 rounded-xl bg-white p-4 overflow-hidden" data-sb-scope="preview-diff">
              <div className="flex items-center gap-2.5 mb-3">
                <b className="text-[13.5px]">版本对比</b>
                <span className="text-[12px] text-neutral-400">
                  v{compareWith.version_number} ↔ v{currentVersion?.version_number} · {diff ? `新增 ${diff.added} 行，删除 ${diff.removed} 行` : "计算差异…"}
                </span>
                <button type="button" className="ml-auto text-[13px] text-brand-600 hover:underline" data-sb-scope="preview-diff-exit"
                  onClick={() => { setCompareWith(null); setDiff(null); }}>退出对比</button>
              </div>
              {diff && (
                <div className="grid grid-cols-2 border border-neutral-200 rounded-lg overflow-hidden font-mono text-[12px] leading-[1.8] max-h-[420px]"
                  aria-label={`第 ${compareWith.version_number} 版与第 ${currentVersion?.version_number} 版差异：新增 ${diff.added} 行，删除 ${diff.removed} 行`}
                  data-sb-scope="preview-diff-cols">
                  <div className="overflow-auto">
                    <h5 className="text-[12px] text-neutral-400 font-medium px-3 pt-3 pb-2 sticky top-0 bg-white">
                      v{compareWith.version_number}（{memberName(compareWith.uploaded_by)} {shortDate(compareWith.created_at)}）
                    </h5>
                    {diff.left.map((l, i) => (
                      <div key={i} className={`px-3 whitespace-pre-wrap break-all ${l.del ? "del bg-red-50 text-red-800 line-through" : "text-neutral-800"}`}>{l.t || " "}</div>
                    ))}
                  </div>
                  <div className="overflow-auto border-l border-neutral-200">
                    <h5 className="text-[12px] text-neutral-400 font-medium px-3 pt-3 pb-2 sticky top-0 bg-white">
                      v{currentVersion?.version_number}（当前 · {memberName(currentVersion?.uploaded_by)} {shortDate(currentVersion?.created_at)}）
                    </h5>
                    {diff.right.map((l, i) => (
                      <div key={i} className={`px-3 whitespace-pre-wrap break-all ${l.ins ? "ins bg-emerald-50 text-emerald-800" : "text-neutral-800"}`}>{l.t || " "}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="mx-6 mb-6 border border-neutral-200 rounded-xl bg-white overflow-hidden" data-sb-scope="preview-versions">
              <div className="px-4 py-2.5 text-[13px] font-semibold border-b border-neutral-200 flex items-center gap-2.5">
                版本（{versions.length}）
                <span className="text-[11.5px] font-normal text-neutral-400" data-sb-scope="preview-versions-limit">上限 20 · 超出自动淘汰最旧非当前版本</span>
              </div>
              {versions.map((v) => (
                <div key={v.version_id} className="flex items-center gap-2.5 px-4 py-2.5 border-b border-neutral-100 last:border-b-0 text-[13px]" data-sb-scope="preview-ver-row" data-version={v.version_number}>
                  <span className="font-mono text-[11px] px-1.5 py-px bg-neutral-100 rounded text-neutral-700">v{v.version_number}</span>
                  {v.is_current && <span className="text-[11px] text-emerald-600 bg-emerald-50 rounded px-1.5 py-px" data-sb-scope="preview-ver-current">● 当前</span>}
                  <span className="text-neutral-400 text-[12.5px]">
                    {shortDate(v.created_at)} · {memberName(v.uploaded_by)} · {humanSize(v.size_bytes)}
                  </span>
                  <span className="ml-auto inline-flex gap-2">
                    {isTextLike(file) && !v.is_current && (
                      <button type="button" data-sb-scope="preview-ver-cmp" data-version={v.version_number}
                        onClick={() => { void openCompare(v); }}
                        className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">对比</button>
                    )}
                    {canWrite && !v.is_current && (
                      <button type="button" data-sb-scope="preview-ver-rollback" data-version={v.version_number}
                        onClick={() => setRollbackFor(v)}
                        className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">回滚</button>
                    )}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      {rollbackFor && (
        <ConfirmDialog title={`回滚到 v${rollbackFor.version_number}？`} okText="回滚" layer={90}
          onOk={() => { void doRollback(); }} onClose={() => setRollbackFor(null)}>
          将创建新版本（内容同 v{rollbackFor.version_number}），版本链完整保留，不删除任何版本。
        </ConfirmDialog>
      )}
    </>
  );
}

export { isTextLike };
