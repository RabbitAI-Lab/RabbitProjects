/** FILE-002 共享工具单测（文件库域核心路径：图标/人性化/回收站/时间筛选）。 */
import { describe, expect, it } from "vitest";
import {
  TYPE_FILTERS,
  VIS_LABEL,
  fileIcon,
  fileTypeName,
  humanSize,
  humanSpeed,
  shortDate,
  trashDaysLeft,
  withinWeek,
} from "./filelib-shared";

describe("fileIcon / fileTypeName（类型图标与冗余文本，§3.5 无障碍）", () => {
  it("五类 + document 细分", () => {
    expect(fileIcon("image", "a.png")).toBe("🖼");
    expect(fileIcon("video", "a.mp4")).toBe("🎬");
    expect(fileIcon("archive", "a.zip")).toBe("📦");
    expect(fileIcon("document", "a.md")).toBe("📝");
    expect(fileIcon("document", "a.pdf")).toBe("📕");
    expect(fileIcon("document", "a.xlsx")).toBe("📊");
    expect(fileIcon("document", "a.docx")).toBe("📄");
    expect(fileIcon("other", "a.bin")).toBe("📎");
    expect(fileTypeName("image", "a.png")).toBe("图片");
    expect(fileTypeName("document", "a.md")).toBe("Markdown");
    expect(fileTypeName("other", "a")).toBe("文件");
  });
  it("TYPE_FILTERS 与后端五类白名单同源（含空「全部」）", () => {
    expect(TYPE_FILTERS[0]).toEqual({ key: "", label: "全部类型" });
    expect(TYPE_FILTERS.map((t) => t.key).slice(1).sort()).toEqual(
      ["archive", "document", "image", "other", "video"],
    );
  });
  it("VIS_LABEL 三态", () => {
    expect(Object.keys(VIS_LABEL).sort()).toEqual(["admins", "all", "members"]);
  });
});

describe("humanSize（§4.2.1 示例「8.2MB」口径）", () => {
  it("量级与去尾零", () => {
    expect(humanSize(0)).toBe("0B");
    expect(humanSize(512)).toBe("512B");
    expect(humanSize(8 * 1024)).toBe("8KB");
    expect(humanSize(8.2 * 1024 * 1024)).toBe("8.2MB");
    expect(humanSize(1024 ** 3)).toBe("1GB");
    expect(humanSize(1.5 * 1024 ** 4)).toBe("1.5TB");
  });
});

describe("humanSpeed", () => {
  it("正数带 /s；非有限与零用 —", () => {
    expect(humanSpeed(2048)).toBe("2KB/s");
    expect(humanSpeed(0)).toBe("—");
    expect(humanSpeed(Number.NaN)).toBe("—");
  });
});

describe("trashDaysLeft（回收站 30 天，BR-06）", () => {
  it("刚删 29 天；25 天前删 5 天；未来/空 0 兜底", () => {
    const now = Date.now();
    expect(trashDaysLeft(new Date(now).toISOString())).toBeGreaterThanOrEqual(29);
    expect(trashDaysLeft(new Date(now - 25 * 86_400_000).toISOString())).toBe(5);
    expect(trashDaysLeft(null)).toBe(0);
  });
});

describe("withinWeek / shortDate（客户端时间筛选——后端无时间参数）", () => {
  it("一周内 true", () => {
    expect(withinWeek(new Date().toISOString())).toBe(true);
    expect(withinWeek(new Date(Date.now() - 8 * 86_400_000).toISOString())).toBe(false);
  });
  it("shortDate 本年 MM-DD；空 —", () => {
    expect(shortDate(new Date().toISOString())).toMatch(/^\d{2}-\d{2}$/);
    expect(shortDate(null)).toBe("—");
  });
});
