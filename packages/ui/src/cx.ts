import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 语义类名合并：允许组件使用者覆盖默认样式而不会产生冲突类。 */
export function cx(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
