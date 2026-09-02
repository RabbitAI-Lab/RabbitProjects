/** 全局 fixture：所有 spec 零控制台错误（除已知噪音白名单）。
 *  使用：在测试里 import { expectNoConsoleErrors } from "./no-console-errors" 然后 expectNoConsoleErrors(page)。
 *  注册方式：所有 spec 自动调 beforeEach/afterEach 失败即 attach 错误列表。 */
import type { Page } from "@playwright/test";

/** 已知的"非业务错误"白名单（浏览器 / 框架噪声；不在此列的必须 0 出现）
 *  - vite client: HMR / websocket connect
 *  - React DevTools: download banner
 *  - 404 favicon 噪声由 network 监听过滤 */
const ALLOWLIST = [
  /\[vite\]/i,
  /Download the React DevTools/i,
  /react-devtools/i,
];

export function attachConsoleGuard(page: Page): () => string[] {
  const errors: string[] = [];
  const onConsole = (m: any) => {
    if (m.type() !== "error") return;
    const text = (m.text?.() ?? String(m)).trim();
    if (ALLOWLIST.some((re) => re.test(text))) return;
    errors.push(text);
  };
  const onPageError = (e: any) => errors.push(String(e).trim());
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  return () => { page.off("console", onConsole); page.off("pageerror", onPageError); return errors; };
}

/** API 响应与 JMeter 脚本共用的断言真相（tests/jmeter/sprint-0-flow.py 同源）。
 *  字段名必须与后端 IssueSerializer / project envelope 完全一致。
 *  所有"接口/UI 数据一致"断言必须走这里，禁止各自硬编码。 */
export const API_TRUTH = {
  signUp: { status: 201, topLevel: "status", topLevelTrue: true, workspaceField: "default_workspace_slug", userField: "email" },
  me: { status: 200, hasUser: true, hasWorkspaces: true },
  signOut: { status: 204 },
  health: { status: 200, hasDbTrue: true, key: "checks.db" },
  createProject: { status: 201, conflictStatus: 409, conflictCode: "PROJECT_IDENTIFIER_EXISTS" },
  createIssue: { status: 201 },
  projectStates: { status: 200, requiresFields: ["id", "name", "color", "group"] },
  patchIssue: { status: 200 },
  issueDetail: { status: 200, requiresFields: ["issue_key", "name", "state_group", "state_name"] },
  login: { status: 200 },
  /** 错误码来自 docs/api-conventions §8（AUTH-001 §4.x） */
  codes: { emailExists: "AUTH_EMAIL_EXISTS", disabled: "AUTH_ACCOUNT_DISABLED", invalidCreds: "AUTH_INVALID_CREDENTIALS", csrf: "AUTH_CSRF_FAILED" },
} as const;
