/** 票据验签（COLLAB-004 §4.3.2，api-conventions.md §9.5）。
 *
 *  - live 只持公钥（RS256 verify only）——被攻破也无法伪造票据（UT-02）；
 *  - algorithms 锁死 ["RS256"]：alg=none / HS256 混淆 / 无签名一律拒（UT-01）；
 *  - clockTolerance 5s：api/live 容器时钟微小漂移容忍；
 *  - rooms 声明与 sub 一致性强校验（BR-01）：房间名三类形态合法、上限 10、
 *    user 房间必须恰为 user:{sub}、ws 声明必须以 {sub}: 开头。
 */
import jwt from "jsonwebtoken";

export interface TicketClaims {
  sub: string;
  rooms: string[];
  /** BR-12 去重 / 重连幂等键 = {sub}:{client_tab_id}（§4.2.1） */
  ws: string;
  iat: number;
  exp: number;
  jti: string;
}

export const MAX_ROOMS_PER_TICKET = 10;

const ROOM_PATTERN = /^(project|issue|user):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** 从 upgrade URL 提取票据（/live/connect?token=…）。 */
export function extractToken(url: string | undefined): string | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url, "http://live.internal");
  } catch {
    return null;
  }
  return parsed.searchParams.get("token");
}

/** 验签 + 声明形态校验；任何失败统一 null（调用方 close 4001 TOKEN_INVALID）。 */
export function verifyTicket(token: string | null, publicKey: string): TicketClaims | null {
  if (!token || !publicKey) return null;
  // PEM 归一：兼容 .env 单行 ``\n`` 转义形态与真实多行形态（与 api 侧 normalize_pem 对齐）
  const pem = publicKey.includes("\\n") ? publicKey.replace(/\\n/g, "\n") : publicKey;
  let claims: jwt.JwtPayload;
  try {
    const decoded = jwt.verify(token, pem, {
      algorithms: ["RS256"],
      clockTolerance: 5,
    });
    if (typeof decoded === "string") return null;
    claims = decoded;
  } catch {
    return null; // 过期 / 伪造 / 算法降级统一 4001（UT-01）
  }
  const { sub, rooms, ws, jti } = claims;
  if (typeof sub !== "string" || !UUID_PATTERN.test(sub)) return null;
  if (typeof ws !== "string" || !ws.startsWith(`${sub}:`)) return null; // sub 一致性
  if (typeof jti !== "string" || jti.length === 0) return null;
  if (!Array.isArray(rooms) || rooms.length === 0 || rooms.length > MAX_ROOMS_PER_TICKET) {
    return null; // 边界：房间声明上限 10（§2.6）
  }
  let userRoomSeen = false;
  for (const room of rooms) {
    if (typeof room !== "string" || !ROOM_PATTERN.test(room)) return null;
    if (room.startsWith("user:")) {
      if (room !== `user:${sub}`) return null; // 只能订本人房间
      userRoomSeen = true;
    }
  }
  if (!userRoomSeen) return null; // user:{sub} 恒附（§4.2.1 rooms 装配契约）
  return {
    sub,
    rooms: rooms as string[],
    ws,
    iat: Number(claims.iat ?? 0),
    exp: Number(claims.exp ?? 0),
    jti,
  };
}
