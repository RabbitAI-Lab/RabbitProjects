/**
 * PNG 导出（GANTT-002 §4.3.3）：DOM 截图而非 canvas 重绘——甘特是 DOM/SVG 混合
 * 渲染，重绘成本远高于截图；html-to-image + 2x pixelRatio + 过滤器。
 *
 *  - BR-11：导出 = 当前视窗（表头/可见行/连线/今日线/图例）+ 右下角水印三行
 *    （项目名 / 2026-09-01 14:32 / 导出人）；
 *  - BR-12：过滤浮层与拖拽中的条（data-export-hidden）；2x 分辨率；
 *  - §2.6：文件名 `{项目}-{视图名}-{yyyyMMdd-HHmm}.png`；
 *  - §2.5：导出失败（canvas 限制）→ Toast「缩小时间范围重试」。
 */
import { toPng } from "html-to-image";
import { toast } from "../Toast";

export interface GanttExportMeta {
  projectName: string;
  viewName: string;
  userName: string;
}

/** 水印覆盖层（导出期间临时挂载，finally 移除）。 */
function buildWatermark(meta: GanttExportMeta): HTMLDivElement {
  const overlay = document.createElement("div");
  overlay.setAttribute("aria-hidden", "true");
  overlay.style.cssText =
    "position:absolute;right:16px;bottom:12px;z-index:50;background:rgba(255,255,255,.9);border:1px solid #e5e5e5;border-radius:8px;padding:8px 12px;font-size:12px;color:#525252;line-height:1.7;text-align:right;pointer-events:none";
  const now = new Date();
  const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  overlay.innerHTML = `<div>${escapeHtml(meta.projectName)}</div><div>${stamp}</div><div>${escapeHtml(meta.userName)}</div>`;
  return overlay;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

function download(dataUrl: string, filename: string) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** 触发浏览器下载（e2e 断言 download 事件的稳定文件名）。 */
export function exportFilename(meta: GanttExportMeta): string {
  const now = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}`;
  return `${meta.projectName}-${meta.viewName}-${stamp}.png`;
}

export async function exportGanttPng(container: HTMLElement, meta: GanttExportMeta): Promise<void> {
  const watermark = buildWatermark(meta);
  container.appendChild(watermark);
  try {
    const dataUrl = await toPng(container, {
      pixelRatio: 2, // BR-11 2x
      backgroundColor: "#FFFFFF",
      filter: (node) =>
        !(node instanceof HTMLElement && node.dataset.exportHidden === "1"), // BR-12 过滤浮层/拖拽态
    });
    download(dataUrl, exportFilename(meta));
  } catch {
    toast("导出失败，请缩小时间范围重试", "error"); // §2.5 canvas 限制
  } finally {
    watermark.remove();
  }
}
