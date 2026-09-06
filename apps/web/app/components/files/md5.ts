/** 流式 MD5（RFC 1321）——FILE-003 §4.4 ChunkUploader 自带实现（BR-03 片级校验）。
 *
 *  为什么手写而不用 spark-md5：web 依赖清单刻意最小（无任何 crypto-MD5 包；
 *  WebCrypto 不提供 MD5），而 MinIO 的 UploadPart ETag = 片内容 MD5 的十六进制——
 *  前端算得 MD5 才能做「ETag 与登记 MD5 核对」的客户端半边（服务端半边在
 *  register_chunk）。整件 content_md5（§4.2.1 可选）由同一增量状态机顺带产出。
 *
 *  用法：new Md5().update(chunkA).update(chunkB).hex()；或一次性 md5Hex(bytes)。
 *  量程：len 以 number 计（2^53），远超单文件 5GB 上限，无 64 位长度回绕问题。
 */

const S = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

const K = new Uint32Array(64);
for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32);

function rotl(x: number, n: number): number {
  return ((x << n) | (x >>> (32 - n))) >>> 0;
}

export class Md5 {
  private a = 0x67452301;
  private b = 0xefcdab89;
  private c = 0x98badcfe;
  private d = 0x10325476;
  private readonly buf = new Uint8Array(64);
  private bufLen = 0;
  private len = 0;
  private done = false;

  update(data: Uint8Array): this {
    if (this.done) throw new Error("md5: hex() 后状态机已终结");
    let off = 0;
    if (this.bufLen > 0) {
      const take = Math.min(64 - this.bufLen, data.length);
      this.buf.set(data.subarray(0, take), this.bufLen);
      this.bufLen += take;
      off = take;
      if (this.bufLen === 64) { this.block(this.buf, 0); this.bufLen = 0; }
    }
    for (; off + 64 <= data.length; off += 64) this.block(data, off);
    if (off < data.length) {
      this.buf.set(data.subarray(off), 0);
      this.bufLen = data.length - off;
    }
    this.len += data.length;
    return this;
  }

  hex(): string {
    if (this.done) throw new Error("md5: hex() 只能调用一次");
    const bitLenLo = (this.len << 3) >>> 0;
    const bitLenHi = Math.floor(this.len / 2 ** 29);
    const padLen = this.bufLen < 56 ? 56 - this.bufLen : 120 - this.bufLen;
    const pad = new Uint8Array(padLen + 8);
    pad[0] = 0x80;
    const dv = new DataView(pad.buffer);
    dv.setUint32(padLen, bitLenLo, true);
    dv.setUint32(padLen + 4, bitLenHi, true);
    this.update(pad);
    this.done = true;
    const toHexByte = (n: number) => (n & 0xff).toString(16).padStart(2, "0");
    // 小端序输出：每 32 位字的低字节在前（byte0..byte3）
    const word = (n: number) =>
      toHexByte(n) + toHexByte(n >>> 8) + toHexByte(n >>> 16) + toHexByte(n >>> 24);
    return word(this.a) + word(this.b) + word(this.c) + word(this.d);
  }

  /** 单 512bit 块压缩。 */
  private block(data: Uint8Array, off: number): void {
    let aa = this.a, bb = this.b, cc = this.c, dd = this.d;
    const m = new Uint32Array(16);
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      const b0 = data[j] ?? 0, b1 = data[j + 1] ?? 0, b2 = data[j + 2] ?? 0, b3 = data[j + 3] ?? 0;
      m[i] = (b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)) >>> 0;
    }
    for (let i = 0; i < 64; i++) {
      let f: number, g: number;
      if (i < 16) { f = (bb & cc) | (~bb & dd); g = i; }
      else if (i < 32) { f = (dd & bb) | (~dd & cc); g = (5 * i + 1) % 16; }
      else if (i < 48) { f = bb ^ cc ^ dd; g = (3 * i + 5) % 16; }
      else { f = cc ^ (bb | ~dd); g = (7 * i) % 16; }
      const tmp = dd;
      dd = cc;
      cc = bb;
      const x = (aa + f + (K[i] ?? 0) + (m[g] ?? 0)) >>> 0;
      bb = (bb + rotl(x, S[i] ?? 0)) >>> 0;
      aa = tmp;
    }
    this.a = (this.a + aa) >>> 0;
    this.b = (this.b + bb) >>> 0;
    this.c = (this.c + cc) >>> 0;
    this.d = (this.d + dd) >>> 0;
  }
}

/** 一次性 MD5（分片片级校验用：md5Hex(chunk) 与 MinIO ETag 十六进制比对）。 */
export function md5Hex(data: Uint8Array | ArrayBuffer): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  return new Md5().update(bytes).hex();
}
