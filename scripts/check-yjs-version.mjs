#!/usr/bin/env node
/** Yjs 家族跨包同版本守卫（tech-stack.md §4.1 红线）
 * 校验 apps/web / packages/editor / apps/live 三处 package.json 声明版本
 * 与 pnpm-lock.yaml 实际解析版本完全一致，否则非零退出。 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const PKGS = ["apps/web/package.json", "packages/editor/package.json", "apps/live/package.json"];
const FAMILY = ["yjs", "y-prosemirror", "y-protocols"];
const root = process.cwd();

const declared = Object.fromEntries(
  PKGS.map((p) => {
    const pkg = JSON.parse(readFileSync(resolve(root, p), "utf8"));
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    return [p, Object.fromEntries(FAMILY.map((f) => [f, deps[f] ?? null]))];
  }),
);

const lock = readFileSync(resolve(root, "pnpm-lock.yaml"), "utf8");
const resolved = Object.fromEntries(
  FAMILY.map((f) => {
    const re = new RegExp(`(?:^|[^@\\w/-])${f.replace("/", "\\/")}@(\\d+\\.\\d+\\.\\d+)`, "gm");
    const found = [...lock.matchAll(re)].map((m) => m[1]);
    return [f, found.length ? [...new Set(found)] : null];
  }),
);

let fail = false;
const rows = [];
for (const f of FAMILY) {
  const uniq = resolved[f];
  if (!uniq || uniq.length !== 1) {
    rows.push(`${f}: lock 解析 ${uniq ? uniq.join(", ") : "未找到"} ❌`);
    fail = true;
    continue;
  }
  const vers = PKGS.map((p) => declared[p][f]);
  const same = vers.every((v) => v && v.split(".")[1] === uniq[0].split(".")[1]);
  rows.push(`${f}: 声明 [${vers.join(", ")}] / lock ${uniq[0]} ${same ? "✓" : "❌"}`);
  if (!same) fail = true;
}
console.log(rows.join("\n"));
process.exit(fail ? 1 : 0);
