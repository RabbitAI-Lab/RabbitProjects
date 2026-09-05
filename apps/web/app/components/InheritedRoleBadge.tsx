/** AUTH-005 §3.4 继承角色徽标：成员列表中 inherited=true 行名片徽标「继承自工作空间管理员」。
 *
 *  清单行：C.14 继承角色徽标（成员列表中 inherited 行名片徽标「继承自工作空间管理员」）。
 *
 *  成员列表由其它 agent 装配（TEAM-002 / PROJ-002），本组件仅负责渲染徽标本身。
 *  使用方只需把 snapshot.projects[id].inherited 作为 `inherited` prop 传入即可。
 *
 *  BR-08：inherited=true 仅作展示提示，不参与判定数值。
 */
import { observer } from "mobx-react-lite";
import { useStores } from "../stores";
import { PERMISSION_LABELS } from "./PermissionGate";

export interface InheritedRoleBadgeProps {
  /** 该项目成员行的 inherited 字段（PermissionSnapshot.projects[id].inherited） */
  inherited: boolean;
  /** 可选：覆盖默认 tooltip 文案 */
  title?: string;
}

export const InheritedRoleBadge = observer(function InheritedRoleBadge({ inherited, title }: InheritedRoleBadgeProps) {
  // 读 store 仅用于触发 reactive 重渲染（inherited 直接传参即可，无需走 store；
  // 但保留 observer 包装以便上层重构为 store 派生时无需改 import）
  useStores();
  if (!inherited) return null;
  return (
    <span
      data-sb-scope="inherited-role-badge"
      title={title ?? `继承自工作空间管理员 · ${PERMISSION_LABELS["workspace.member.manage"] ?? ""}`}
      className="inline-flex items-center gap-1 h-5 px-1.5 rounded bg-neutral-100 text-neutral-600 text-[11px] border border-neutral-200"
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
        <path d="m4 4 8 3 8-3" />
        <path d="M12 22V8" />
      </svg>
      继承自工作空间管理员
    </span>
  );
});
