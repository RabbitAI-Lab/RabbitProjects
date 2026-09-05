/** AUTH-005 §3.1 / §3.4 / §3.6 按钮级权限门控组件族。
 *
 *  清单行对照（ADR-0010 ③ 由清单生成断言，禁止实现反推）：
 *  - C.14 PermissionGate · hide       → PermissionGate hide 模式（hide prop 默认 true）
 *  - C.14 PermissionGate · disable    → mode="disable" + Tooltip 双通道 + 可聚焦
 *  - C.14 PermissionGate · fallback   → mode="fallback" + fallback prop
 *  - C.14 Gate 加载骨架               → snapshot 为空时 <GateSkeleton /> 等宽等高，aria-busy
 *  - C.14 无权 Tooltip                → <GateTooltip /> focus+hover 双通道
 *  - 截断提示条 / 继承角色徽标 / 路由守卫 → 见同目录其它文件
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router";
import { observer } from "mobx-react-lite";
import { useStores } from "../stores";
import { PERMISSION_LABELS, type PermissionKey, type Scope } from "../stores/permission";

export type GateMode = "hide" | "disable" | "fallback";

export interface PermissionGateProps {
  /** 权限点；类型由 PERMISSION_MATRIX 联合类型收窄（BR-06：未登记 key 编译失败） */
  permission: PermissionKey;
  /** 域；默认 "project"（§3.1 全量范式都用 project 域） */
  scope?: Scope;
  /** 资源 id（项目 id / 工作空间 id）；缺省 → 路由上下文兜底（§4.5.1 路由上下文注入） */
  resourceId?: string;
  /** 模式：hide（默认）/ disable / fallback —— §3.1 mode 决策树 */
  mode?: GateMode;
  /** fallback 模式命中时渲染的降级视图 */
  fallback?: ReactNode;
  /** disable 模式 hover/focus 时的原因文案（§3.4 可换文案 prop） */
  reason?: string;
  children: ReactNode;
}

/** 取当前路由的 :workspaceSlug 作为 ctx 兜底（§4.5.1 路由上下文注入）。 */
function useRouteCtx() {
  const params = useParams();
  return { workspaceSlug: params.workspaceSlug };
}

/** §3.6 Gate 加载骨架：等宽等高占位（防布局跳动 CLS=0），aria-busy="true"。
 *  与最终 button 尺寸一致（h-[34px] min-w-[96px]）—— 模板来自原型 gateBtn() + .gateskel-btn。 */
export function GateSkeleton({ width = 96, height = 34, label = "加载权限中" }: { width?: number; height?: number; label?: string }) {
  return (
    <span
      aria-busy="true"
      aria-label={label}
      data-sb-scope="permission-gate-skel"
      className="inline-flex items-center rounded-md bg-neutral-100 animate-pulse"
      style={{ minWidth: width, height }}
    />
  );
}

/** §3.4 / §3.6 无权 Tooltip：focus + hover 双通道（不依赖单一 hover 触发，键盘聚焦即触发）。
 *  极简实现 —— mousedown 阶段判 target.closest 关闭（CLAUDE.md 教训 #4 dropdown 纪律）。 */
export function GateTooltip({ content, children }: { content: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const root = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="permission-gate-tooltip"]')) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", root, true);
    return () => document.removeEventListener("mousedown", root, true);
  }, [open]);
  return (
    <span
      ref={wrapRef}
      data-sb-scope="permission-gate-tooltip"
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span aria-describedby={open ? id : undefined}>{children}</span>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-1 bg-neutral-800 text-white text-[12px] rounded whitespace-nowrap z-50"
        >
          {content}
        </span>
      )}
    </span>
  );
}

/** PermissionGate：hide / disable / fallback 三模式（AUTH-005 §3.1）。
 *  - snapshot 为空 → 渲染骨架（fail-closed 前置，BR-11）
 *  - allowed → 直接渲染 children
 *  - 不允许：按 mode 分支
 *    - hide → 返回 null（从 DOM 移除，不占焦点序，§3.6）
 *    - fallback → 渲染 fallback prop（§3.1 降级视图）
 *    - disable → 包裹层 aria-disabled=true、可聚焦（tabIndex=0），不用原生 disabled（§3.6 焦点 + Tooltip 可达） */
export const PermissionGate = observer(function PermissionGate({
  permission,
  scope = "project",
  resourceId,
  mode = "hide",
  fallback,
  reason = "当前角色无权执行此操作",
  children,
}: PermissionGateProps) {
  const { permission: store } = useStores();
  const ctx = useRouteCtx();

  // §3.4 Gate 加载骨架：权限数据未到 → 等宽等高占位，aria-busy=true
  // §3.6 转无权渲染时 aria-live="polite" 播报一次 —— 在骨架 fallback 容器上加 role=status + aria-live
  if (store.snapshot === null) {
    return (
      <span role="status" aria-live="polite" className="inline-flex">
        <GateSkeleton />
      </span>
    );
  }

  const allowed = store.can(permission, scope, resourceId, ctx);
  if (allowed) return <>{children}</>;
  if (mode === "hide") return null;                                 // §3.6 hide：从 DOM 移除，不占焦点序
  if (mode === "fallback") return <>{fallback ?? null}</>;          // §3.1 降级视图
  // disable：保留可见性 + 可聚焦 + Tooltip（§3.1/§3.6 —— 危险操作一律 disable，不用原生 disabled）
  return (
    <GateTooltip content={reason}>
      <span
        aria-disabled="true"
        tabIndex={0}
        // 视觉降级 + 拦截鼠标激活（业务按钮仍渲染，只是点了不触发）
        className="inline-flex opacity-50 pointer-events-none"
      >
        {children}
      </span>
    </GateTooltip>
  );
});

/** hook 版：给需要布尔值的代码路径（如 useCanDeleteIssue 组合判定） */
export function usePermission(
  permission: PermissionKey,
  scope: Scope = "project",
  resourceId?: string,
): boolean {
  const { permission: store } = useStores();
  const ctx = useRouteCtx();
  return store.can(permission, scope, resourceId, ctx);
}

/** 导出 PERMISSION_LABELS 供 403 页 / 其它消费者复用（AUTH-005 §3.3 中文名渲染） */
export { PERMISSION_LABELS };
