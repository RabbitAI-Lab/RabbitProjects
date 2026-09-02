import type { Config } from "@react-router/dev/config";

// SPA 模式（tech-stack.md 决策），配合 Nginx try_files fallback
export default {
  ssr: false,
} satisfies Config;
