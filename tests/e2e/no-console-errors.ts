/** API 真相源（与 tests/jmeter/sprint-0-flow.py 的 HTTP 状态码表镜像同源——双源同步）。
 *  所有 e2e 与接口断言必须 import 此表，禁止各自硬编码状态码/字段名/错误码。
 *  TypeScript 版本无 import（Playwright runner 1.62 解析 import type 有边界）；Page 类型用 infer。 */

/** 状态码（与 sprint-0-flow.py 同步；变更时必须双源一起改） */
export const HTTP = {
  OK:            200,
  CREATED:       201,
  NO_CONTENT:    204,
  UNAUTHORIZED:  401,  // DRF 未认证
  FORBIDDEN:     403,  // 越权 / 角色不足
  NOT_FOUND:     404,  // 资源不存在 / 越权 404（防 ID 枚举）
  CONFLICT:      409,  // identifier 重复
  TOO_MANY:      429,  // 限流
  SRV_ERR:       500,
} as const;

/** 关键错误码（与 AUTH-001 §4.3.x 对齐） */
export const CODES = {
  emailExists:   "AUTH_EMAIL_EXISTS",
  disabled:      "AUTH_ACCOUNT_DISABLED",
  invalidCreds:  "AUTH_INVALID_CREDENTIALS",
  csrf:          "AUTH_CSRF_FAILED",
  projectExists: "PROJECT_IDENTIFIER_EXISTS",
} as const;

/** 端点响应字段名 */
export const FIELDS = {
  signUp:        { topLevel: "status", workspace: "default_workspace_slug", user: "email", dataNode: "data" },
  me:            { topLevel: "status", user: "user.email", workspaces: "workspaces" },
  project:       { topLevel: "status", id: "id", identifier: "identifier", states: "data" },
  state:         { group: "group", name: "name", isDefault: "is_default" },
  issue:         { key: "issue_key", state: "state_group", assignee: "assignee.name" },
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
  /Failed to load resource: the server responded with a status of 40[13]/,
];

type _Page = { on: Function; off: Function };
export function attachConsoleGuard(page: _Page): () => string[] {
  const errs: string[] = [];
  const onConsole = (m: any) => {
    if (m.type?.() !== "error") return;
    const text = (m.text?.() ?? String(m)).trim();
    if (ALLOWLIST.some((re) => re.test(text))) return;
    errs.push(text);
  };
  const onPageError = (e: any) => errs.push(String(e).trim());
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  return () => { page.off("console", onConsole); page.off("pageerror", onPageError); return errs; };
}
