/** 与 INFRA-001 §4.10 VITE_* 注入对齐（构建期内联进 bundle）。 */
type ViteImportMeta = { env?: Record<string, string | undefined> };

export const API_BASE_URL: string =
  (import.meta as unknown as ViteImportMeta).env?.VITE_API_BASE_URL ?? "/api/v1";
export const LIVE_BASE_URL: string =
  (import.meta as unknown as ViteImportMeta).env?.VITE_LIVE_BASE_URL ?? "/live";
