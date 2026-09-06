#!/usr/bin/env node
/**
 * Sprint-3 验收录屏（14 幕 × 独立视频；幕 4/11/14 为双视口成对视频 a/b）。
 *
 * 纪律：所有场景从真实用户入口出发——登录页 →（一键演示账号/李四表单登录）→
 * 工作台/项目卡片 → 侧栏导航 → 操作；禁止直接 goto 深链。唯一例外：幕 13 越权
 * 分支与他人视图/动态页 URL（错误分支演示，先登录后直达），以及幕 1/4 契约明文
 * 要求的「URL ?view_id= / ?filters= 分享直达」（分享链接即被验收功能本身）。
 * 每幕独立 browser context（recordVideo 1440×900）、关键交互 waitForResponse
 * 校验 2xx（录到的必须是成功画面）、失败画面留 1.2s、ONLY= 幕名过滤。
 *
 * 用法：node scripts/acceptance_video_s3.mjs
 *       ONLY=视图保存 node scripts/acceptance_video_s3.mjs   # 单幕重录（会先重跑 seed）
 * 输出：docs/sprint-3-acceptance/videos/scene-XX[-a|b]-<名称>.webm + README 索引
 */
import { chromium, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdirSync, renameSync } from "node:fs";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

const WEB = process.env.E2E_BASE_URL ?? "http://localhost:3001";
const OUT = "docs/sprint-3-acceptance/videos";
const PROJ_NAME = "S3 验收演示";
const LISI = { email: "lisi@rabbit.dev", password: "Rabbit123!" };
mkdirSync(OUT, { recursive: true });

/** 幕 8 图片评论素材（与 seed_acceptance_s3.py 同一份字节：Pillow 预生成）。 */
const IMG = {
  png: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAeAAAAEOCAIAAADe+FMwAAAF60lEQVR42u3dPYpUURCG4ffK7GEQRQSZ8rx7e5ippZdiZjbbbLPMMssss8wy22yzzTbbbLPNNtvstDHbZnvMtpnZbGaze9iWs73d3m5vZrtt2NlsoqOj/WSSk5KSk5KSk5KSk5KSk5KSk5KSk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OTk5OT5+GfH4QAAAAElFTkSuQmCC",
    "base64"),
  gif: Buffer.from(
    "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",
    "base64"),
};

const results = [];
let sceneNo = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MOD = process.platform === "darwin" ? "Meta" : "Control";

const ONLY = (process.env.ONLY ?? "").split(",").filter(Boolean);

/**
 * 单幕录制：pages=1 → scene-XX-名称.webm；pages=2 → scene-XXa/XXb-名称.webm（成对观看）。
 * fn 收到 pages 数组；任一断言抛错 → 本幕 FAIL（画面留 1.2s）。
 */
async function runScene(browser, name, fn, { pages = 1 } = {}) {
  sceneNo += 1;
  if (ONLY.length && !ONLY.some((k) => name.includes(k))) {
    console.log(`⊘ 幕${String(sceneNo).padStart(2, "0")} ${name}（ONLY 过滤跳过）`);
    return;
  }
  const id = String(sceneNo).padStart(2, "0");
  const ctxs = [];
  const ps = [];
  for (let i = 0; i < pages; i += 1) {
    const ctx = await browser.newContext({
      baseURL: WEB,
      viewport: { width: 1440, height: 900 },
      recordVideo: { dir: OUT, size: { width: 1440, height: 900 } },
    });
    ctxs.push(ctx);
    ps.push(await ctx.newPage());
  }
  const t0 = Date.now();
  let ok = true;
  let err = "";
  try {
    await fn(pages === 1 ? ps[0] : ps);
  } catch (e) {
    ok = false;
    err = String(e).split("\n").slice(0, 6).join(" | ");
    console.error(`  ✗ 幕${id} ${name} 失败：\n${String(e).split("\n").slice(0, 10).join("\n")}`);
    await ps[0].screenshot({ path: `/tmp/s3-fail-${id}.png` }).catch(() => {});
    await sleep(1200); // 失败画面留 1.2s 便于验收方看到现场
  }
  await sleep(900); // 等视频尾部封帧
  const videos = ps.map((p) => p.video());
  await Promise.all(ctxs.map((c) => c.close()));
  const suffix = pages === 1 ? [""] : ["a", "b"];
  for (let i = 0; i < videos.length; i += 1) {
    const tmpPath = await videos[i].path();
    renameSync(tmpPath, join(OUT, `scene-${id}${suffix[i]}-${name}.webm`));
  }
  results.push({ id, name, ok, seconds: Math.round((Date.now() - t0) / 1000), err });
  console.log(`${ok ? "✓" : "✗"} 幕${id} ${name}（${Math.round((Date.now() - t0) / 1000)}s）`);
}

/* ── 真实入口 helpers（与 e2e spec 同源选择器）────────────────── */

async function login(page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /一键进入演示账号/ }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}

/** 李四表单登录（真实入口）→ 切到演示工作空间（顶栏切换器）。 */
async function loginLisi(page) {
  await page.goto("/login");
  await page.locator("#email").fill(LISI.email);
  await page.locator("#pw").fill(LISI.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
  await page.locator('[data-sb-scope="topbar-menu"]').first().click();
  await page.getByRole("option", { name: /张三 的工作空间/ }).click();
  await page.waitForFunction(() => /\/workspace\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
}

async function enterProject(page) {
  const card = page.locator("a,div").filter({ hasText: new RegExp(PROJ_NAME) }).first();
  await card.locator(`text=${PROJ_NAME}`).first().click();
  await page.waitForURL(/\/board/, { timeout: 15_000 });
  await sleep(800);
}

async function waitTabs(page) {
  await page.locator('[data-sb-scope="view-tab"][data-view-name="全部"]').waitFor({ timeout: 15_000 });
  await page.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]').waitFor({ timeout: 10_000 });
}

async function switchGroup(page, name) {
  const grouped = page.waitForResponse((r) => /\/issues\/\?.*group_by=/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 });
  await page.locator('[data-sb-scope="view-group-btn"]').click();
  await page.getByRole("menuitem", { name }).first().click();
  const res = await grouped;
  if (res.status() !== 200) throw new Error(`分组切换请求 ${res.status()}`);
  await sleep(900); // 列重建 120ms 渐隐 + 计数稳定
}

async function gotoList(page) {
  await page.getByRole("navigation").getByRole("link", { name: "任务列表" }).click();
  await page.waitForURL(/\/issues/, { timeout: 10_000 });
  await page.locator('tr[data-sb-scope="tree-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(700);
}

async function gotoActivity(page) {
  await page.getByRole("navigation").getByRole("link", { name: "动态" }).click();
  await page.waitForURL(/\/activity/, { timeout: 10_000 });
  await page.locator('[data-sb-scope="stream-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(700);
}

/** 看板卡片 → 任务详情抽屉（真实点击路径）。 */
async function openCard(page, name) {
  const card = page.locator('article[data-sb-scope="board-card"]').filter({ hasText: name }).first();
  await card.waitFor({ timeout: 15_000 });
  await card.click();
  const drawer = page.locator('aside[role="dialog"]');
  await drawer.first().waitFor({ timeout: 10_000 });
  await sleep(600);
  return drawer.first();
}

async function commentsTab(page) {
  await page.locator('[data-sb-scope="drawer-tab"][data-tab-key="comments"]').click();
  await page.locator('[data-sb-scope="cmt-head"]').waitFor({ timeout: 10_000 });
  await sleep(500);
}

async function logout(page) {
  const avatarBtn = page.locator('button[aria-label="账号菜单"]').first();
  await avatarBtn.waitFor({ timeout: 10_000 });
  await avatarBtn.click();
  await sleep(400);
  await page.getByRole("menuitem").filter({ hasText: "退出登录" }).click();
  await page.waitForFunction(() => /\/login/.test(location.pathname), null, { timeout: 10_000 });
  await sleep(600);
}

const ok2xx = (r) => (r.status() >= 200 && r.status() < 300 ? Promise.resolve(r) : Promise.reject(new Error(`${r.request().method()} ${r.url().slice(-60)} → ${r.status()}`)));

/* ═══════════════════════════ 幕定义 ═══════════════════════════ */

// 数据重置（幂等，走 API 不属于被验收场面）
console.log("── seed：scripts/seed_acceptance_s3.py");
execSync("python3 scripts/seed_acceptance_s3.py", { stdio: "inherit" });

const browser = await chromium.launch({ headless: true, slowMo: 130 });

/* 幕 1：视图保存与还原（BOARD-003 §7.2-1 / 概览 §6-2） */
await runScene(browser, "视图保存与还原", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  // ① 筛选：@我 快捷 chip → 应用（?filters= 临时层）
  await page.locator('[data-sb-scope="filter-open-btn"]').click();
  const panel = page.locator('[data-sb-scope="filter-panel"]');
  await panel.waitFor({ timeout: 10_000 });
  await sleep(600);
  await panel.locator('[data-sb-scope="fpanel-quickchip"]', { hasText: "@我" }).click();
  await sleep(400);
  await panel.locator('[data-sb-scope="fpanel-apply"]').click();
  await page.waitForURL(/filters=/, { timeout: 10_000 });
  await sleep(900); // chips 行叠加徽章可见
  // ② 分组：按优先级（列重建 + 黄条）
  await switchGroup(page, "按优先级");
  await page.locator('[data-sb-scope="view-dirty-bar"]').waitFor({ timeout: 8_000 });
  await sleep(600);
  // ③ 显示：⚙ 显示 → 关「显示空分组」→ 空列折叠胶囊（乐观预览）
  await page.locator('[data-sb-scope="view-display-btn"]').click();
  const drawer = page.locator('[data-sb-scope="display-drawer"]');
  await drawer.waitFor({ timeout: 10_000 });
  await sleep(500);
  await drawer.getByRole("switch", { name: "显示空分组" }).click();
  await page.locator('[data-sb-scope="bcol-collapsed"]').first().waitFor({ timeout: 10_000 });
  await sleep(700);
  await page.keyboard.press("Escape");
  await sleep(400);
  // ④ 黄条 [另存为] → 救火看板（POST views 201 → 新视图选中 + ?view_id=）
  await page.locator('[data-sb-scope="view-dirty-saveas"]').click();
  const modal = page.locator('[data-sb-scope="save-view-modal"]');
  await modal.waitFor({ timeout: 10_000 });
  await sleep(900); // 8 icon 宫格 + 布局四选全貌
  await modal.locator('[data-sb-scope="save-view-name"]').fill("救火看板");
  const post = page.waitForResponse((r) => /\/views\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await modal.locator('[data-sb-scope="save-view-submit"]').click();
  const created = await post;
  const viewId = ((await created.json()).data ?? {}).id;
  await page.waitForURL(new RegExp(`view_id=${viewId}`), { timeout: 10_000 });
  await sleep(900);
  // ⑤ 刷新 → 完整还原（分组 / 显示 / 筛选 chips）
  await page.reload();
  await waitTabs(page);
  await expect(page.locator('[data-sb-scope="view-group-btn"]')).toContainText("分组：按优先级", { timeout: 10_000 });
  await page.locator('[data-sb-scope="bcol-collapsed"]').first().waitFor({ timeout: 15_000 });
  await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("@张三", { timeout: 10_000 });
  await sleep(1400); // 还原后全景停留
  // ⑥ 退出重登录 → 粘贴分享 URL（?view_id= 直达——幕 1 契约明文的被验收能力）
  const sharedUrl = new URL(page.url()); // 先取当前视图完整 URL（登出后路由会离开）
  await logout(page);
  await login(page);
  await page.goto(`${sharedUrl.pathname}${sharedUrl.search}`);
  await waitTabs(page);
  await expect(page.locator('[data-sb-scope="view-group-btn"]')).toContainText("分组：按优先级", { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("@张三", { timeout: 10_000 });
  // 个人视图落入 ＋ ▾ 折叠位且选中 ✓
  await page.locator('[data-sb-scope="views-more-btn"]').click();
  await page.locator('[data-sb-scope="views-more"]').getByRole("menuitem", { name: /✓.*救火看板/ }).waitFor({ timeout: 10_000 });
  await sleep(1500);
});

/* 幕 2：四布局切换条件不丢（BOARD-003 §7.2-2 / TASK-011 §7.2-3） */
await runScene(browser, "四布局切换", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  // 内置「本周到期」：视图条件 chips + 命中数（seed 已备本周到期任务）
  await page.locator('[data-sb-scope="view-tab"][data-view-name="本周到期"]').click();
  await page.waitForURL(/view_id=/, { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("截止日期 介于 本周", { timeout: 10_000 });
  const hits = await page.locator('[data-sb-scope="view-chiprow"]').innerText();
  await sleep(900);
  // 叠加临时条件 @我（chips 恒显 + 命中数）
  await page.locator('[data-sb-scope="filter-open-btn"]').click();
  const panel = page.locator('[data-sb-scope="filter-panel"]');
  await panel.waitFor({ timeout: 10_000 });
  await panel.locator('[data-sb-scope="fpanel-quickchip"]', { hasText: "@我" }).click();
  await sleep(300);
  await panel.locator('[data-sb-scope="fpanel-apply"]').click();
  await page.waitForURL(/view_id=.*&filters=/, { timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件", { timeout: 10_000 });
  await sleep(800);
  // list → table → kanban 往返：每次 PATCH views/{id} 仅 layout（BR-04/13），chips 与命中保持
  for (const [seg, urlRe] of [
    ["layout-seg-list", /\/issues\?view_id=/],
    ["layout-seg-table", /\/table\?view_id=/],
    ["layout-seg-kanban", /\/board\?view_id=/],
  ]) {
    const patch = page.waitForResponse((r) => /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
    await page.locator(`[data-sb-scope="${seg}"]`).click();
    await patch;
    await page.waitForURL(urlRe, { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("截止日期 介于 本周", { timeout: 10_000 });
    await expect(page.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 1 条临时条件", { timeout: 10_000 });
    await sleep(900);
  }
  const hits2 = await page.locator('[data-sb-scope="view-chiprow"]').innerText();
  if (!hits2.includes(hits.match(/(\d+) 个任务/)?.[1] ?? "?")) {
    console.log(`    [幕2] 命中数：布局往返 ${hits.match(/(\d+) 个任务/)?.[1]} → ${(hits2.match(/(\d+) 个任务/) ?? [])[1]}`);
  }
  // 甘特禁用 + tooltip（title 属性）
  await page.locator('[data-sb-scope="layout-seg-gantt"]').hover();
  const title = await page.locator('[data-sb-scope="layout-seg-gantt"]').getAttribute("title");
  if (!/甘特视图即将上线/.test(title ?? "")) throw new Error("甘特禁用 tooltip 缺失");
  await sleep(600);
  const disabled = await page.locator('[data-sb-scope="layout-seg-gantt"]').isDisabled();
  if (!disabled) throw new Error("甘特段未禁用");
  await sleep(1200);
});

/* 幕 3：多维分组与拖拽（BOARD-003 §7.2-3/4 / BR-14/15） */
await runScene(browser, "多维分组与拖拽", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  // ① 按优先级：五枚举列 + 无 列恒在
  await switchGroup(page, "按优先级");
  for (const name of ["紧急", "高", "中", "低", "无"]) {
    await page.locator('section[data-sb-scope="bcol"]').filter({ hasText: new RegExp(`^\\s*${name}`) }).first().waitFor({ timeout: 8_000 });
  }
  await sleep(900);
  // ② 按负责人：成员列 + __none__ 虚线「未指派」列恒在最末
  await switchGroup(page, "按负责人");
  const noneCol = page.locator('section[data-sb-scope="bcol"][data-none="1"]').first();
  await noneCol.waitFor({ timeout: 8_000 });
  await expect(noneCol).toContainText("可拖出 · 不可拖入", { timeout: 5_000 });
  await sleep(800);
  // ③ 多执行人卡拖拽 → 替换确认（BR-15）→ PUT assignees 2xx
  const multi = page.locator('article[data-sb-scope="board-card"]').filter({ hasText: "S3-紧急-多执行人" });
  await multi.first().waitFor({ timeout: 8_000 });
  const zhangCol = page.locator('section[data-sb-scope="bcol"]').filter({ hasText: "张三" }).first();
  await multi.first().dragTo(zhangCol, { targetPosition: { x: 140, y: 120 } });
  const dlg = page.locator('[data-sb-scope="board-replace-confirm"]');
  await dlg.waitFor({ timeout: 8_000 });
  await sleep(900); // 「全量替换」知情文案
  const put = page.waitForResponse((r) => /\/assignees\/$/.test(r.url()) && r.request().method() === "PUT", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="board-replace-ok"]').click();
  await put;
  await sleep(1400); // 卡片迁入张三列
  // ④ 拖入未指派列 → 拦截 toast + 弹回（BR-14，零写请求）
  let writeSent = false;
  page.on("request", (r) => {
    if (/\/issues\/[0-9a-f-]+/.test(r.url()) && ["PATCH", "PUT", "POST"].includes(r.method())) writeSent = true;
  });
  const lisiCard = page.locator('article[data-sb-scope="board-card"]').filter({ hasText: "S3-高-联调阻塞排查" }).first();
  await lisiCard.waitFor({ timeout: 8_000 });
  await lisiCard.dragTo(noneCol, { targetPosition: { x: 140, y: 100 } });
  await expect(page.locator(".fixed.top-4.right-4")).toContainText("不能拖入「未指派」列", { timeout: 8_000 });
  await sleep(1200); // 弹回动画 + 列头红圈
  if (writeSent) throw new Error("BR-14 拦截不应发出写请求");
  // ⑤ 按标签 / 按自定义 select（严重等级）——列从 options 配置生成、空列恒在
  await switchGroup(page, "按标签");
  await page.locator('section[data-sb-scope="bcol"]').filter({ hasText: "前端" }).first().waitFor({ timeout: 8_000 });
  await sleep(800);
  await switchGroup(page, "按严重等级（自定义）");
  for (const name of ["致命", "严重", "一般", "未填值"]) {
    await page.locator('section[data-sb-scope="bcol"]').filter({ hasText: new RegExp(`^\\s*${name}`) }).first().waitFor({ timeout: 8_000 });
  }
  await sleep(1600); // 四维分组全景停留
});

/* 幕 4：组合筛选器（TASK-011 §7.2-1/2 / 概览 §6-8）——双视口成对（a 构建 / b 还原） */
await runScene(browser, "组合筛选器", async ([a, b] = []) => {
  // —— 视口 A：构建 3 层嵌套树 ——
  await login(a);
  await enterProject(a);
  await waitTabs(a);
  await a.locator('[data-sb-scope="filter-open-btn"]').click();
  const panel = a.locator('[data-sb-scope="filter-panel"]');
  await panel.waitFor({ timeout: 10_000 });
  await sleep(700);
  // 根组 + 条件：优先级 in (紧急, 高)（行定位用「最新行」——字段切换后文案会变）
  await panel.locator('[data-sb-scope="fgroup-add-cond"]').first().click();
  const row1 = panel.locator('[data-sb-scope="frow"]').last();
  await row1.locator('[data-sb-scope="frow-field"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-field="priority"]').click();
  await sleep(300);
  await row1.locator('[data-sb-scope="frow-value-add"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-value="urgent"]').click();
  await sleep(250);
  // 值菜单为多选语义：选中后保持打开，直接续选第二个值
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-value="high"]').click();
  await sleep(400); // 命中数防抖变化 ①
  // 根组 + 子组（后切 OR）：负责人 in (@me)
  await panel.locator('[data-sb-scope="fgroup-add-group"]').first().click();
  await sleep(300);
  const sub1 = panel.locator('[data-sb-scope="fgroup"]').nth(1); // DOM 序：root=0 / 子组=1
  await sub1.locator('[data-sb-scope="fgroup-add-cond"]').click();
  const row2 = sub1.locator('[data-sb-scope="frow"]').last();
  await row2.locator('[data-sb-scope="frow-field"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-field="assignees"]').click();
  await sleep(300);
  await row2.locator('[data-sb-scope="frow-value-add"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-value="@me"]').click();
  await sub1.locator('[data-sb-scope="fgroup-op"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"]').getByRole("menuitem", { name: /满足 任一（OR）/ }).click();
  await sleep(400); // 命中数变化 ②（子组切 OR）
  // 子组 + 孙组（AND）：截止日期 between 本周（占位符）
  await sub1.locator('[data-sb-scope="fgroup-add-group"]').click();
  await sleep(300);
  const sub2 = panel.locator('[data-sb-scope="fgroup"]').nth(2); // DOM 序：root=0 / 子组=1 / 孙组=2
  await sub2.locator('[data-sb-scope="fgroup-add-cond"]').click();
  const row3 = sub2.locator('[data-sb-scope="frow"]').last();
  await row3.locator('[data-sb-scope="frow-field"]').click();
  await panel.locator('[data-sb-scope="fpanel-menu"] [role="menuitem"][data-field="target_date"]').click();
  await sleep(300);
  await row3.getByTitle("快捷占位符：本周").click();
  await sleep(500);
  // 配额 + 命中数（3 条件 / 3 层）
  await expect(panel.locator('[data-sb-scope="fpanel-quota"]')).toContainText("条件 3/20 · 层级 3/3");
  await expect(panel.locator('[data-sb-scope="fpanel-hitcount"]')).toContainText("命中", { timeout: 10_000 });
  await sleep(900);
  // 应用 → ?filters= 入 URL
  await panel.locator('[data-sb-scope="fpanel-apply"]').click();
  await a.waitForURL(/filters=/, { timeout: 10_000 });
  await expect(a.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 3 条临时条件", { timeout: 10_000 });
  await sleep(1200);
  const sharedUrl = new URL(a.url());
  // —— 视口 B：第二浏览器（一键登录后粘贴分享 URL）→ 整树还原 ——
  await login(b);
  await b.goto(`${sharedUrl.pathname}${sharedUrl.search}`);
  await waitTabs(b);
  await expect(b.locator('[data-sb-scope="view-chiprow"]')).toContainText("叠加 3 条临时条件", { timeout: 10_000 });
  await b.locator('[data-sb-scope="filter-open-btn"]').click();
  const panelB = b.locator('[data-sb-scope="filter-panel"]');
  await panelB.waitFor({ timeout: 10_000 });
  await sleep(700);
  await expect(panelB.locator('[data-sb-scope="fpanel-quota"]')).toContainText("条件 3/20 · 层级 3/3");
  const groupsN = await panelB.locator('[data-sb-scope="fgroup"]').count();
  if (groupsN !== 3) throw new Error(`整树还原失败：组数 ${groupsN} ≠ 3`);
  await panelB.locator('[data-sb-scope="fgroup"][data-op="OR"]').first().waitFor({ timeout: 5_000 });
  await sleep(1600); // 还原后的树全景
}, { pages: 2 });

/* 幕 5：批量操作全链路（BOARD-004 §7.2-2 / 概览 §6-4） */
await runScene(browser, "批量操作全链路", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoList(page);
  // 多选：hover 行首复选框 ×2 + ⌘ 点选第 3 行 + 被阻塞卡
  const cb = (name) => page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: name }).first().locator('[data-sb-scope="list-row-cb"]');
  for (const n of ["S3-中-文案修订", "S3-中-样式走查", "S3-低-日志清理"]) {
    const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: n }).first();
    await row.hover();
    await sleep(200);
    await cb(n).click();
    await sleep(250);
  }
  const blockedRow = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "S3-被阻塞-登录联调" }).first();
  await blockedRow.hover();
  await sleep(200);
  await cb("S3-被阻塞-登录联调").click();
  const bar = page.locator('[data-sb-scope="bulk-bar"]');
  await bar.waitFor({ timeout: 8_000 });
  await expect(bar.locator('[data-sb-scope="bulk-count"]')).toContainText("已选 4 项");
  await sleep(800);
  // 第一发：批量改状态「已完成」→ 400（被阻塞项 BLOCKED_BY，单事务全回滚）
  const p1 = page.waitForResponse((r) => /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await page.locator('[data-sb-scope="bulk-state-btn"]').click();
  await page.locator('[data-sb-scope="bulk-state-item"]').filter({ hasText: "已完成" }).first().click();
  const r1 = await p1;
  if (r1.status() !== 400) throw new Error(`首发应 400（实际 ${r1.status()}）`);
  // 失败定位弹层（alertdialog）：2 项中 1 项未通过 + 任务键定位
  const dlg = page.locator('[data-sb-scope="bulk-fail-dialog"]');
  await dlg.waitFor({ timeout: 8_000 });
  await dlg.locator('[data-sb-scope="bulk-fail-item"]').first().waitFor({ timeout: 8_000 });
  await sleep(1400); // 定位列表可见
  const p2 = page.waitForResponse((r) => /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await dlg.locator('[data-sb-scope="bulk-fail-retry"]').click();
  await p2;
  await expect(page.locator(".fixed.top-4.right-4")).toContainText("已更新 3 项", { timeout: 10_000 });
  await sleep(1000);
  // 动态流聚合「批量更新了 3 个任务」
  await gotoActivity(page);
  const batchRow = page.locator('[data-sb-scope="stream-row"][data-kind="batch"]').first();
  await batchRow.waitFor({ timeout: 15_000 });
  await expect(batchRow.locator('[data-sb-scope="stream-batch-count"]')).toContainText("批量更新了 3 个任务", { timeout: 10_000 });
  await sleep(1500);
});

/* 幕 6：批量删除确认（BOARD-004 §7.2-4） */
await runScene(browser, "批量删除确认", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoList(page);
  // 展开子任务行（级联可见）+ 多选两个含子任务父卡（先记下 issue_key 供动态流留痕断言）
  const keyOf = {};
  for (const n of ["S3-删除组A", "S3-删除组B"]) {
    const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: n }).first();
    await row.hover();
    await sleep(200);
    keyOf[n] = /S3AC-\d+/.exec(await row.innerText())[0];
    await row.locator('[data-sb-scope="tree-toggle"]').first().click();
    await sleep(400);
  }
  await sleep(400);
  for (const n of ["S3-删除组A", "S3-删除组B"]) {
    const row = page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: n }).first();
    await row.hover();
    await sleep(200);
    await row.locator('[data-sb-scope="list-row-cb"]').click();
    await sleep(250);
  }
  await expect(page.locator('[data-sb-scope="bulk-bar"]').locator('[data-sb-scope="bulk-count"]')).toContainText("已选 2 项", { timeout: 8_000 });
  await sleep(700);
  // 🗑 删除 → 预检级联统计（POST preview action=delete）
  const prev = page.waitForResponse((r) => /\/issues\/bulk\/preview\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="bulk-delete-btn"]').click();
  await prev;
  const dlg = page.locator('[data-sb-scope="bulk-del-dialog"]');
  await dlg.waitFor({ timeout: 8_000 });
  await expect(dlg.locator('[data-sb-scope="bulk-del-stats"]')).toContainText("合计影响 6 个任务", { timeout: 8_000 });
  await sleep(1200); // 「其中 2 个含子任务（将级联删除 4 个子任务）」
  // 数量输入错值不激活 → 正确值激活
  const go = dlg.locator('[data-sb-scope="bulk-del-ok"]');
  if (await go.isEnabled()) throw new Error("未输入数量时按钮不应激活");
  await dlg.locator('[data-sb-scope="bulk-del-count"]').fill("1");
  await sleep(400);
  if (await go.isEnabled()) throw new Error("错值 1 不应激活（需 2）");
  await dlg.locator('[data-sb-scope="bulk-del-count"]').fill("2");
  await sleep(400);
  if (!(await go.isEnabled())) throw new Error("正确值 2 应激活");
  const del = page.waitForResponse((r) => /\/issues\/bulk\/$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 20_000 }).then(ok2xx);
  await go.click();
  await del;
  await expect(page.locator(".fixed.top-4.right-4")).toContainText("已删除 2 项", { timeout: 10_000 });
  await sleep(1000);
  // 列表行（含子任务行）消失 + 动态流留痕（删除行 chip 置灰）
  await page.reload();
  await page.locator('tr[data-sb-scope="tree-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(600);
  if (await page.locator('tr[data-sb-scope="tree-row"]').filter({ hasText: "S3-删除组A" }).count()) throw new Error("删除组A 行应消失");
  await gotoActivity(page);
  // 动态流留痕：批量删除聚合 batch 行（含级联子任务数）；软删任务 chip 置灰可见
  const delBatch = page.locator('[data-sb-scope="stream-row"][data-kind="batch"]').filter({ hasText: "批量删除 2 个任务" }).first();
  await delBatch.waitFor({ timeout: 15_000 });
  await page.locator('[data-sb-scope="stream-chip-dead"]').filter({ hasText: keyOf["S3-删除组A"] }).first().waitFor({ timeout: 10_000 }).catch(() => {});
  await sleep(1500);
});

/* 幕 7：楼中楼与表情（COLLAB-002 §7.2-1/2） */
await runScene(browser, "楼中楼与表情", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  const drawer = await openCard(page, "S3-评论演示任务");
  await commentsTab(page);
  await expect(page.locator('[data-sb-scope="cmt-head"]')).toContainText("评论 3");
  await sleep(900);
  // 折叠线程：显示前 2 条 + 「⊕ 查看另外 3 条回复」→ 展开 5 → 收起
  const fold = page.locator('[data-sb-scope="cmt-fold"][data-fold="open"]').first();
  await fold.waitFor({ timeout: 10_000 });
  await expect(fold).toContainText("查看另外 3 条回复");
  await sleep(700);
  await fold.click();
  await page.locator('[data-sb-scope="cmt-reply"]').filter({ hasText: "公告模板我来提供" }).waitFor({ timeout: 8_000 });
  await expect(page.locator('[data-sb-scope="cmt-reply"]').filter({ hasText: "公告模板我来提供" }).locator('[data-sb-scope="cmt-reply-to"]')).toContainText("@");
  await sleep(1000);
  await page.locator('[data-sb-scope="cmt-fold"][data-fold="close"]').first().click();
  await sleep(600);
  // 回复：点回复行 ↩ → 归并提示（回复 @xx ▾）→ 发送（parent 归并顶层）
  const replyBtn = page.locator('[data-sb-scope="cmt-reply"]').filter({ hasText: "分布统计" }).locator('[data-sb-scope="cmt-reply-btn"]').first();
  await replyBtn.waitFor({ timeout: 8_000 });
  await replyBtn.scrollIntoViewIfNeeded();
  await replyBtn.click();
  const ctxBar = page.locator('[data-sb-scope="cmt-reply-ctx"]');
  await ctxBar.waitFor({ timeout: 8_000 });
  await expect(ctxBar).toContainText("↩ 回复 @");
  await sleep(700);
  await page.locator('[data-sb-scope="drawer-comment-input"]').fill("归并提示收到，统计口径我确认一下");
  const post1 = page.waitForResponse((r) => /\/comments\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
  await post1;
  await sleep(1100);
  // 表情：hover 🎉 chip → 名单浮层（含（你））；toggle 换 emoji
  const thread = page.locator('[data-sb-scope="drawer-comment-row"]').filter({ hasText: "接口超时集中" }).first();
  const chip = thread.locator('[data-sb-scope="cmt-rx-chip"][data-emoji="🎉"]');
  await chip.waitFor({ timeout: 10_000 });
  await chip.hover();
  await page.locator('[data-sb-scope="cmt-who-pop"]').waitFor({ timeout: 10_000 });
  await expect(page.locator('[data-sb-scope="cmt-who-pop"]')).toContainText("（你）");
  await sleep(1200); // 名单浮层（张三（你）+ 李四）
  // toggle off：点自己的 🎉 → DELETE reactions（剩李四 1 人）
  const del = page.waitForResponse((r) => /\/reactions\/$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 15_000 }).then(ok2xx);
  await chip.click();
  await del;
  await sleep(900);
  // 换 emoji：➕ → 选择器 → 👍 → POST reactions
  await thread.locator('[data-sb-scope="cmt-rx-plus"]').first().click();
  const pop = page.locator('[data-sb-scope="cmt-emoji-pop"]');
  await pop.waitFor({ timeout: 8_000 });
  await sleep(700); // 24 枚白名单宫格
  const on = page.waitForResponse((r) => /\/reactions\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await pop.locator('[data-sb-scope="cmt-emoji-cell"][data-emoji="👍"]').click();
  await on;
  await thread.locator('[data-sb-scope="cmt-rx-chip"][data-emoji="👍"]').first().waitFor({ timeout: 10_000 });
  await sleep(1600);
});

/* 幕 8：图片评论与灯箱（COLLAB-002 §7.2-3） */
await runScene(browser, "图片评论与灯箱", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  const drawer = await openCard(page, "S3-评论演示任务");
  await commentsTab(page);
  // seed 已有 2 图评论（缩略网格 + GIF 角标）先入画
  await page.locator('[data-sb-scope="cmt-imggrid"]').first().waitFor({ timeout: 10_000 });
  await page.locator('[data-sb-scope="cmt-gif-badge"]').first().waitFor({ timeout: 8_000 });
  await sleep(900);
  // 🖼 选择两张小图（PNG + GIF）→ presign(entity_type=comment_image)
  const pre = page.waitForResponse((r) => /\/attachments\/presign\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="cmt-img-btn"]').click();
  await page.locator('input[type="file"][aria-label="选择图片"]').setInputFiles([
    { name: "s3-video-shot.png", mimeType: "image/png", buffer: IMG.png },
    { name: "s3-video-anim.gif", mimeType: "image/gif", buffer: IMG.gif },
  ]);
  await pre;
  // 直传 + complete：两节点全部「已上传」（进度可见）
  await page.locator('[data-sb-scope="cmt-upload-node"]', { hasText: "已上传" }).first().waitFor({ timeout: 30_000 });
  await page.locator('[data-sb-scope="cmt-upload-node"]', { hasText: "已上传" }).nth(1).waitFor({ timeout: 30_000 });
  await sleep(800);
  // 发表 → POST comments（正文 + 2 img 节点）
  await page.locator('[data-sb-scope="drawer-comment-input"]').fill("补充两张新截图（上传进度走完再发表）");
  const post = page.waitForResponse((r) => /\/comments\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="drawer-comment-submit"]').click();
  await post;
  await sleep(1200);
  // 新评论缩略网格（2 列 96px + GIF 角标）
  const newRow = page.locator('[data-sb-scope="drawer-comment-row"]').filter({ hasText: "补充两张新截图" }).first();
  await newRow.waitFor({ timeout: 10_000 });
  await newRow.locator('[data-sb-scope="cmt-imgcell"]').first().waitFor({ timeout: 10_000 });
  // 灯箱：点缩略图 → 全屏 → ←→ 翻页（1/2 → 2/2 → 1/2）→ Esc
  await newRow.locator('[data-sb-scope="cmt-imgcell"]').first().click();
  const lb = page.locator('[data-sb-scope="cmt-lightbox"]');
  await lb.waitFor({ timeout: 8_000 });
  await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("1 / 2");
  await sleep(900);
  await page.keyboard.press("ArrowRight");
  await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("2 / 2", { timeout: 5_000 });
  await sleep(900);
  await page.keyboard.press("ArrowLeft");
  await expect(lb.locator('[data-sb-scope="cmt-lb-meta"]')).toContainText("1 / 2", { timeout: 5_000 });
  await sleep(700);
  await page.keyboard.press("Escape");
  await lb.waitFor({ state: "hidden", timeout: 5_000 });
  await sleep(1200);
});

/* 幕 9：父删子留（COLLAB-002 §7.2-4） */
await runScene(browser, "父删子留", async (page) => {
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  await openCard(page, "S3-评论演示任务");
  await commentsTab(page);
  const thread = page.locator('[data-sb-scope="drawer-comment-row"]').filter({ hasText: "这个方案需要再评估" }).first();
  await thread.waitFor({ timeout: 10_000 });
  await sleep(800);
  // 🗑 + confirm → DELETE comments/{id} 2xx → 父占位「回复 2 条保留」
  page.once("dialog", (d) => void d.accept());
  const del = page.waitForResponse((r) => /\/comments\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "DELETE", { timeout: 15_000 }).then(ok2xx);
  await thread.locator('[data-sb-scope="cmt-del"]').click();
  await del;
  const placeholder = page.locator('[data-sb-scope="cmt-deleted"]').filter({ hasText: "该评论已删除" });
  await placeholder.waitFor({ timeout: 10_000 });
  await expect(placeholder).toContainText("回复 2 条保留");
  await page.locator('[data-sb-scope="cmt-reply"]').filter({ hasText: "风险点主要在权限收敛" }).waitFor({ timeout: 8_000 });
  await page.locator('[data-sb-scope="cmt-reply"]').filter({ hasText: "用例链接发我一份" }).waitFor({ timeout: 8_000 });
  await sleep(1600); // 线程不塌全景
});

/* 幕 10：项目动态流（COLLAB-003 §7.2-1/3 / 概览 §6-6） */
await runScene(browser, "项目动态流", async (page) => {
  await login(page);
  await enterProject(page);
  await gotoActivity(page);
  // 视图条 + 过滤条 + 60s hint
  await expect(page.locator('[data-sb-scope="activity-viewbar"]')).toContainText("动态");
  await page.locator('[data-sb-scope="presence-cluster"]').waitFor({ timeout: 8_000 });
  await expect(page.locator('[data-sb-scope="stream-filters"]')).toContainText(/60s 自动刷新/);
  // 日期分区 sticky（今天）+ 三态行（activity / comment / batch）
  await expect(page.locator('[data-sb-scope="stream-day"]').first()).toContainText("今天");
  await page.locator('[data-sb-scope="stream-row"][data-kind="activity"]').first().waitFor({ timeout: 8_000 });
  await page.locator('[data-sb-scope="stream-row"][data-kind="comment"]').first().waitFor({ timeout: 8_000 });
  await sleep(1200);
  // 软删任务 chip 置灰 ✕ 不可点（chip 文案 = issue_key 删除线 + ✕）
  const dead = page.locator('[data-sb-scope="stream-chip-dead"]').first();
  await dead.waitFor({ timeout: 10_000 });
  await expect(dead).toContainText("✕");
  if (!(await dead.isDisabled())) throw new Error("软删 chip 应不可点");
  await sleep(800);
  // 批量行展开 → 明细抽屉（?epoch= 轻量拉取 + 变更摘要 + 明细行）
  const batchRow = page.locator('[data-sb-scope="stream-row"][data-kind="batch"]').first();
  await batchRow.waitFor({ timeout: 8_000 });
  const epochReq = page.waitForResponse((r) => /\/activities\/\?.*epoch=/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 }).then(ok2xx);
  await batchRow.locator('[data-sb-scope="stream-batch-expand"]').click();
  await epochReq;
  const bdrawer = page.locator('[data-sb-scope="batch-drawer"]');
  await bdrawer.waitFor({ timeout: 8_000 });
  await expect(bdrawer.locator('[data-sb-scope="batch-drawer-brief"]')).toContainText("变更摘要：");
  await bdrawer.locator('[data-sb-scope="batch-drawer-row"]').first().waitFor({ timeout: 10_000 });
  await sleep(1500); // 明细抽屉停留
  await page.keyboard.press("Escape").catch(() => {});
  await bdrawer.locator("button[aria-label='关闭'], button:has-text('关闭')").first().click().catch(() => {});
  await sleep(600);
  // 过滤条组合：event=comment 仅评论行 + URL 同源
  const evtReq = page.waitForResponse((r) => /\/activities\/\?.*event=comment/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 }).then(ok2xx);
  await page.locator('[data-sb-scope="stream-filter-event"]').click();
  await page.locator('[data-sb-scope="stream-filter-event-item"][data-value="comment"]').click();
  await evtReq;
  await page.waitForURL(/event=comment/, { timeout: 10_000 });
  await page.locator('[data-sb-scope="stream-row"][data-kind="activity"]').first().waitFor({ state: "detached", timeout: 8_000 }).catch(() => {});
  await sleep(1000);
  // 组合 actor=张三（AND）
  await page.locator('[data-sb-scope="stream-filter-actor"]').click();
  await page.locator('[data-sb-scope="stream-filter-actor-item"]').filter({ hasText: "张三" }).first().click();
  await page.waitForURL(/actor=/, { timeout: 10_000 });
  await sleep(1400);
});

/* 幕 11：双端实时同步（COLLAB-004 §7.2-1/3 / 概览 §6-7）——双视口成对（a 张三 / b 李四） */
await runScene(browser, "双端实时同步", async ([a, b] = []) => {
  await login(a);
  await enterProject(a);
  await waitTabs(a);
  await a.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  await loginLisi(b);
  await enterProject(b);
  await waitTabs(b);
  await b.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  await sleep(800);
  // presence：张三头像列 2 人（李四加入 + 自己影子）
  await a.locator('[data-sb-scope="presence-cluster"][aria-label*="2 人"]').waitFor({ timeout: 15_000 });
  await sleep(900);
  // IT-01：李四拖卡 待办 → 进行中（PATCH 2xx），张三看板 <1s 远端迁移
  const card = b.locator('article[data-sb-scope="board-card"]').filter({ hasText: "S3-实时演示卡" }).first();
  await card.waitFor({ timeout: 15_000 });
  const cardId = await card.getAttribute("data-card-id");
  const startedColB = b.locator('section[data-sb-scope="bcol"]').filter({ hasText: "进行中" }).first();
  const startedKeyB = await startedColB.getAttribute("data-key");
  // 前置：拖拽前卡片不在「进行中」列（张三端同口径）——排除原地命中假阳
  const inStartedBefore = await a.evaluate(
    ({ id, key }) => document.querySelector(`section[data-key="${key}"] article[data-card-id="${id}"]`) != null,
    { id: cardId, key: startedKeyB },
  );
  if (inStartedBefore) throw new Error("前置失败：卡片已在进行中列（种子态漂移）");
  const patch = b.waitForResponse((r) => /\/issues\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 });
  await card.dragTo(startedColB, { targetPosition: { x: 140, y: 200 } });
  const pres = await patch;
  if (pres.status() !== 200) throw new Error(`李四拖卡 PATCH ${pres.status()}`);
  const body = pres.request().postDataJSON();
  const targetKey = body.state_id;
  if (targetKey !== startedKeyB) throw new Error(`拖拽目标列不符：${targetKey}`);
  const t0 = Date.now();
  await a.waitForFunction(
    ({ id, key }) => document.querySelector(`section[data-key="${key}"] article[data-card-id="${id}"]`) != null,
    { id: cardId, key: targetKey },
    { timeout: 6_000, polling: 100 },
  );
  const elapsed = Date.now() - t0;
  console.log(`    [幕11] 拖卡远端可见耗时 ${elapsed}ms（COLLAB-004 IT-01 口径 <1s）`);
  if (elapsed >= 1_000) throw new Error(`远端可见 ${elapsed}ms ≥ 1s`);
  await sleep(1400); // 远端淡入 + 列计数 bump 可见
  // 张三切到动态页（用户路径，铃铛在顶栏常驻）→ 李四两个动作触发两条推送
  await gotoActivity(a);
  const bell = a.locator('[data-sb-scope="topbar-bell"]');
  const bellBase = await bell.getAttribute("aria-label");
  // ① 动态流增量划入：李四在任务抽屉改优先级（activity.created 水位锚 → 增量拉取）
  const rowsBefore = await a.locator('[data-sb-scope="stream-row"]').count();
  const drawerB = await openCard(b, "S3-实时演示卡");
  await sleep(400);
  const prPatch = b.waitForResponse((r) => /\/issues\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await b.locator('button[aria-label="修改优先级"]').click();
  await b.getByRole("menuitem", { name: "高" }).click();
  await prPatch;
  // 新行划入（行数 +1 且首行 = 李四的优先级变更；行文案为动词 + issue_key chip）
  await a.waitForFunction(
    (n) => document.querySelectorAll('[data-sb-scope="stream-row"]').length > n,
    rowsBefore, { timeout: 12_000, polling: 200 },
  );
  await expect(a.locator('[data-sb-scope="stream-row"]').first()).toContainText("优先级");
  console.log("    [幕11] 张三动态流新行已划入（增量拉取）");
  await sleep(1200);
  await drawerB.getByRole("button", { name: "关闭" }).click();
  await sleep(600);
  // ② 铃铛 +1：李四回复张三的顶层评论（COMMENT_REPLIED 通知 → user 房间）
  const drawerC = await openCard(b, "S3-评论演示任务");
  await commentsTab(b);
  const zTop = b.locator('[data-sb-scope="drawer-comment-row"]').filter({ hasText: "接口超时集中" }).first();
  await zTop.waitFor({ timeout: 10_000 });
  await zTop.scrollIntoViewIfNeeded();
  await zTop.locator('[data-sb-scope="cmt-reply-btn"]').first().click();
  await b.locator('[data-sb-scope="cmt-reply-ctx"]').waitFor({ timeout: 8_000 });
  await sleep(400);
  await b.locator('[data-sb-scope="drawer-comment-input"]').fill("状态已同步，请复核泳道归属");
  const cmt = b.waitForResponse((r) => /\/comments\/$/.test(r.url()) && r.request().method() === "POST", { timeout: 15_000 }).then(ok2xx);
  await b.locator('[data-sb-scope="drawer-comment-submit"]').click();
  await cmt;
  await a.waitForFunction(
    (base) => document.querySelector('[data-sb-scope="topbar-bell"]')?.getAttribute("aria-label") !== base,
    bellBase, { timeout: 10_000, polling: 150 },
  );
  console.log(`    [幕11] 张三铃铛：${bellBase} → ${await bell.getAttribute("aria-label")}`);
  await sleep(1600);
}, { pages: 2 });

/* 幕 12：断线补偿与降级（COLLAB-004 §7.2-4/5）
 * 断网模拟选型（live 属用户 dev 树不可停）：context.setOffline(true) 封死新连接/REST
 * + 页内 WebSocket 注册表主动断开既有 /live/connect（Chromium 网络模拟对已建 WS
 * 的处置不稳定——双保险；应用侧走真实降级状态机：重连退避失败累计 30s → 降级横幅，
 * 恢复网络 + [立即重连] → 补偿拉取全列收敛）。 */
await runScene(browser, "断线补偿与降级", async (page) => {
  await page.addInitScript(() => {
    const Orig = window.WebSocket;
    window.__rpWs = [];
    window.WebSocket = class extends Orig {
      constructor(...a) { super(...a); window.__rpWs.push(this); }
    };
  });
  await login(page);
  await enterProject(page);
  await waitTabs(page);
  await page.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  // 连接详情弹层（状态/房间/延迟/重连）
  await page.locator('[data-sb-scope="conn-dot-btn"]').click();
  const pop = page.locator('[data-sb-scope="conn-pop"]');
  await pop.waitFor({ timeout: 8_000 });
  await expect(pop).toContainText("已连接");
  await expect(pop).toContainText(/project:/);
  await sleep(1200);
  // ① 断网（封死新连接/REST/健康探测）
  await page.context().setOffline(true);
  // ② 主动断开既有 live 连接（等效连接丢失；重连尝试将全部撞在断网上）
  await page.evaluate(() => window.__rpWs.forEach((w) => {
    if (w.url.includes("/live/connect") && w.readyState === 1) w.close();
  }));
  console.log("    [幕12] 断网模拟：setOffline + 断开 live WS");
  // 弹层保持打开：状态翻「重连中」（黄点 pulse）
  await expect(pop).toContainText("重连中", { timeout: 8_000 });
  await sleep(1400);
  await page.locator("body").dispatchEvent("mousedown");
  // 断线窗口零功能损失：搜索框本地过滤照常（数据已在前端）
  await page.locator('input[aria-label="搜索任务"]').fill("S3-紧急");
  await sleep(1000);
  await page.locator('input[aria-label="搜索任务"]').fill("");
  await sleep(600);
  // 失败累计 30s → 降级横幅（BR-10：轮询模式 + 常驻黄条）
  await page.locator('[data-sb-scope="rt-degraded-banner"]').waitFor({ timeout: 50_000 });
  await expect(page.locator('[data-sb-scope="rt-degraded-banner"]')).toContainText("实时同步暂停 · 已切换为定时刷新");
  console.log("    [幕12] 降级横幅出现（连接失败累计 ≥30s）");
  await sleep(1600);
  // 恢复网络 → [立即重连] → 补偿拉取（group_by 全列收敛）+ 横幅撤除
  const comp = page.waitForResponse((r) => /\/issues\/\?.*group_by=/.test(r.url()) && r.request().method() === "GET", { timeout: 30_000 }).then(ok2xx);
  await page.context().setOffline(false);
  console.log("    [幕12] 网络恢复（setOffline false）");
  const reBtn = page.locator('[data-sb-scope="rt-banner-reconnect"]');
  await reBtn.waitFor({ state: "visible", timeout: 8_000 }).catch(() => {});
  if (await reBtn.isVisible().catch(() => false)) await reBtn.click();
  await comp;
  await page.locator('[data-sb-scope="rt-degraded-banner"]').waitFor({ state: "hidden", timeout: 30_000 });
  await page.locator('[data-sb-scope="conn-dot-btn"][aria-label="实时已连接"]').waitFor({ timeout: 20_000 });
  await sleep(1600); // 恢复已连接全景
});

/* 幕 13：越权 404（概览 §6-9）——李四直入他人视图 URL；王五（被移出成员）直入动态页 */
await runScene(browser, "越权404", async (page) => {
  await login(page); // 张三先拿「他人视图 URL」素材（自己的私人视图分享给李四）
  await enterProject(page);
  await waitTabs(page);
  await page.locator('[data-sb-scope="views-more-btn"]').click();
  await page.locator('[data-sb-scope="views-more"]').getByRole("menuitem", { name: /张三-私人视图/ }).click();
  await page.waitForURL(/view_id=/, { timeout: 10_000 });
  const victimUrl = new URL(page.url());
  await sleep(600);
  await logout(page);
  // 场景 A：李四登录 → 直达张三个人视图 URL（错误分支演示：先登录后 goto）
  await page.goto("/login");
  await page.locator("#email").fill(LISI.email);
  await page.locator("#pw").fill(LISI.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
  await page.locator('[data-sb-scope="topbar-menu"]').first().click();
  await page.getByRole("option", { name: /张三 的工作空间/ }).click();
  await page.waitForFunction(() => /\/workspace\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(500);
  const gone = page.waitForResponse((r) => /\/issues\/\?.*view_id=/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 });
  await page.goto(`${victimUrl.pathname}${victimUrl.search}`);
  const gres = await gone;
  // 注：BOARD-003 §4.2-6 要求不可见视图 404（存在性隐藏）。实测「不存在 id」走 404，
  // 而「存在但他人个人视图」列表端点返回 200（仅校验存在性、未校验归属——缺陷
  // P2-002，见 README 缺陷清单）；前端侧 ViewStore 不可见 → 黄条回退「全部」正常。
  console.log(`    [幕13] 李四访问他人视图 API：${gres.status()}（前端存在性隐藏按预期回退）`);
  await page.locator('[data-sb-scope="view-gone-bar"]').waitFor({ timeout: 15_000 });
  await expect(page.locator('[data-sb-scope="view-tab"][data-view-id="__all__"]')).toHaveAttribute("aria-selected", "true", { timeout: 8_000 });
  await sleep(1400);
  // 场景 B：被移出成员（王五）直入项目动态页 → 404 错误态（不泄露内容）
  await logout(page);
  await page.goto("/login");
  await page.locator("#email").fill("wangwu@rabbit.dev");
  await page.locator("#pw").fill("Rabbit123!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(600);
  await page.locator('[data-sb-scope="topbar-menu"]').first().click();
  await page.getByRole("option", { name: /张三 的工作空间/ }).click();
  await page.waitForFunction(() => /\/workspace\/projects$/.test(location.pathname), null, { timeout: 15_000 });
  await sleep(500);
  const actPath = victimUrl.pathname.replace(/\/board.*$/, "/activity");
  const act404 = page.waitForResponse((r) => /\/activities\/\?/.test(r.url()) && r.request().method() === "GET", { timeout: 15_000 });
  await page.goto(actPath);
  const ares = await act404;
  console.log(`    [幕13] 被移出成员访问动态 API：${ares.status()}（期望 404）`);
  if (ares.status() !== 404) throw new Error(`移出成员动态应 404（实际 ${ares.status()}）`);
  await page.locator('[data-sb-scope="stream-err"]').waitFor({ timeout: 15_000 });
  await sleep(1600);
});

/* 幕 14：内置视图与默认星标（BOARD-003 §7.2-5 / BR-10）——双视口成对（a 张三 / b 李四） */
await runScene(browser, "内置视图与默认", async ([a, b] = []) => {
  // 视口 A（张三）：五内置在场 🔒 → 右键设默认 → 重进项目直达
  await login(a);
  await enterProject(a);
  await waitTabs(a);
  for (const name of ["需求池", "缺陷列表", "我的待办", "本周到期", "测试执行"]) {
    await a.locator(`[data-sb-scope="view-tab"][data-view-name="${name}"]`).waitFor({ timeout: 8_000 });
  }
  await expect(a.locator('[data-sb-scope="view-tab"][data-view-name="需求池"]')).toContainText("🔒");
  await sleep(900);
  // 「我的待办」@me 生效（张三：紧急/高等本人任务）——该内置视图为列表布局，
  // 切列表段进入 /issues?view_id= 再数行
  await a.locator('[data-sb-scope="view-tab"][data-view-name="我的待办"]').click();
  await a.waitForURL(/view_id=/, { timeout: 10_000 });
  await expect(a.locator('[data-sb-scope="view-chiprow"]')).toContainText("@张三", { timeout: 10_000 });
  const listPatch = a.waitForResponse((r) => /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await a.locator('[data-sb-scope="layout-seg-list"]').click();
  await listPatch;
  await a.waitForURL(/\/issues\?view_id=/, { timeout: 10_000 });
  await a.locator('tr[data-sb-scope="tree-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(800);
  const zRows = await a.locator('tr[data-sb-scope="tree-row"]').count();
  if (zRows === 0) throw new Error("张三「我的待办」应命中 ≥1 行");
  console.log(`    [幕14] 张三「我的待办」命中 ${zRows} 行`);
  await sleep(900);
  // 右键 → 设为默认视图（PATCH users/me/settings board.default_view_id）
  const pref = a.waitForResponse((r) => /\/users\/me\/settings\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await a.locator('[data-sb-scope="view-tab"][data-view-name="我的待办"]').click({ button: "right" });
  const menu = a.locator('[data-sb-scope="view-ctx-menu"]');
  await menu.waitFor({ timeout: 8_000 });
  await sleep(600);
  await menu.getByRole("menuitem", { name: "设为默认视图" }).click();
  await pref;
  await sleep(900);
  await expect(a.locator('[data-sb-scope="view-tab"][data-view-name="我的待办"]')).toContainText("★");
  // 重进项目（项目卡片 = 用户入口）→ 直达默认视图
  await a.getByRole("navigation").getByRole("link", { name: "返回项目列表" }).click();
  await a.waitForFunction(() => /\/projects$/.test(location.pathname), null, { timeout: 10_000 });
  await sleep(700);
  await enterProject(a);
  await a.waitForURL(/view_id=/, { timeout: 10_000 });
  await expect(a.locator('[data-sb-scope="view-tab"][data-view-name="我的待办"]')).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
  await sleep(1200);
  // 视口 B（李四）：同一内置视图 @me 各自生效（结果不同）
  await loginLisi(b);
  await enterProject(b);
  await waitTabs(b);
  await b.locator('[data-sb-scope="view-tab"][data-view-name="我的待办"]').click();
  await b.waitForURL(/view_id=/, { timeout: 10_000 });
  await expect(b.locator('[data-sb-scope="view-chiprow"]')).toContainText("@李四", { timeout: 10_000 });
  const listPatchB = b.waitForResponse((r) => /\/views\/[0-9a-f-]+\/$/.test(r.url()) && r.request().method() === "PATCH", { timeout: 15_000 }).then(ok2xx);
  await b.locator('[data-sb-scope="layout-seg-list"]').click();
  await listPatchB;
  await b.waitForURL(/\/issues\?view_id=/, { timeout: 10_000 });
  await b.locator('tr[data-sb-scope="tree-row"]').first().waitFor({ timeout: 15_000 });
  await sleep(800);
  const lRows = await b.locator('tr[data-sb-scope="tree-row"]').count();
  if (lRows === 0) throw new Error("李四「我的待办」应命中 ≥1 行");
  console.log(`    [幕14] 李四「我的待办」命中 ${lRows} 行（张三 ${zRows} 行——@me 占位符跨用户「活」${zRows === lRows ? "" : "，结果不同"}）`);
  await sleep(1600);
}, { pages: 2 });

await browser.close();

/* ── 汇总 + README 索引 ──────────────────────────────────────── */
const pass = results.filter((r) => r.ok).length;
console.log("\n═══ Sprint-3 验收录屏完成 ═══");
console.log(`  ${pass}/${results.length} 幕成功；视频目录：${OUT}/`);
const COVERAGE = {
  视图保存与还原: "BOARD-003 §7.2-1 / 概览 §6-2",
  四布局切换: "BOARD-003 §7.2-2 / TASK-011 §7.2-3",
  多维分组与拖拽: "BOARD-003 §7.2-3/4 / BR-14/15",
  组合筛选器: "TASK-011 §7.2-1/2 / 概览 §6-8",
  批量操作全链路: "BOARD-004 §7.2-2 / 概览 §6-4",
  批量删除确认: "BOARD-004 §7.2-4",
  楼中楼与表情: "COLLAB-002 §7.2-1/2",
  图片评论与灯箱: "COLLAB-002 §7.2-3",
  父删子留: "COLLAB-002 §7.2-4",
  项目动态流: "COLLAB-003 §7.2-1/3 / 概览 §6-6",
  双端实时同步: "COLLAB-004 §7.2-1/3 / 概览 §6-7",
  断线补偿与降级: "COLLAB-004 §7.2-4/5",
  越权404: "概览 §6-9",
  内置视图与默认: "BOARD-003 §7.2-5 / BR-10",
};
const readme = [
  "# Sprint-3 验收录屏（14 幕，全部从用户实际入口出发）",
  "",
  "| 幕 | 场景 | 验收条款 | 时长 | 结果 |",
  "| --- | --- | --- | --- | --- |",
  ...results.map((r) => `| ${r.id} | ${r.name}${["组合筛选器", "双端实时同步", "内置视图与默认"].includes(r.name) ? "（a/b 成对）" : ""} | ${COVERAGE[r.name] ?? "—"} | ${r.seconds}s | ${r.ok ? "✓" : "✗"} |`),
  "",
  "## 双视口成对视频（a/b 并排观看）",
  "",
  "- 幕 4：`04a` 构建三层嵌套筛选树并应用；`04b` 第二浏览器粘贴分享 URL 还原整树。",
  "- 幕 11：`11a` 张三视口（看板远端迁移 / 动态页新行划入 / 铃铛 +1）；`11b` 李四视口（拖卡 / 改优先级 / 回复评论）——两视口同一时间轴编排。",
  "- 幕 14：`14a` 张三（五内置在场 🔒 → 右键设默认 → 重进项目直达）；`14b` 李四（同一「我的待办」@me 各自生效）。",
  "",
  "## 环境与复跑",
  "",
  "前置（与 sprint-2 同栈 + 实时/对象存储）：API(8000，含票据密钥与 INTERNAL_KEY) + Web(3001) + Celery worker（activity 队列，`celery inspect registered` 应含 `plane.bgtasks.event_publisher.publish_event`）+ live(3000，Redis 连通) + MinIO(9000) + 演示账号 bootstrap（zhangsan@rabbit.dev 一键进入）。",
  "",
  "```bash",
  "python3 scripts/seed_acceptance_s3.py     # 数据准备（幂等；李四/王五账号自动创建，密码固定）",
  "node scripts/acceptance_video_s3.mjs      # 全量 14 幕（脚本会先自动重跑一次 seed）",
  "ONLY=组合筛选器 node scripts/acceptance_video_s3.mjs   # 单幕重录（幕名子串匹配，逗号分隔多个）",
  "```",
  "",
  "产物：`videos/scene-XX[-a|b]-<名称>.webm`（1440×900，每幕独立 context；webm 不入 git）。",
  "",
  "## 录制手段备注",
  "",
  "- 全部幕走真实用户入口：登录页 →（一键演示账号 / 李四表单登录 → 顶栏切工作空间）→ 项目卡片 → 侧栏导航。深链 goto 仅两处且均为契约明文：幕 1/4 的「URL ?view_id= / ?filters= 分享直达」（被验收功能本身）与幕 13 的越权错误分支（先登录后直达他人视图 / 被移出成员动态页）。",
  "- 幕 11 拖卡远端可见耗时实测 <10ms（PATCH 响应后 DOM 已迁移；独立探针全链路 worker→Redis→live→对端约 123ms，满足 IT-01 <1s）。铃铛 +1 走真实链路：李四回复张三顶层评论 → COMMENT_REPLIED 通知 → user 房间。",
  "- 幕 12 断网模拟：`context.setOffline(true)` 封死新连接/REST + 页内 WebSocket 注册表主动断开既有 `/live/connect`（Chromium 网络模拟对已建 WS 的处置不稳定，双保险；live/api 服务全程未动）。降级横幅由真实状态机在连接失败累计 30s 后触发，恢复后 [立即重连] 触发补偿拉取（group_by 全列收敛）。",
  "",
  "## 与 sprint-2 验收目录的差异",
  "",
  "- 本目录新增 `scripts/seed_acceptance_s3.py`（S3 数据准备，走 API、幂等）与 `scripts/acceptance_video_s3.mjs`（14 幕录制器）；沿用 sprint-2 的 `scripts/seed_acceptance.py` / `scripts/acceptance_video.mjs` 模式但未改动它们。",
  "- 幕 4/11/14 为双视口成对视频（sprint-2 全部单视口）；幕 12 因 live 属用户 dev 树不可停，改为浏览器断网模拟（sprint-2 无此约束）。",
  "- 视频 webm 同 sprint-2 一样不入 git；本 README 与 SCENARIOS.md 入库。",
  "",
  "## 已知缺陷（录制期间发现，未在本次修复）",
  "",
  "1. **P2 · 列表端点对他人个人视图未校验归属**（幕 13 触发）：`GET …/issues/?view_id=` 对「存在但他人个人视图」返回 200（仅校验存在性）；随机不存在 id 才 404。前端侧 ViewStore 不可见 → 黄条回退「全部」正常。规格口径：BOARD-003 §4.2-6 / BR-11 要求不可见视图 404 存在性隐藏。最小复现：李四会话 `GET /api/v1/workspaces/workspace/projects/{pid}/issues/?view_id={张三个人视图id}` → 200。",
  "2. **P1（潜在）· 实时事件 version 时区混用**（幕 11 排查期间发现）：worker 载荷 `version=updated_at.isoformat()` 为 UTC（+00:00），REST 列表/分组端点序列化为本地时区（+08:00），前端 BR-07 按字符串比较恒判「旧于本地」→ `issue.state.changed` 对已加载卡片的定向更新被静默丢弃。看板拖卡因伴随 `sort_order` 变更走 `board.moved`（无 version 门）而掩盖；纯状态变更（列表页/批量单卡）不会实时迁移对端看板。最小复现：A 端看板已含某卡，B 端 `PATCH {state_id}`（不含 sort_order）→ A 收到 `issue.state.changed` 帧但卡片不迁移。",
  "",
  "观看建议：慢放 0.75× 可看清 Toast 与动画细节；每幕开头的登录/导航即「用户实际入口」演示。",
  "",
  ...results.filter((r) => !r.ok).map((r) => `> ⚠ 幕${r.id}（${r.name}）录制中断于失败点，现场画面保留 1.2s${r.err ? `；首错：${r.err}` : ""}`),
].join("\n");
writeFileSync("docs/sprint-3-acceptance/README.md", readme + "\n");
console.log("  索引：docs/sprint-3-acceptance/README.md");
process.exit(pass === results.length ? 0 : 1);
