/** 结构化日志（INFRA-001 §4.11：生产代码禁止裸 console；INFRA-004 JSON 格式）。
 *  BR-14：连接数 / 房间数 / 事件速率 / 断开原因码均走此处结构化输出。 */
export type LogLevel = "info" | "warn" | "error";

export interface Logger {
  info(msg: string, fields?: Record<string, unknown>): void;
  warn(msg: string, fields?: Record<string, unknown>): void;
  error(msg: string, fields?: Record<string, unknown>): void;
}

function write(level: LogLevel, msg: string, fields?: Record<string, unknown>): void {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    service: "live",
    msg,
    ...fields,
  });
  process[level === "error" ? "stderr" : "stdout"].write(`${line}\n`);
}

export const log: Logger = {
  info: (msg, fields) => write("info", msg, fields),
  warn: (msg, fields) => write("warn", msg, fields),
  error: (msg, fields) => write("error", msg, fields),
};
