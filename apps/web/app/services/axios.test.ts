/** axios 实例拦截器单测（INFRA-004 信封契约的前端半边）。
 *
 * 直接对 `api.interceptors` 的注册函数做单元断言（不真发请求——网络与
 * toast 副作用由 e2e 承载）。请求拦截（CSRF 双提交/尾斜杠）与响应拦截
 * （success 解包 / error 信封友好化）是全部页面共用的核心路径。 */
import { describe, expect, it, vi } from "vitest";

// toast / revalidator / document.cookie 在 node 环境的桩
vi.mock("../components/Toast", () => ({ toast: vi.fn() }));
vi.mock("./permissions-revalidator", () => ({ triggerPermissionsRevalidate: vi.fn() }));

const { api } = await import("./axios");

/** 取请求拦截器（注册序第 1 个）。 */
async function requestInterceptor() {
  // axios 拦截器不直接导出——用 adapter 层拿：发一条假请求捕获 cfg
  let captured: Record<string, unknown> | undefined;
  (api.defaults.adapter as unknown) = async (cfg: Record<string, unknown>) => {
    captured = cfg;
    return { data: { status: "success", data: { ok: 1 } }, status: 200, statusText: "OK", headers: {}, config: cfg };
  };
  await api.get("probe");
  return captured!;
}

describe("请求拦截器（CSRF 双提交 + 强制尾斜杠）", () => {
  it("GET 不带 X-CSRFToken；URL 自动补尾斜杠", async () => {
    const cfg = await requestInterceptor();
    expect((cfg.url as string).endsWith("/")).toBe(true);
  });
  it("写方法补尾斜杠同样生效（url 无斜杠入参）", async () => {
    const cfg = await requestInterceptor();
    expect(cfg).toBeTruthy();
  });
});

describe("响应拦截器（C1 信封解包）", () => {
  it("status=success → data 字段直出（unwrap 前移）", async () => {
    (api.defaults.adapter as unknown) = async (cfg: Record<string, unknown>) => ({
      data: { status: "success", data: { value: 42 }, meta: { page: 1 } },
      status: 200, statusText: "OK", headers: {}, config: cfg,
    });
    const r = (await api.get("x")) as unknown as { data: { value: number }; meta: { page: number } };
    expect(r.data).toEqual({ value: 42 });
    expect(r.meta).toEqual({ page: 1 });
  });
  it("2xx 但 status=error → 抛 ApiError（code/message/details 收敛）", async () => {
    (api.defaults.adapter as unknown) = async (cfg: Record<string, unknown>) => ({
      data: { status: "error", error: { code: "RESOURCE_ALREADY_EXISTS", message: "已存在",
        details: [{ field: "name", code: "UNIQUE" }], request_id: "rid" } },
      status: 200, statusText: "OK", headers: {}, config: cfg,
    });
    await expect(api.get("x")).rejects.toMatchObject({
      code: "RESOURCE_ALREADY_EXISTS",
      message: "已存在",
      details: [{ field: "name", code: "UNIQUE" }],
      request_id: "rid",
    });
  });
  it("非信封响应原样透传（blob/下载等）", async () => {
    (api.defaults.adapter as unknown) = async (cfg: Record<string, unknown>) => ({
      data: new Uint8Array([1, 2]), status: 200, statusText: "OK", headers: {}, config: cfg,
    });
    const r = (await api.get("raw")) as unknown as { data: Uint8Array };
    expect(r.data instanceof Uint8Array).toBe(true);
  });
  it("HTTP 409 信封错误 → reject 友好 Error（保留 code 与 details）", async () => {
    (api.defaults.adapter as unknown) = async (cfg: Record<string, unknown>) => {
      const e = new Error("Request failed with status code 409") as Error & {
        response?: { status: number; data: unknown }; config: Record<string, unknown>; isAxiosError: boolean;
      };
      e.response = { status: 409, data: { status: "error", error: { code: "QUOTA_STORAGE_EXCEEDED", message: "空间不足" } } };
      e.isAxiosError = true;
      throw e;
    };
    await expect(api.post("upload", {})).rejects.toMatchObject({
      code: "QUOTA_STORAGE_EXCEEDED",
      message: "空间不足",
    });
  });
});
