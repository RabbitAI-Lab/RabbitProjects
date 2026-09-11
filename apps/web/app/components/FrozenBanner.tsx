import { useEffect, useState } from "react";
import { subscribeFrozenBanner } from "../services/frozen-banner";

/** 租户冻结横幅（AUTH-012 §3.4——冻结原型 V-FROZEN O8：琥珀静默条置顶
 * 非阻断；法务口径文案不用「违规」等定性词；数据完整保留可逆 BR-03）。 */
export function FrozenBanner() {
  const [shown, setShown] = useState(false);
  useEffect(() => subscribeFrozenBanner(setShown), []);
  if (!shown) return null;
  return (
    <div data-sb-scope="frozen-banner" role="status"
      className="bg-amber-50 border-b border-amber-200 text-amber-800 text-[13px] px-4 h-9 flex items-center gap-2.5 shrink-0">
      <span>⚠ 租户处于安全审查期，功能暂时受限</span>
      <span className="text-[12px] text-amber-600/80">已建立会话可读，写操作暂时不可用</span>
      <span className="ml-auto text-[12px] text-amber-600/80">数据完整保留；审查结束后由平台运营解除</span>
    </div>
  );
}
