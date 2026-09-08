/** API 真相源（与 tests/jmeter/_contract.py 的 HTTP 状态码表镜像同源——双源同步）。
 *  所有 e2e 与接口断言必须 import 此表，禁止各自硬编码状态码/字段名/错误码。
 *  两侧一致性由 tests/run-ci-checks.sh 的 TC-INF4-015 做跨语言集合比对，不靠人眼。
 *  TypeScript 版本无 import（Playwright runner 1.62 解析 import type 有边界）；Page 类型用 infer。
 *
 *  Sprint-1 INFRA-004 收口后字段路径：
 *    成功信封：{ status: "success", data, meta? }
 *    错误信封：{ status: "error", error: { code, message, details?, request_id? } }
 *  错误码做了 §4.2 映射（业务冲突码统一到 RESOURCE_ALREADY_EXISTS）。
 */

/** 状态码（与 sprint-0-flow.py 同步；变更时必须双源一起改） */
export const HTTP = {
  OK:            200,
  CREATED:       201,
  ACCEPTED:      202,   // 已受理异步处理（Sprint-2 TASK-008 字段删除）
  NO_CONTENT:    204,
  BAD_REQUEST:   400,   // 参数 / 校验失败（_contract.py STATUS 有而本表漏登——双源同步补齐，Phase 3-B 批量 400 失败定位用）
  UNAUTHORIZED:  401,  // DRF 未认证
  FORBIDDEN:     403,  // 越权 / 角色不足
  NOT_FOUND:     404,  // 资源不存在 / 越权 404（防 ID 枚举）
  CONFLICT:      409,  // identifier 重复
  GONE:          410,  // 公开分享读时四查统一失效（Sprint-4 FILE-004，与 _contract.py 同步）
  TOO_MANY:      429,  // 限流
  SRV_ERR:       500,
  SRV_UNAVAILABLE: 503, // 依赖服务不可用（COLLAB-004 live 降级，与 _contract.py 同步）
} as const;

/** 关键错误码（INFRA-004 §4.2 映射后）
 *  - emailExists / projectExists 都收敛到 RESOURCE_ALREADY_EXISTS（带 details[]）
 *  - 旧的 sprint-0 私码（PROJECT_IDENTIFIER_EXISTS / AUTH_EMAIL_EXISTS）已废弃
 */
export const CODES = {
  emailExists:   "RESOURCE_ALREADY_EXISTS",
  disabled:      "AUTH_ACCOUNT_DISABLED",
  invalidCreds:  "AUTH_INVALID_CREDENTIALS",
  csrf:          "AUTH_CSRF_FAILED",
  projectExists: "RESOURCE_ALREADY_EXISTS",
  limitExceeded: "RESOURCE_LIMIT_EXCEEDED", // TASK-004 深度越限（details[].code=DEPTH）
  // Sprint-4（GANTT-002 §2.5）：VIEWER 直连改期 PATCH 403——与 _contract.py
  // CODES.roleInsufficient 对齐（此前 TS 镜像漏登，本次同步补齐）
  roleInsufficient: "PERM_ROLE_INSUFFICIENT",
  // Sprint-2（TASK-004~010；均为既有注册码，顶层码零新增）
  stateInvalid:      "RESOURCE_STATE_INVALID",
  circular:          "RESOURCE_CIRCULAR_DEPENDENCY",
  transitionBlocked: "RESOURCE_TRANSITION_BLOCKED",
  cfInvalid:         "VALIDATION_CUSTOM_FIELD_INVALID",
  queueError:        "SERVER_QUEUE_ERROR",
} as const;

/** 端点响应字段名（INFRA-004 C1 信封） */
export const FIELDS = {
  signUp:        { topLevel: "status", workspace: "default_workspace_slug", user: "email", dataNode: "data" },
  me:            { topLevel: "status", user: "user.email", workspaces: "workspaces" },
  project:       { topLevel: "status", id: "id", identifier: "identifier", states: "data" },
  state:         { group: "group", name: "name", isDefault: "is_default" },
  issue:         { key: "issue_key", state: "state_group", assignee: "assignee.name" },
  // INFRA-004 错误体新增的嵌套字段：spec 可断言 error.code 而非老的 meta.code
  error: {
    topLevel:     "error",
    code:         "error.code",
    message:      "error.message",
    details:      "error.details",
    requestId:    "error.request_id",
    // details[] 子码（INFRA-004 §8.8 字段级子码）
    detailField:  "error.details[].field",
    detailCode:   "error.details[].code",
  },
} as const;

/** 跨 test 唯一邮箱 helper（同 worker 不冲突 demo ts） */
export function freshEmail(prefix = "parity"): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e4)}@rabbit.dev`;
}

/** console guard（mousedown 阶段 + 白名单过滤；不需要 Page 类型注解） */
const ALLOWLIST = [
  /\[vite\]/i,
  /Download the React DevTools/i,
  /react-devtools/i,
  // 浏览器对 4xx 资源加载的网络层回声：鉴权类测试故意触发 401/403，
  // 状态码已由 spec 的 waitForResponse 显式断言，console 重复记录是噪声。
  // JS 异常 / React 错误 / 5xx 仍为硬失败。
  /Failed to load resource: the server responded with a status of 4\d\d/,
  // COLLAB-004 BR-10：live 不可达是「预期的降级态」（WS 连接失败 + /health 探测
  // 失败），实时层只是加速器、轮询兜底功能零损失——降级噪声不算应用缺陷。
  // 仅放行 /live/ 资源（location.url 精确判定，见 onConsole）；应用自身 5xx 仍硬失败。
  /WebSocket connection to '[^']*\/live\/connect[^']*' failed/i,
];

type _Page = { on: Function; off: Function };
export function attachConsoleGuard(page: _Page): () => string[] {
  const errs: string[] = [];
  const onConsole = (m: any) => {
    if (m.type?.() !== "error") return;
    const text = (m.text?.() ?? String(m)).trim();
    if (ALLOWLIST.some((re) => re.test(text))) return;
    // 按来源 URL 放行 /live/ 资源的网络层回声（探测/连接失败是 BR-10 降级路径）；
    // realtime-token 500（SERVER_LIVE_SERVICE_UNAVAILABLE）同为已文档化降级（横幅兜底）。
    const url = m.location?.()?.url ?? "";
    if (url.includes("/live/") || url.includes("realtime-token")) return;
    errs.push(text);
  };
  const onPageError = (e: any) => errs.push(String(e).trim());
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  return () => { page.off("console", onConsole); page.off("pageerror", onPageError); return errs; };
}

/** ── 网络层守卫（Sprint-7 验收教训补口）──────────────────────────────
 *  console 守卫只硬拦 5xx 回声，4xx 网络回声整体放行——后台请求的非预期
 *  4xx/5xx（如曾混进验收视频的 field-schema 500 toast）无人断言即漏网。
 *  attachGuards 在 console 守卫之上收集页面全部 ≥400 响应；测试用
 *  allow() 声明「与接口契约一致的预期失败」（状态码引用本文件 HTTP 表，
 *  与 jmeter/_contract.py 双源同源），期末调用（或 report()）断言为空。
 *  返回值可调用（等价 report()），存量 attachConsoleGuard 接法换名即升级。
 *  realtime-token / /live/ 为已文档化降级路径（COLLAB-004 BR-10），默认豁免。 */
export interface NetAllow { method?: string; url: string; status: number }
export type PageGuards = (() => string[]) & { allow(e: NetAllow): void; report(): string[] };

const NET_DEGRADED = (url: string) => url.includes("realtime-token") || url.includes("/live/");

export function attachGuards(page: _Page): PageGuards {
  const errs: string[] = [];
  const netFails: Array<{ method: string; url: string; status: number }> = [];
  const allowed: NetAllow[] = [];
  const onConsole = (m: any) => {
    if (m.type?.() !== "error") return;
    const text = (m.text?.() ?? String(m)).trim();
    if (ALLOWLIST.some((re) => re.test(text))) return;
    // 网络层回声（4xx/5xx）以响应级记录为准，console 侧去重防双计
    if (/^Failed to load resource/i.test(text)) return;
    const url = m.location?.()?.url ?? "";
    if (url.includes("/live/") || url.includes("realtime-token")) return;
    errs.push(text);
  };
  const onPageError = (e: any) => errs.push(String(e).trim());
  const onResponse = (r: any) => {
    const status = r.status?.() ?? 0;
    if (status < 400) return;
    const url: string = r.url() ?? "";
    if (NET_DEGRADED(url)) return;
    netFails.push({ method: r.request()?.method?.() ?? "?", url, status });
  };
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  page.on("response", onResponse);
  const report = () => {
    const unexpected = netFails.filter((f) => !allowed.some(
      (a) => a.status === f.status
        && (a.method === undefined || a.method === f.method)
        && f.url.includes(a.url)));
    return [...errs, ...unexpected.map((f) => `net ${f.status} ${f.method} ${f.url}`)];
  };
  return Object.assign(() => report(), {
    allow: (e: NetAllow) => allowed.push(e),
    report,
  }) as PageGuards;
}