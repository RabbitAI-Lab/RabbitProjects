/** GanttStore 实例行为单测（视窗坐标系 / 钳制 / 粒度锚点 / resetZoom / 建议粒度）。
 *
 * ctx 全注入（不发请求——scheduleWindowFetch 的网络面由 e2e 承载）；
 * 构造即得 3 年时间轴域（today−365d ~ +730d，§4.4.1 时间轴域定稿）。 */
import { describe, expect, it, vi } from "vitest";
import { DAY_WIDTH, GanttStore, type GanttStoreCtx } from "./gantt";

function makeStore(over: Partial<GanttStoreCtx> = {}): GanttStore {
  const ctx: GanttStoreCtx = {
    slug: "ws", projectId: "p1", listParams: {},
    canDrag: () => true,
    ...over,
  };
  return new GanttStore(ctx);
}

describe("视窗坐标系（§4.4.1 与原型 O3 同构）", () => {
  it("today 落轴内；totalPx = 3 年域 × dayWidth", () => {
    const s = makeStore();
    expect(diff(s.tlStart, s.today)).toBe(365);
    expect(diff(s.today, s.tlEnd)).toBe(730);
    expect(s.totalPx).toBe((365 + 730 + 1) * DAY_WIDTH.day);
  });
  it("xOf / dateAt 互逆（day 粒度像素整除）", () => {
    const s = makeStore();
    expect(s.dateAt(s.xOf(s.today))).toBe(s.today);
    expect(s.dateAt(0)).toBe(s.tlStart);
  });
  it("clampPan 边界 [0, totalPx - viewportW]", () => {
    const s = makeStore();
    s.setViewportW(1000);
    expect(s.clampPan(-50)).toBe(0);
    expect(s.clampPan(10 ** 9)).toBe(s.totalPx - 1000);
  });
});

describe("平移与今天导航", () => {
  it("panToCenter(today) → 今天在视窗中线", () => {
    const s = makeStore();
    s.setViewportW(360);
    s.panToCenter(s.today);
    expect(s.dateAt(s.pan + 180)).toBe(s.today);
  });
  it("setPan 走钳制 + 触发窗口取数调度", () => {
    const fetch = vi.fn();
    const s = makeStore();
    s["scheduleWindowFetch"] = fetch;
    s.setPan(10 ** 9);
    expect(s.pan).toBe(s.totalPx - s.viewportW);
    expect(fetch).toHaveBeenCalled();
  });
});

describe("粒度切换（§1.4 中心锚点不变）", () => {
  it("day→week 中心日期保持", () => {
    const s = makeStore();
    s.setViewportW(360);
    s.panToCenter(s.today);
    const center = s.viewportCenterDate();
    s.setGranularity("week");
    expect(s.viewportCenterDate()).toBe(center);
    expect(s.dayWidth).toBe(DAY_WIDTH.week);
  });
  it("day 视窗跨 >180 天 → suggestGranularity=week（一次性提示）", () => {
    const s = makeStore();
    s.setViewportW(DAY_WIDTH.day * 200); // 200 天视窗
    s.setGranularity("day");
    expect(s.suggestGranularity).toBe("week");
    s.setGranularity("week");
    expect(s.suggestGranularity).toBeNull();
  });
});

describe("resetZoom（GANTT-002 工具条 ↺）", () => {
  it("回 day 粒度 + 今天居中", () => {
    const s = makeStore();
    s.setGranularity("month");
    expect(s.granularity).toBe("month");
    s.resetZoom();
    expect(s.granularity).toBe("day");
    expect(s.viewportCenterDate()).toBe(s.today);
  });
});

function diff(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
}
