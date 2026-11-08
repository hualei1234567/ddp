/**
 * 流式 SHA-256 实现。
 *
 * 为什么不用 `crypto.subtle.digest`：它要求一次性持有完整 ArrayBuffer，
 * 大文件会占用大量内存。这里按块更新，前端可以在上传/完成时流式计算，
 * 与契约的 `sha256:<64hex>` 表示保持一致。
 *
 * 该实现不用于密码学密钥派生，只用于内容完整性校验。
 */

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotr(value: number, bits: number): number {
  return ((value >>> bits) | (value << (32 - bits))) >>> 0;
}

export function bytesToHex(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) {
    text += byte.toString(16).padStart(2, "0");
  }
  return text;
}

export function bytesToBase64(bytes: Uint8Array): string {
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let output = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const first = bytes[index] ?? 0;
    const second = bytes[index + 1];
    const third = bytes[index + 2];
    const hasSecond = second !== undefined;
    const hasThird = third !== undefined;
    const triplet =
      (first << 16) | ((second ?? 0) << 8) | (third ?? 0);
    output += alphabet[(triplet >>> 18) & 0x3f];
    output += alphabet[(triplet >>> 12) & 0x3f];
    output += hasSecond ? alphabet[(triplet >>> 6) & 0x3f] : "=";
    output += hasThird ? alphabet[triplet & 0x3f] : "=";
  }
  return output;
}

export class Sha256Hasher {
  private readonly h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
    0x1f83d9ab, 0x5be0cd19,
  ]);
  private readonly buffer = new Uint8Array(64);
  private readonly schedule = new Uint32Array(64);
  private bufferLength = 0;
  private bytesHashed = 0;
  private finished = false;

  update(data: Uint8Array): this {
    if (this.finished) {
      throw new Error("SHA-256 已结束，不能继续更新");
    }
    this.bytesHashed += data.length;
    let offset = 0;

    if (this.bufferLength > 0) {
      const needed = 64 - this.bufferLength;
      const take = Math.min(needed, data.length);
      this.buffer.set(data.subarray(0, take), this.bufferLength);
      this.bufferLength += take;
      offset += take;
      if (this.bufferLength === 64) {
        this.compress(this.buffer, 0);
        this.bufferLength = 0;
      }
    }

    while (offset + 64 <= data.length) {
      this.compress(data, offset);
      offset += 64;
    }

    if (offset < data.length) {
      this.buffer.set(data.subarray(offset), 0);
      this.bufferLength = data.length - offset;
    }
    return this;
  }

  digest(): Uint8Array {
    if (this.finished) {
      throw new Error("SHA-256 已结束");
    }
    this.finished = true;
    const bitLength = BigInt(this.bytesHashed) * 8n;
    const paddingLength =
      this.bufferLength < 56 ? 56 - this.bufferLength : 120 - this.bufferLength;
    const finalBlock = new Uint8Array(this.bufferLength + paddingLength + 8);
    finalBlock.set(this.buffer.subarray(0, this.bufferLength));
    finalBlock[this.bufferLength] = 0x80;
    for (let index = 0; index < 8; index += 1) {
      finalBlock[finalBlock.length - 1 - index] = Number(
        (bitLength >> BigInt(index * 8)) & 0xffn,
      );
    }
    for (let offset = 0; offset < finalBlock.length; offset += 64) {
      this.compress(finalBlock, offset);
    }

    const digest = new Uint8Array(32);
    for (let index = 0; index < 8; index += 1) {
      const value = this.h[index] ?? 0;
      digest[index * 4] = value >>> 24;
      digest[index * 4 + 1] = value >>> 16;
      digest[index * 4 + 2] = value >>> 8;
      digest[index * 4 + 3] = value;
    }
    return digest;
  }

  private compress(data: Uint8Array, offset: number): void {
    const w = this.schedule;
    for (let index = 0; index < 16; index += 1) {
      const base = offset + index * 4;
      w[index] =
        (((data[base] ?? 0) << 24) |
          ((data[base + 1] ?? 0) << 16) |
          ((data[base + 2] ?? 0) << 8) |
          (data[base + 3] ?? 0)) >>>
        0;
    }
    for (let index = 16; index < 64; index += 1) {
      const previous15 = w[index - 15] ?? 0;
      const previous2 = w[index - 2] ?? 0;
      const sigma0 =
        rotr(previous15, 7) ^ rotr(previous15, 18) ^ (previous15 >>> 3);
      const sigma1 =
        rotr(previous2, 17) ^ rotr(previous2, 19) ^ (previous2 >>> 10);
      w[index] =
        ((w[index - 16] ?? 0) +
          sigma0 +
          (w[index - 7] ?? 0) +
          sigma1) >>>
        0;
    }

    let a = this.h[0] ?? 0;
    let b = this.h[1] ?? 0;
    let c = this.h[2] ?? 0;
    let d = this.h[3] ?? 0;
    let e = this.h[4] ?? 0;
    let f = this.h[5] ?? 0;
    let g = this.h[6] ?? 0;
    let h = this.h[7] ?? 0;

    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temp1 =
        (h + sum1 + choice + (K[index] ?? 0) + (w[index] ?? 0)) >>> 0;
      const sum0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sum0 + majority) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    this.h[0] = ((this.h[0] ?? 0) + a) >>> 0;
    this.h[1] = ((this.h[1] ?? 0) + b) >>> 0;
    this.h[2] = ((this.h[2] ?? 0) + c) >>> 0;
    this.h[3] = ((this.h[3] ?? 0) + d) >>> 0;
    this.h[4] = ((this.h[4] ?? 0) + e) >>> 0;
    this.h[5] = ((this.h[5] ?? 0) + f) >>> 0;
    this.h[6] = ((this.h[6] ?? 0) + g) >>> 0;
    this.h[7] = ((this.h[7] ?? 0) + h) >>> 0;
  }
}

export function createSha256Hasher(): Sha256Hasher {
  return new Sha256Hasher();
}

export function sha256Hex(data: Uint8Array): string {
  return bytesToHex(createSha256Hasher().update(data).digest());
}

export function sha256HexOfChunks(chunks: Iterable<Uint8Array>): string {
  const hasher = createSha256Hasher();
  for (const chunk of chunks) {
    hasher.update(chunk);
  }
  return bytesToHex(hasher.digest());
}
