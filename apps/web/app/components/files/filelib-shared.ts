/** FILE-002 §3.1/§3.5 文件库域共享工具：类型图标（大小与类型图标冗余文本）、
 *  大小人性化（§4.2.1 示例「9.8GB / 10GB，8MB」口径）、目录树/面包屑派生。
 *  图标映射 = 原型 TYPE_ICO/TYPE_NAME 的 type_category + 扩展名双层版
 *  （后端 type_category 五类，document 细分 md/pdf/sheet 沿用原型图标）。 */

/** type_category + ext → 图标（emoji，与冻结原型同源）。 */
export function fileIcon(category: string, name: string): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (category === "image") return "🖼";
  if (category === "video") return "🎬";
  if (category === "archive") return "📦";
  if (category === "document") {
    if (ext === ".md" || ext === ".markdown") return "📝";
    if (ext === ".pdf") return "📕";
    if (ext === ".xls" || ext === ".xlsx" || ext === ".csv") return "📊";
    return "📄";
  }
  return "📎";
}

/** type_category + ext → 冗余文本（§3.5 无障碍：图标冗余文本）。 */
export function fileTypeName(category: string, name: string): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  if (category === "image") return "图片";
  if (category === "video") return "视频";
  if (category === "archive") return "压缩包";
  if (category === "document") {
    if (ext === ".md" || ext === ".markdown") return "Markdown";
    if (ext === ".pdf") return "PDF";
    if (ext === ".xls" || ext === ".xlsx" || ext === ".csv") return "表格";
    return "文档";
  }
  return "文件";
}

/** 字节人性化（FILE-002 §4.2.1 示例「8.2MB」/ 后端 _human 同算法）。 */
export function humanSize(n: number): string {
  let size = n;
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (size < 1024 || unit === "TB") {
      if (unit === "B") return `${Math.round(size)}B`;
      return `${(Math.round(size * 10) / 10).toString().replace(/\.0$/, "")}${unit}`;
    }
    size /= 1024;
  }
  return `${n}B`;
}

/** 上传速度人性化（KB/s 起）。 */
export function humanSpeed(bytesPerSec: number): string {
  if (!Number.isFinite(bytesPerSec) || bytesPerSec <= 0) return "—";
  return `${humanSize(bytesPerSec)}/s`;
}

/** 工具条「类型」筛选下拉（C.113：三下拉过滤——类型/上传人/时间）。
 *  值 = 后端 type_category 五类（_category_filter 白名单）。 */
export const TYPE_FILTERS: Array<{ key: string; label: string }> = [
  { key: "", label: "全部类型" },
  { key: "image", label: "图片" },
  { key: "document", label: "文档" },
  { key: "video", label: "视频" },
  { key: "archive", label: "压缩包" },
  { key: "other", label: "其他" },
];

export const VIS_LABEL: Record<string, string> = {
  all: "全员可见",
  admins: "仅管理员",
  members: "指定成员",
};

/** 剩余天数（回收站 30 天保留，BR-06；按 deleted_at 倒推）。 */
export function trashDaysLeft(deletedAt: string | null | undefined): number {
  if (!deletedAt) return 0;
  const ms = Date.now() - new Date(deletedAt).getTime();
  return Math.max(0, 30 - Math.floor(ms / 86_400_000));
}

/** 「最近一周」时间筛选判定（C.113 工具条；客户端过滤——后端无时间参数）。 */
export function withinWeek(iso: string): boolean {
  return new Date(iso).getTime() >= Date.now() - 7 * 86_400_000;
}

/** ISO → MM-DD 展示（列表「修改时间」列，原型 09-01 口径；跨年补年份）。 */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  const md = `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return sameYear ? md : `${d.getFullYear()}-${md}`;
}
