import { useCallback, useEffect, useRef, useState } from "react";
import {
  CHUNK_MAX_RETRIES,
  CHUNK_PARALLELISM,
  CHUNK_SIZE_BYTES,
  FileUploadSessionAPI,
  unwrap,
  type ChunkPresignRow,
  type UploadSessionRow,
} from "../../services/api";
import { toast } from "../Toast";
import { humanSize, humanSpeed } from "./filelib-shared";
import { Md5 } from "./md5";

/** FILE-003 §3.1 分片上传器（C.119）——>50MB 强制分片（BR-01）。
 *
 *  引擎（ChunkUploadTask）与 UI 分离：Task 是非 React 状态机
 *  hashing→uploading(paused)→merging→done/error（§4.4），通过 onUpdate 把
 *  快照推回 React；暂停 = 停止取新片（在途片完成后停）；取消 = DELETE 会话
 *  （残片 30 分钟后标记 abandoned）。
 *
 *  断点续传：session_id 持久化 localStorage（重进上传器探测恢复，「继续/放弃」
 *  提示；已传片零重传——uploaded_chunks 跳过）。MD5：整件流式（Md5 增量状态机，
 *  init 随 content_md5 提交）+ 片级（PUT 返回 ETag 与本地 MD5 核对，不符该片
 *  重传 ≤3，BR-03）。 */

export interface ChunkUploadSnapshot {
  key: string;
  name: string;
  size: number;
  state: "hashing" | "uploading" | "paused" | "merging" | "done" | "failed";
  pct: number;
  uploadedBytes: number;
  doneChunks: number;
  totalChunks: number;
  speedBps: number;
  statusText: string;
  resumed: boolean;
}

interface ChunkStorageRow {
  slug: string;
  projectId: string;
  folderId: string;
  sessionId: string;
  name: string;
  size: number;
  savedAt: number;
}

const LS_PREFIX = "rp:chunk-session:";

function lsKey(slug: string, projectId: string, folderId: string, name: string, size: number): string {
  return `${LS_PREFIX}${slug}:${projectId}:${folderId}:${name}:${size}`;
}

function readStored(slug: string, projectId: string, folderId: string | null): ChunkStorageRow[] {
  try {
    const out: ChunkStorageRow[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(LS_PREFIX)) continue;
      const row = JSON.parse(localStorage.getItem(k) || "null") as ChunkStorageRow | null;
      if (!row || row.slug !== slug || row.projectId !== projectId) continue;
      if (folderId !== null && row.folderId !== folderId) continue;
      if (Date.now() - row.savedAt > 24 * 3600_000) { // 会话 24h TTL（BR-05）——过期探测即弃
        localStorage.removeItem(k);
        continue;
      }
      out.push(row);
    }
    return out;
  } catch {
    return [];
  }
}

function storedSessionKey(row: ChunkStorageRow): string {
  return lsKey(row.slug, row.projectId, row.folderId, row.name, row.size);
}

function clearStored(row: ChunkStorageRow): void {
  try { localStorage.removeItem(storedSessionKey(row)); } catch { /* 隐私模式 */ }
}

interface TaskHooks {
  onUpdate: (snap: ChunkUploadSnapshot) => void;
  /** ok=true 完成入列表；ok=false 失败（任务保留在映射中供断点重试）。 */
  onFinished: (ok: boolean, snap: ChunkUploadSnapshot) => void;
}

class ChunkUploadTask {
  private paused = false;
  private cancelled = false;
  private settled = false;
  private sessionId: string | null = null;
  private chunkSize = CHUNK_SIZE_BYTES;
  /** 已登记完成片（PATCH 回执）。 */
  private doneSet = new Set<number>();
  /** 已派发（在途或完成）——防同片重入。 */
  private claimed = new Set<number>();
  /** 在途片已上载字节（进度聚合：完成片 + 在途增量）。 */
  private inflightLoaded = new Map<number, number>();
  private doneBytes = 0;
  private speedEwma = 0;
  private lastSpeedAt = performance.now();
  readonly snap: ChunkUploadSnapshot;

  constructor(private readonly opts: {
    slug: string; projectId: string; folderId: string; file: File; key: string; resumed: boolean;
    hooks: TaskHooks;
  }) {
    this.snap = {
      key: opts.key, name: opts.file.name, size: opts.file.size,
      state: "hashing", pct: 0, uploadedBytes: 0, doneChunks: 0,
      totalChunks: Math.max(1, Math.ceil(opts.file.size / CHUNK_SIZE_BYTES)),
      speedBps: 0, statusText: "计算 MD5 校验值…", resumed: opts.resumed,
    };
    this.emit();
  }

  private emit(patch: Partial<ChunkUploadSnapshot> = {}): void {
    Object.assign(this.snap, patch);
    this.opts.hooks.onUpdate({ ...this.snap });
  }

  private trackSpeed(deltaBytes: number): void {
    const now = performance.now();
    const dt = (now - this.lastSpeedAt) / 1000;
    if (dt > 0.15 && deltaBytes > 0) {
      const inst = deltaBytes / dt;
      this.speedEwma = this.speedEwma ? this.speedEwma * 0.7 + inst * 0.3 : inst;
      this.lastSpeedAt = now;
    }
  }

  private progressOf(partNumber: number, loaded: number): void {
    const prev = this.inflightLoaded.get(partNumber) ?? 0;
    this.inflightLoaded.set(partNumber, loaded);
    this.trackSpeed(loaded - prev);
    const shown = this.doneBytes + [...this.inflightLoaded.values()].reduce((a, b) => a + b, 0);
    this.emit({
      pct: Math.min(99, Math.round((shown / this.opts.file.size) * 100)),
      speedBps: this.speedEwma,
    });
  }

  pause(): void {
    if (this.paused || this.settled) return;
    this.paused = true;
    this.emit({ state: "paused", statusText: "已暂停（在途片完成后停；断点续传已启用）" });
  }

  resume(): void {
    if (!this.paused || this.settled) return;
    this.paused = false;
    this.emit({ state: "uploading", statusText: "继续中…（从已传片断点续传）" });
    void this.pump();
  }

  /** failed 态重试：会话仍在途（uploading）——uploaded_chunks 断点续传，已传片零重传。 */
  retryFromFailure(): void {
    if (!this.settled) return;
    this.settled = false;
    this.emit({ state: "uploading", statusText: "断点续传重试中…" });
    if (this.sessionId == null) {
      void this.start();
      return;
    }
    void this.pump();
  }

  async cancel(): Promise<void> {
    this.cancelled = true;
    this.settled = true;
    this.activeXhrs.forEach((x) => { try { x.abort(); } catch { /* 半死 socket */ } });
    const sid = this.sessionId;
    if (sid) {
      try { await FileUploadSessionAPI.abort(this.opts.slug, this.opts.projectId, sid); } catch { /* 会话可能已过期 */ }
    }
  }

  /** 卸载脱离：只停本地 XHR，**不** Abort 会话——刷新/路由离开是断点续传语义
   *  （BR-05：会话 24h TTL 由 beat 回收；unmount 即 abort 会把 resume 路径杀死）。 */
  detach(): void {
    this.cancelled = true;
    this.settled = true;
    this.activeXhrs.forEach((x) => { try { x.abort(); } catch { /* 半死 socket */ } });
  }

  private readonly activeXhrs = new Set<XMLHttpRequest>();

  /** 启动（可选复用既有会话——resume 路径：已传片零重传）。 */
  async start(session?: UploadSessionRow): Promise<void> {
    const { slug, projectId, folderId, file } = this.opts;
    try {
      // 整件 MD5（init 随 content_md5 提交，§4.2.1；流式增量——不整体载入内存）
      const whole = new Md5();
      for (let off = 0; off < file.size; off += 4 * 1024 * 1024) {
        if (this.cancelled) return;
        whole.update(new Uint8Array(await file.slice(off, off + 4 * 1024 * 1024).arrayBuffer()));
      }
      const contentMd5 = file.size > 0 ? whole.hex() : undefined;

      let sessionRow = session ?? null;
      if (!sessionRow) {
        const r = await FileUploadSessionAPI.init(slug, projectId, {
          file_name: file.name, file_size: file.size,
          content_type: file.type || "application/octet-stream", folder_id: folderId,
          ...(contentMd5 ? { content_md5: contentMd5 } : {}),
        });
        sessionRow = unwrap<UploadSessionRow>(r);
      }
      this.sessionId = sessionRow.session_id;
      this.chunkSize = sessionRow.chunk_size || CHUNK_SIZE_BYTES;
      this.snap.totalChunks = sessionRow.total_chunks;
      (sessionRow.uploaded_chunks ?? []).forEach((n) => {
        this.doneSet.add(n);
        this.claimed.add(n);
      });
      this.doneBytes = [...this.doneSet].reduce(
        (s, n) => s + this.chunkSizeOf(n), 0);
      try {
        localStorage.setItem(lsKey(slug, projectId, folderId, file.name, file.size),
          JSON.stringify({ slug, projectId, folderId, sessionId: this.sessionId, name: file.name, size: file.size, savedAt: Date.now() }));
      } catch { /* 隐私模式 */ }
      this.emit({
        state: this.paused ? "paused" : "uploading",
        statusText: `并行 ${CHUNK_PARALLELISM} 片直传 MinIO`,
        doneChunks: this.doneSet.size, uploadedBytes: this.doneBytes,
        pct: Math.min(99, Math.round((this.doneBytes / file.size) * 100)),
      });
      await this.pump();
    } catch (e: unknown) {
      if (this.cancelled) return;
      this.fail(e instanceof Error ? e.message : "分片上传失败");
    }
  }

  private chunkSizeOf(n: number): number {
    return Math.min(this.chunkSize, this.opts.file.size - (n - 1) * this.chunkSize);
  }

  private fail(msg: string): void {
    this.settled = true;
    this.emit({ state: "failed", statusText: msg });
    this.opts.hooks.onFinished(false, { ...this.snap });
  }

  /** 并行 3 片泵：从最小未派发片号起派发；暂停即停止派发。 */
  private async pump(): Promise<void> {
    if (this.settled || this.cancelled) return;
    const inFlight: Promise<void>[] = [];
    const nextUnclaimed = (): number => {
      for (let n = 1; n <= this.snap.totalChunks; n++) if (!this.claimed.has(n)) return n;
      return 0;
    };
    while (!this.paused && !this.cancelled && !this.settled && inFlight.length < CHUNK_PARALLELISM) {
      const n = nextUnclaimed();
      if (!n) break;
      this.claimed.add(n); // 防同片重入（失败释放）
      inFlight.push(this.uploadChunk(n).catch(() => { this.claimed.delete(n); }));
    }
    await Promise.allSettled(inFlight);
    if (this.cancelled || this.settled) return;
    if (this.paused) return;
    if (nextUnclaimed() === 0) {
      if (this.doneSet.size >= this.snap.totalChunks) await this.merge();
      else await this.pump(); // 有失败片被释放回队列
      return;
    }
    await this.pump();
  }

  private async uploadChunk(partNumber: number): Promise<void> {
    const { slug, projectId, file } = this.opts;
    const sid = this.sessionId!;
    const size = this.chunkSizeOf(partNumber);
    const blob = file.slice((partNumber - 1) * this.chunkSize, (partNumber - 1) * this.chunkSize + size);
    const buf = new Uint8Array(await blob.arrayBuffer());
    const chunkMd5 = new Md5().update(buf).hex();
    let lastErr: unknown = null;
    for (let attempt = 1; attempt <= CHUNK_MAX_RETRIES; attempt++) { // BR-03 片级重传 ≤3
      if (this.cancelled) return;
      try {
        const pre = unwrap<ChunkPresignRow>(await FileUploadSessionAPI.chunkUrl(slug, projectId, sid, partNumber));
        const etag = await new Promise<string>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          this.activeXhrs.add(xhr);
          xhr.open("PUT", pre.upload_url);
          xhr.setRequestHeader("Content-Type", "application/octet-stream");
          xhr.upload.onprogress = (e) => { if (e.lengthComputable) this.progressOf(partNumber, e.loaded); };
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve((xhr.getResponseHeader("ETag") || "").replace(/"/g, "").trim());
            } else reject(new Error(`片 ${partNumber} PUT 失败：HTTP ${xhr.status}`));
          };
          xhr.onerror = () => reject(new Error(`片 ${partNumber} 网络错误`));
          xhr.onabort = () => reject(new Error("已取消"));
          xhr.send(blob);
        });
        if (etag && etag.toLowerCase() !== chunkMd5) {
          throw new Error(`片 ${partNumber} MD5 与 ETag 不符（BR-03 静默损坏防护）`);
        }
        await FileUploadSessionAPI.registerChunk(slug, projectId, sid, partNumber,
          { etag: etag || chunkMd5, md5: chunkMd5 });
        this.inflightLoaded.delete(partNumber);
        this.doneSet.add(partNumber);
        this.doneBytes += size;
        this.emit({
          doneChunks: this.doneSet.size,
          uploadedBytes: this.doneBytes,
          pct: Math.min(99, Math.round((this.doneBytes / file.size) * 100)),
        });
        return;
      } catch (e) {
        lastErr = e;
        if (this.cancelled) return;
      }
    }
    throw lastErr instanceof Error ? lastErr : new Error(`片 ${partNumber} 重传 ${CHUNK_MAX_RETRIES} 次仍失败`);
  }

  private async merge(): Promise<void> {
    const { slug, projectId, hooks } = this.opts;
    this.emit({ state: "merging", pct: 100, statusText: "complete：ListParts 片级核对 + 合并落库…" });
    try {
      await FileUploadSessionAPI.complete(slug, projectId, this.sessionId!);
      try {
        localStorage.removeItem(lsKey(slug, projectId, this.opts.folderId, this.opts.file.name, this.opts.file.size));
      } catch { /* 隐私模式 */ }
      this.settled = true;
      this.emit({ state: "done", pct: 100, uploadedBytes: this.opts.file.size, statusText: "完成，已入列表" });
      hooks.onFinished(true, { ...this.snap });
    } catch (e: unknown) {
      this.fail(e instanceof Error ? e.message : "合并失败");
    }
  }
}

/** React 侧挂载态：任务句柄 + 快照列表。 */
export function useChunkUploads(opts: {
  slug: string; projectId: string;
  onComplete: (name: string) => void;
}) {
  const [snaps, setSnaps] = useState<ChunkUploadSnapshot[]>([]);
  const tasksRef = useRef(new Map<string, ChunkUploadTask>());
  const optsRef = useRef(opts);
  useEffect(() => { optsRef.current = opts; }); // 每渲染后同步（渲染期写 ref 被 react/refs 规则拦）

  const upsert = useCallback((snap: ChunkUploadSnapshot) => {
    setSnaps((cur) => cur.some((s) => s.key === snap.key)
      ? cur.map((s) => (s.key === snap.key ? snap : s))
      : [...cur, snap]);
  }, []);

  const start = useCallback((file: File, folderId: string, session?: UploadSessionRow) => {
    const key = crypto.randomUUID();
    const task = new ChunkUploadTask({
      slug: optsRef.current.slug, projectId: optsRef.current.projectId,
      folderId, file, key, resumed: Boolean(session),
      hooks: {
        onUpdate: upsert,
        onFinished: (ok) => {
          if (!ok) return; // 失败保留任务（failed 态断点重试按钮复用会话）
          tasksRef.current.delete(key);
          optsRef.current.onComplete(file.name);
          setSnaps((cur) => cur.filter((s) => s.key !== key)); // 完成即入列表（浮层收行）
        },
      },
    });
    tasksRef.current.set(key, task);
    void task.start(session);
    return key;
  }, [upsert]);

  const pause = useCallback((key: string) => tasksRef.current.get(key)?.pause(), []);
  const resume = useCallback((key: string) => tasksRef.current.get(key)?.resume(), []);
  const retry = useCallback((key: string) => tasksRef.current.get(key)?.retryFromFailure(), []);
  const cancel = useCallback(async (key: string) => {
    const t = tasksRef.current.get(key);
    tasksRef.current.delete(key);
    setSnaps((cur) => cur.filter((s) => s.key !== key));
    if (t) {
      await t.cancel();
      toast("已取消：残片对象 30 分钟后标记 abandoned，次日物理回收", "info", { ttl: 3200 });
    }
  }, []);

  useEffect(() => () => {
    tasksRef.current.forEach((t) => t.detach()); // 卸载仅脱离（会话留给断点续传/24h TTL）
  }, []);

  return { snaps, start, pause, resume, cancel, retry };
}

/** C.119 分片卡片（上传浮层内的大文件态）：进度 role=progressbar + 片统计 + 暂停/取消。 */
export function ChunkUploadCard({ snap, onPause, onResume, onCancel, onRetry }: {
  snap: ChunkUploadSnapshot;
  onPause: (key: string) => void; onResume: (key: string) => void;
  onCancel: (key: string) => void; onRetry: (key: string) => void;
}) {
  const pct = Math.round(Math.min(100, (snap.uploadedBytes / Math.max(1, snap.size)) * 100));
  const statusLabel = {
    hashing: "计算 MD5 校验值…", uploading: "分片直传中", paused: "已暂停",
    merging: "合并落库中…", done: "完成", failed: "失败",
  }[snap.state];
  return (
    <div className="m-2.5 border border-neutral-200 rounded-xl px-5 py-4" data-sb-scope="files-chunk-card" data-chunk-state={snap.state}>
      <div className="text-[13px] text-neutral-800">⬆ 上传 {snap.name}（{humanSize(snap.size)}）</div>
      <div className="h-2 rounded-full bg-neutral-100 overflow-hidden my-2.5">
        <div className={`h-full rounded-full transition-[width] duration-300 ${snap.state === "failed" ? "bg-red-500" : snap.state === "done" ? "bg-emerald-500" : "bg-brand-500"}`}
          style={{ width: `${Math.max(2, snap.pct, pct)}%` }} role="progressbar" aria-valuenow={Math.max(snap.pct, pct)}
          aria-valuemin={0} aria-valuemax={100} aria-label={`${snap.name} 分片上传进度`} data-sb-scope="files-chunk-bar" />
      </div>
      <div className="flex justify-between text-[11.5px] text-neutral-400">
        <span data-sb-scope="files-chunk-stats">
          {humanSize(snap.uploadedBytes)} / {humanSize(snap.size)} · 已传 {snap.doneChunks}/{snap.totalChunks} 片 · 并行 {CHUNK_PARALLELISM}
        </span>
        <span className="font-mono">{pct}% · {humanSpeed(snap.speedBps)}</span>
      </div>
      <div className="text-[11px] text-neutral-400 mt-1" data-sb-scope="files-chunk-status">
        {statusLabel}{snap.state === "failed" && snap.statusText ? ` · ${snap.statusText}` : ""}
      </div>
      {snap.resumed && (
        <div className="text-[12px] text-emerald-600 mt-1.5" data-sb-scope="files-chunk-resumed">
          ⏸ 断点续传已启用（localStorage session 恢复，已传片零重传）
        </div>
      )}
      <div className="flex gap-2 mt-2.5">
        {snap.state === "paused" && (
          <button type="button" data-sb-scope="files-chunk-resume-btn" onClick={() => onResume(snap.key)}
            className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">继续</button>
        )}
        {(snap.state === "uploading" || snap.state === "hashing") && (
          <button type="button" data-sb-scope="files-chunk-pause-btn" onClick={() => onPause(snap.key)}
            className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">暂停</button>
        )}
        {snap.state === "failed" && (
          <button type="button" data-sb-scope="files-chunk-retry-btn" onClick={() => onRetry(snap.key)}
            className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">重试（断点续传）</button>
        )}
        {snap.state !== "done" && (
          <button type="button" data-sb-scope="files-chunk-cancel-btn" onClick={() => onCancel(snap.key)}
            className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">取消</button>
        )}
      </div>
    </div>
  );
}

/** localStorage 会话探测行（C.119：重进上传器「检测到未完成的上传 [继续] [放弃]」）。 */
export function ChunkResumeBanner({ rows, onContinue, onDiscard }: {
  rows: ChunkStorageRow[];
  onContinue: (row: ChunkStorageRow) => void;
  onDiscard: (row: ChunkStorageRow) => void;
}) {
  if (rows.length === 0) return null;
  const row = rows[0];
  if (!row) return null;
  return (
    <div className="mx-5 my-2 border border-amber-200 bg-amber-50 rounded-lg px-4 py-2.5 text-[12.5px] text-amber-800 flex items-center gap-2.5 flex-wrap"
      role="status" data-sb-scope="files-chunk-resume-banner">
      <span>⏸ 检测到未完成的上传「{row.name}」（{humanSize(row.size)}）——重新选择同一文件可从断点续传</span>
      <button type="button" data-sb-scope="files-chunk-resume-continue"
        onClick={() => onContinue(row)}
        className="h-7 px-2.5 rounded-md bg-brand-500 hover:bg-brand-600 text-[12px] text-white">继续（选择文件）</button>
      <button type="button" data-sb-scope="files-chunk-resume-discard"
        onClick={() => onDiscard(row)}
        className="h-7 px-2.5 rounded-md border border-amber-300 bg-white text-[12px] text-amber-800 hover:bg-amber-100">放弃</button>
    </div>
  );
}

export type { ChunkStorageRow };
export { readStored, clearStored };
