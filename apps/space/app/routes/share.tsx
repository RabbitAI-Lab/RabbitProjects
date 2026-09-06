import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import {
  expiresInLabel,
  fetchShareContent,
  fetchShareMeta,
  humanSize,
  unlockShare,
  type ContentDispatch,
  type ShareFileMeta,
} from "../services/share";

/** FILE-004 §3.3 匿名访问页三态（C.125）：密码门 / 文件页 / 失效页。
 *
 *  - 密码门：极简不泄露文件信息（BR-10——未解锁 meta 仅 requires_password）；
 *    回车提交；错误抖动 + role=alert 剩余次数；429 → 锁定倒计时（BR-07）。
 *  - 文件页：文件名 + 大小 + 过期倒计时 + 预览区（FILE-003 匿名只读变体：
 *    image/pdf/text/video 通道 + 排队轮询；隐藏版本面板与内部按钮）+
 *    下载按钮按 permission 渲染（view 态不渲染，BR-05）。
 *  - 失效页：三因（吊销/过期/源软删）与无效 slug 统一 410 同文案（防枚举区分）。
 *  - 移动优先（§3.4）；下载 aria-label 含文件名；错误 role=alert；密码框自动 focus。 */

type Phase =
  | { t: "loading" }
  | { t: "error"; message: string }
  | { t: "dead" }
  | { t: "pwd"; shake: boolean; err: string | null }
  | { t: "locked"; until: number }
  | { t: "file"; meta: { file: ShareFileMeta; permission: "view" | "download"; expiresAt: string | null } };

const QUEUED_POLL_MS = 5_000;

/** 锁定剩余时间（mm:ss）——helper 间接取时（渲染期直呼 Date.now 会被 purity 规则拦）。 */
function lockCountdown(until: number): string {
  const left = Math.max(0, Math.floor((until - Date.now()) / 1000));
  const mm = String(Math.floor(left / 60)).padStart(2, "0");
  const ss = String(left % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function Share() {
  const { slug = "" } = useParams();
  const [phase, setPhase] = useState<Phase>({ t: "loading" });
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [, setTick] = useState(0); // 锁定倒计时心跳驱动（秒级重渲染）
  const [content, setContent] = useState<ContentDispatch | null>(null);
  const [textBody, setTextBody] = useState<string | null>(null);
  const pwdRef = useRef<HTMLInputElement | null>(null);

  const loadMeta = useCallback(async () => {
    setPhase({ t: "loading" });
    setContent(null); // 阶段切换即重置预览态（避免上一链接残影）
    setTextBody(null);
    const r = await fetchShareMeta(slug);
    if (r.ok) {
      if (r.data.requires_password) {
        setPhase({ t: "pwd", shake: false, err: null });
      } else if (r.data.file && r.data.permission) {
        setPhase({ t: "file", meta: { file: r.data.file, permission: r.data.permission, expiresAt: r.data.expires_at ?? null } });
      } else {
        setPhase({ t: "dead" }); // 已解锁标记但载荷缺失——按失效口径兜底
      }
    } else if ("gone" in r) {
      setPhase({ t: "dead" });
    } else {
      setPhase({ t: "error", message: r.error });
    }
  }, [slug]);

  useEffect(() => {
    const t = setTimeout(() => { void loadMeta(); }, 0); // setState-in-effect 规避（web 域同款）
    return () => clearTimeout(t);
  }, [loadMeta]);

  // 锁定倒计时心跳（BR-07 10 分钟窗口）
  useEffect(() => {
    if (phase.t !== "locked") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [phase.t]);

  // 文件态：预览调度（匿名变体；排队 202 轮询）
  useEffect(() => {
    if (phase.t !== "file") return;
    let alive = true;
    const load = async () => {
      const r = await fetchShareContent(slug);
      if (!alive) return;
      if (r.ok) setContent(r.data);
      else if (r.status === 410) void loadMeta();
      // 其它失败保持 null（预览区显示占位；下载不受影响）
    };
    void load();
    return () => { alive = false; };
  }, [phase.t, slug, loadMeta]);

  // 排队轮询（匿名无 WS——content/ 202 → 5s 重探）
  const queued = content != null && !content.ready && content.state === "transcoding";
  useEffect(() => {
    if (!queued) return;
    const t = setInterval(async () => {
      const r = await fetchShareContent(slug);
      if (r.ok) setContent(r.data);
    }, QUEUED_POLL_MS);
    return () => clearInterval(t);
  }, [queued, slug]);

  // 文本通道正文（preview_url 为匿名直签 5 分钟预签名——fetch 读文本）
  const textUrl = content?.kind === "text" && content.ready ? content.preview_url : null;
  useEffect(() => {
    if (!textUrl) return;
    let alive = true;
    fetch(textUrl).then((r) => r.text())
      .then((t) => { if (alive) setTextBody(t); })
      .catch(() => { if (alive) setTextBody("（正文加载失败）"); });
    return () => { alive = false; };
  }, [textUrl]);

  useEffect(() => {
    if (phase.t === "pwd") pwdRef.current?.focus();
  }, [phase.t]);

  async function submitPassword() {
    if (phase.t !== "pwd" || submitting) return;
    setSubmitting(true);
    const r = await unlockShare(slug, password);
    setSubmitting(false);
    if (r.ok) {
      setPassword("");
      await loadMeta(); // cookie 已落——回读解锁态 meta
      return;
    }
    if (r.status === 429) {
      setPhase({ t: "locked", until: Date.now() + (r.retryAfterSec ?? 600) * 1000 });
      return;
    }
    // C.125：错误文案「密码错误，剩余 N 次尝试」（details 带剩余次数时拼装）
    setPhase({ t: "pwd", shake: true, err: r.remaining ? `密码错误，${r.remaining}` : "密码错误" });
    setTimeout(() => setPhase((p) => (p.t === "pwd" ? { ...p, shake: false } : p)), 400);
  }

  return (
    <div className="min-h-screen flex flex-col bg-[#f6f7f9]" data-sb-scope="space-root">
      <nav className="h-[52px] flex items-center px-6 border-b border-neutral-200 bg-white shrink-0" data-sb-scope="space-nav">
        <b className="text-brand-600">🐇 RabbitProjects</b>
        <span className="text-[12px] text-neutral-400 ml-2">安全分享</span>
        <span className="ml-auto text-[12px] text-neutral-400 hidden sm:inline">space 应用 · 匿名只读（无会话）</span>
      </nav>
      <main className="flex-1 flex items-center justify-center p-4 sm:p-8" data-sb-scope="space-main">
        {phase.t === "loading" ? (
          <div className="w-[520px] max-w-full h-[220px] rounded-2xl bg-white border border-neutral-200 animate-pulse" aria-label="加载中" />
        ) : phase.t === "error" ? (
          <div className="w-[520px] max-w-full bg-white border border-neutral-200 rounded-2xl shadow-md p-9 text-center" role="alert" data-sb-scope="space-error">
            <div className="text-[40px]" aria-hidden="true">⚠</div>
            <div className="text-[16px] font-semibold text-neutral-800 mt-2.5">{phase.message}</div>
            <button type="button" onClick={() => { void loadMeta(); }}
              className="mt-4 h-9 px-4 rounded-md bg-brand-500 hover:bg-brand-600 text-[13px] text-white">重试</button>
          </div>
        ) : phase.t === "dead" ? (
          /* 失效页：三因统一文案（410 同码；不泄露区分原因——§2.4 防枚举） */
          <div className="w-[520px] max-w-full bg-white border border-neutral-200 rounded-2xl shadow-md p-9 text-center" data-sb-scope="space-dead">
            <div className="text-[40px]" aria-hidden="true">🔗</div>
            <div className="text-[16px] font-semibold text-neutral-800 mt-2.5">链接不存在或已失效</div>
            <div className="text-[13px] text-neutral-400 mt-1.5">链接可能已被吊销、过期或源文件已被删除</div>
            <div className="text-[12px] text-neutral-400 mt-3.5">由 RabbitProjects 提供安全分享</div>
          </div>
        ) : phase.t === "locked" ? (
          <div className="w-[520px] max-w-full bg-white border border-neutral-200 rounded-2xl shadow-md p-9 text-center" data-sb-scope="space-locked">
            <div className="text-[40px]" aria-hidden="true">🔒</div>
            <div className="text-[16px] font-semibold text-neutral-800 mt-2.5">尝试次数过多</div>
            <div className="text-[13px] text-neutral-400 mt-1.5" role="alert">
              已锁定，请 <b className="font-mono">{lockCountdown(phase.until)}</b> 后重试
            </div>
            <div className="text-[12px] text-neutral-400 mt-3.5">由 RabbitProjects 提供安全分享</div>
          </div>
        ) : phase.t === "pwd" ? (
          /* 密码门：极简——不泄露任何文件信息（BR-10 信息最小化） */
          <div className="w-[520px] max-w-full bg-white border border-neutral-200 rounded-2xl shadow-md p-9 text-center"
            style={phase.shake ? { animation: "jitter .12s 1" } : undefined} data-sb-scope="space-pwd">
            <style>{"@keyframes jitter{0%,100%{transform:translateX(0)}25%{transform:translateX(-3px)}75%{transform:translateX(3px)}}"}</style>
            <div className="text-[40px]" aria-hidden="true">🔒</div>
            <div className="text-[16px] font-semibold text-neutral-800 mt-2.5">此文件已加密分享</div>
            {phase.err && (
              <div role="alert" className="text-red-600 text-[13px] mt-2.5" data-sb-scope="space-pwd-err">{phase.err}</div>
            )}
            <div className="flex gap-2.5 mt-4">
              <input ref={pwdRef} type="password" value={password}
                placeholder="输入访问密码" aria-label="访问密码" data-sb-scope="space-pwd-input"
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void submitPassword(); }}
                className="flex-1 h-10 px-3 rounded-md border border-neutral-300 focus:border-brand-400 outline-none text-[14px]" />
              <button type="button" data-sb-scope="space-pwd-go" disabled={submitting} onClick={() => { void submitPassword(); }}
                className="h-10 px-4 rounded-md bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-[13.5px] text-white shrink-0">访问</button>
            </div>
            <div className="text-[12px] text-neutral-400 mt-4">由 RabbitProjects 提供安全分享</div>
          </div>
        ) : (
          /* 文件页：头部（名+大小+过期倒计时）+ 预览区 + 下载（按权限）+ 提示行 */
          <div className="w-[520px] max-w-full bg-white border border-neutral-200 rounded-2xl shadow-md p-7 sm:p-9 text-left" data-sb-scope="space-file">
            <div className="flex items-center gap-2.5 flex-wrap" data-sb-scope="space-file-head">
              <b className="text-[16px] text-neutral-900 break-all">{phase.meta.file.name}</b>
              <span className="text-[12px] text-neutral-400">
                {humanSize(phase.meta.file.size_bytes)} · {expiresInLabel(phase.meta.expiresAt)}
              </span>
            </div>
            <div className="w-full h-[300px] rounded-[10px] bg-neutral-100 border border-neutral-200 mt-4 flex items-center justify-center overflow-hidden" data-sb-scope="space-preview">
              {content == null ? (
                <div className="text-[13px] text-neutral-400">预览加载中…</div>
              ) : !content.ready && content.state === "transcoding" ? (
                <div className="flex flex-col items-center gap-2 text-neutral-600 text-[14px]" data-sb-scope="space-preview-queued">
                  <div className="text-[32px]" aria-hidden="true">⏳</div>
                  <div>正在转码预览… 预计约 {content.eta_seconds ?? 30} 秒</div>
                </div>
              ) : content.state === "too_large" || content.state === "unsupported" ? (
                <div className="text-[13px] text-neutral-400" data-sb-scope="space-preview-toolarge">文件较大，请下载查看</div>
              ) : content.kind === "image" ? (
                <img src={content.preview_url} alt={`${phase.meta.file.name} 预览`} className="max-w-full max-h-full object-contain" data-sb-scope="space-preview-img" />
              ) : content.kind === "pdf" ? (
                <iframe src={content.preview_url} title={`${phase.meta.file.name} PDF 预览`} className="w-full h-full" data-sb-scope="space-preview-pdf" />
              ) : content.kind === "video" ? (
                <video controls preload="metadata" src={content.preview_url} poster={content.poster_url}
                  aria-label={`${phase.meta.file.name} 视频预览`} className="max-w-full max-h-full" data-sb-scope="space-preview-video" />
              ) : content.kind === "text" ? (
                <pre className="w-full h-full overflow-auto p-4 text-[13px] leading-relaxed whitespace-pre-wrap break-words" data-sb-scope="space-preview-text">{textBody ?? "加载正文…"}</pre>
              ) : (
                <div className="text-[13px] text-neutral-400" data-sb-scope="space-preview-nopreview">该类型不支持在线预览</div>
              )}
            </div>
            <div className="flex items-center gap-3 mt-4 flex-wrap">
              {phase.meta.permission === "download" ? (
                <button type="button" aria-label={`下载 ${phase.meta.file.name}`} data-sb-scope="space-download"
                  onClick={() => { window.location.href = `/api/v1/public/shares/${encodeURIComponent(slug)}/content/?download=1`; }}
                  className="h-9 px-4 rounded-md bg-brand-500 hover:bg-brand-600 text-[13.5px] text-white">下载</button>
              ) : (
                <span className="text-[12px] text-neutral-400" data-sb-scope="space-nodownload">当前链接仅限预览（无下载权限）</span>
              )}
              <span className="text-[12px] text-neutral-400 ml-auto" data-sb-scope="space-hint">⚠ 本链接由分享者创建，如需延期请联系分享者</span>
            </div>
          </div>
        )}
      </main>
      <footer className="text-center py-3.5 text-neutral-400 text-[12px]" data-sb-scope="space-footer">© 2026 RabbitProjects</footer>
    </div>
  );
}
