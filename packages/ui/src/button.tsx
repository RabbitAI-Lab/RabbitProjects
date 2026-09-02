import type { ButtonHTMLAttributes } from "react";
import { cx } from "./cx";

type Variant = "primary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const VARIANT_CLASS: Record<Variant, string> = {
  primary: "bg-brand-500 text-white hover:bg-brand-600 disabled:opacity-50",
  ghost:
    "bg-white text-neutral-700 border border-neutral-300 hover:bg-neutral-50 disabled:opacity-50",
  danger: "bg-red-500 text-white hover:bg-red-600 disabled:opacity-50",
};

/** 自研按钮：只接受 props，不依赖状态层、不发网络请求（INFRA-001 §2.2 规则 2.5）。 */
export function Button({ variant = "primary", className, type = "button", ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex h-8.5 items-center justify-center gap-1.5 rounded-md px-3.5 text-sm font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed",
        VARIANT_CLASS[variant],
        className,
      )}
      {...rest}
    />
  );
}
