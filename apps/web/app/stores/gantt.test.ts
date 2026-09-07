/** GanttStore 坐标系纯函数单测（GANTT-001 §4.4.1「dX/xD 与原型 O3 同构」）。
 *
 * store 的 MobX 类不在此实例化（React 绑定归 e2e）——只测导出的日期
 * 算术与常量（跨时区 UTC 基准、DAY_WIDTH 冻结原型值 36/13/9）。 */
import { describe, expect, it } from "vitest";
import { DAY_WIDTH, diffDays, isoToUtc, addDaysIso } from "./gantt";

describe("DAY_WIDTH（冻结原型 O3：36/13/9——ADR-0022 E-1 勘误定稿）", () => {
  it("三粒度值锁定", () => {
    expect(DAY_WIDTH).toEqual({ day: 36, week: 13, month: 9 });
  });
});

describe("isoToUtc / addDaysIso / diffDays（UTC 基准日算术）", () => {
  it("isoToUtc：ISO → UTC 毫秒（日期换算不随时区漂移）", () => {
    expect(isoToUtc("2026-09-07")).toBe(Date.UTC(2026, 8, 7));
    expect(isoToUtc("2026-01-01")).toBe(Date.UTC(2026, 0, 1));
  });
  it("addDaysIso：跨月/跨年进位", () => {
    expect(addDaysIso("2026-09-30", 1)).toBe("2026-10-01");
    expect(addDaysIso("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDaysIso("2026-09-07", -7)).toBe("2026-08-31");
  });
  it("diffDays：天数差（甘特条工期派生）", () => {
    expect(diffDays("2026-09-01", "2026-09-08")).toBe(7);
    expect(diffDays("2026-09-08", "2026-09-01")).toBe(-7);
    expect(diffDays("2026-09-07", "2026-09-07")).toBe(0);
  });
});
