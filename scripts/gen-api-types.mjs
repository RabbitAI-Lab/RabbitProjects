#!/usr/bin/env node
/** OpenAPI schema → @rp/types/src/generated 类型生成（monorepo-structure.md §8.2）
 * P0 骨架阶段：api 侧 schema 尚未产出，打印占位说明并以 0 退出（不阻塞 CI）。 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const SCHEMA = resolve(process.cwd(), "apps/api/openapi.json");
if (!existsSync(SCHEMA)) {
  console.log(
    "[gen:api-types] apps/api/openapi.json 不存在，跳过生成（INFRA-003 交付 schema 后生效）",
  );
  process.exit(0);
}
console.log("[gen:api-types] 检测到 schema，生成逻辑待 INFRA-003 联动接入 openapi-typescript");
process.exit(0);
