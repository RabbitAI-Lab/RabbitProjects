/** 票据验签测试（COLLAB-004 §5.1 UT-01/UT-02/UT-03）。 */
import { describe, expect, it } from "vitest";
import { makeKeyPair, signTicket, uuid } from "./testutil";
import { extractToken, MAX_ROOMS_PER_TICKET, verifyTicket } from "./ticket";

const keys = makeKeyPair();
// 「他方密钥」也模块级预生成：RSA 2048 质数搜索在慢速 CI 跑机上可超 5s 单测限时
// （用例内现场 makeKeyPair() 曾致 UT-01「错误公钥」在 GitHub runner 超时）
const otherKeys = makeKeyPair();

describe("extractToken", () => {
  it("从 /live/connect?token=… 提取票据", () => {
    const token = signTicket(keys);
    expect(extractToken(`/live/connect?token=${encodeURIComponent(token)}`)).toBe(token);
  });
  it("缺 token 返回 null", () => {
    expect(extractToken("/live/connect")).toBeNull();
    expect(extractToken(undefined)).toBeNull();
  });
});

describe("verifyTicket（UT-01 安全）", () => {
  it("有效票据通过并返回 claims", () => {
    const sub = uuid();
    const token = signTicket(keys, { sub, clientTabId: "tab-1" });
    const claims = verifyTicket(token, keys.publicKey);
    expect(claims).not.toBeNull();
    expect(claims?.sub).toBe(sub);
    expect(claims?.rooms).toContain(`user:${sub}`);
    expect(claims?.ws).toBe(`${sub}:tab-1`);
  });

  it("过期票据拒绝", () => {
    expect(verifyTicket(signTicket(keys, { expired: true }), keys.publicKey)).toBeNull();
  });

  it("alg=none 无签名票据拒绝（算法降级）", () => {
    expect(verifyTicket(signTicket(keys, { algorithm: "none" }), keys.publicKey)).toBeNull();
  });

  it("错误公钥（他方密钥签发）拒绝", () => {
    expect(verifyTicket(signTicket(otherKeys), keys.publicKey)).toBeNull();
  });

  it("伪造字符串票据拒绝", () => {
    expect(verifyTicket("not.a.jwt", keys.publicKey)).toBeNull();
    expect(verifyTicket(null, keys.publicKey)).toBeNull();
  });
});

describe("verifyTicket 声明一致性（BR-01 强校验）", () => {
  it("ws 声明与 sub 不一致拒绝", () => {
    const token = signTicket(keys, { wsOverride: `${uuid()}:tab-1` });
    expect(verifyTicket(token, keys.publicKey)).toBeNull();
  });

  it("user 房间非本人（user:{他者}）拒绝", () => {
    const sub = uuid();
    const token = signTicket(keys, {
      sub, rooms: [`project:${uuid()}`, `user:${uuid()}`],
    });
    expect(verifyTicket(token, keys.publicKey)).toBeNull();
  });

  it("缺 user:{sub} 房间拒绝（§4.2.1 rooms 装配契约）", () => {
    const token = signTicket(keys, { rooms: [`project:${uuid()}`] });
    expect(verifyTicket(token, keys.publicKey)).toBeNull();
  });

  it("非法房间名（未知前缀 / 非 UUID）拒绝", () => {
    const sub = uuid();
    expect(verifyTicket(
      signTicket(keys, { sub, rooms: [`workspace:${uuid()}`, `user:${sub}`] }),
      keys.publicKey,
    )).toBeNull();
    expect(verifyTicket(
      signTicket(keys, { sub, rooms: ["project:not-a-uuid", `user:${sub}`] }),
      keys.publicKey,
    )).toBeNull();
  });
});

describe("verifyTicket 第四类房间 file:{asset_id}（FILE-003 §4.4）", () => {
  it("file:{asset_id} 房间声明通过（订阅条件在 api 换票时校验，live 仅验形态）", () => {
    const sub = uuid();
    const assetId = uuid();
    const token = signTicket(keys, {
      sub,
      rooms: [`project:${uuid()}`, `file:${assetId}`, `user:${sub}`],
    });
    const claims = verifyTicket(token, keys.publicKey);
    expect(claims).not.toBeNull();
    expect(claims?.rooms).toContain(`file:${assetId}`);
  });

  it("file 房间非 UUID 拒绝（与 project/issue 同形态闸）", () => {
    const sub = uuid();
    expect(verifyTicket(
      signTicket(keys, { sub, rooms: ["file:not-a-uuid", `user:${sub}`] }),
      keys.publicKey,
    )).toBeNull();
  });

  it("file 房间不豁免 user:{sub} 恒附契约（缺本人房间仍拒绝）", () => {
    const sub = uuid();
    expect(verifyTicket(
      signTicket(keys, { sub, rooms: [`file:${uuid()}`] }),
      keys.publicKey,
    )).toBeNull();
  });
});

describe("verifyTicket 房间上限（UT-03 边界）", () => {
  it("声明 11 房间拒绝；10 房间（含 user:{sub}）通过", () => {
    const sub = uuid();
    // 10 = project 1 + issue 8 + user 1（api 侧装配口径：issue_rooms ≤ 8）
    const rooms = Array.from({ length: 8 }, () => `issue:${uuid()}`);
    const ok = verifyTicket(
      signTicket(keys, { sub, rooms: [`project:${uuid()}`, ...rooms, `user:${sub}`] }),
      keys.publicKey,
    );
    expect(ok).not.toBeNull();
    expect(ok?.rooms.length).toBe(MAX_ROOMS_PER_TICKET);

    // 11 = project 1 + issue 9 + user 1
    const over = [`project:${uuid()}`, ...rooms, `issue:${uuid()}`, `user:${sub}`];
    expect(over.length).toBe(11);
    expect(verifyTicket(signTicket(keys, { sub, rooms: over }), keys.publicKey)).toBeNull();
  });
});
