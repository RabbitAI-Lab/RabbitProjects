/** AUTH-005 §3.4 截断提示条：meta.truncated=true 时顶栏一次性黄色条 + 刷新按钮。
 *
 *  清单行：C.14 截断提示条（meta.truncated=true 时顶栏一次性黄色条「部分项目权限未同步」+ 刷新按钮）。
 *
 *  挂在应用 chrome 顶部；点击「刷新」会触发 PermissionStore.refetch() 重拉快照。
 *  一次性语义：成功后由 store.truncated 自行回 false，组件随之消失（无需本地关闭态）。
 */
import { observer } from "mobx-react-lite";
import { useStores } from "../stores";

export const TruncatedStrip = observer(function TruncatedStrip() {
  const { permission: store } = useStores();
  if (!store.truncated) return null;
  return (
    <div
      data-sb-scope="truncated-strip"
      role="status"
      aria-live="polite"
      className="bg-amber-50 border-b border-amber-200 text-amber-900 text-[13px] flex items-center gap-2 px-3.5 py-1.5"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
        <path d="M12 9v4M12 17h.01" />
      </svg>
      <span>部分项目权限未同步</span>
      <button
        type="button"
        onClick={() => { store.refetch().catch(() => { /* 重拉失败仍维持截断态 */ }); }}
        className="ml-auto h-7 px-2.5 bg-white border border-amber-300 rounded text-[12px] text-amber-900 hover:bg-amber-100"
      >
        刷新
      </button>
    </div>
  );
});
