import express from "express";
import { z } from "zod";

/**
 * P0 阶段仅要求：容器可启动、/health 健康检查通过、Nginx WebSocket upgrade 路由可达。
 * 不承载业务功能 —— 协同编辑（Hocuspocus + Yjs，复用 @rp/editor 的 ProseMirror schema
 * 做服务端安全校验）在 P3 COLLAB-004 接入。
 */

/** 最小日志封装（INFRA-001 §4.11：生产代码禁止裸 console；P1 换结构化日志）。 */
const log = (level: "info" | "error", msg: string): void => {
  process[level === "error" ? "stderr" : "stdout"].write(`[live] ${msg}\n`);
};

const EnvSchema = z.object({
  LIVE_PORT: z.coerce.number().int().positive(),
  API_INTERNAL_URL: z.string().url(),
});

// fail-fast：缺失即退出，不静默取默认值（INFRA-001 §4.6 live 关键点）
const parsed = EnvSchema.safeParse(process.env);
if (!parsed.success) {
  const missing = parsed.error.issues.map((i) => i.path.join(".")).join(", ");
  log("error", `环境变量校验失败（缺失: ${missing}）——请参考 apps/live/.env.example`);
  process.exit(1);
}

const { LIVE_PORT, API_INTERNAL_URL } = parsed.data;

const app = express();

app.get("/health", (_req, res) => {
  res.status(200).json({ status: "ok", service: "live", api: API_INTERNAL_URL });
});

app.listen(LIVE_PORT, () => {
  log("info", `listening on :${LIVE_PORT} (health: /health)`);
});
