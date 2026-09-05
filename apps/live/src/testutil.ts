/** 测试共用：RSA 密钥对 + 票据签发/装配辅助（对齐 api 侧 PyJWT RS256 输出）。 */
import { generateKeyPairSync } from "node:crypto";
import jwt from "jsonwebtoken";
import type { TicketClaims } from "./ticket";

export interface KeyPair {
  publicKey: string;
  privateKey: string;
}

export function makeKeyPair(): KeyPair {
  const { publicKey, privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  return { publicKey, privateKey };
}

export function uuid(): string {
  return crypto.randomUUID();
}

export interface SignOptions {
  sub?: string;
  rooms?: string[];
  clientTabId?: string;
  ttlSeconds?: number;
  expired?: boolean;
  /** 签名算法覆盖（alg=none 攻击用例） */
  algorithm?: "RS256" | "none";
  /** 票据外的 ws 声明（sub 不一致用例） */
  wsOverride?: string;
}

export function signTicket(keys: KeyPair, opts: SignOptions = {}): string {
  const sub = opts.sub ?? uuid();
  const rooms = opts.rooms ?? [`project:${uuid()}`, `user:${sub}`];
  const now = Math.floor(Date.now() / 1000);
  const ttl = opts.ttlSeconds ?? 120;
  const payload: Record<string, unknown> = {
    sub,
    rooms,
    ws: opts.wsOverride ?? `${sub}:${opts.clientTabId ?? uuid()}`,
    iat: now,
    exp: now + (opts.expired ? -10 : ttl),
    jti: `jti-${uuid()}`,
  };
  if (opts.algorithm === "none") {
    // 无签名变体（header {"alg":"none"}）：live 必须拒（UT-01）
    const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" }))
      .toString("base64url");
    const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
    return `${header}.${body}.`;
  }
  return jwt.sign(payload, keys.privateKey, { algorithm: "RS256" });
}

export function claimsOf(token: string): TicketClaims {
  return jwt.decode(token) as TicketClaims;
}
