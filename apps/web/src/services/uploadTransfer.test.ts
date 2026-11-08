import { describe, expect, it, vi } from "vitest";

import {
  bytesToBase64,
  bytesToHex,
  createSha256Hasher,
  sha256Hex,
  sha256HexOfChunks,
} from "./sha256";
import {
  computePartChecksum,
  hashFileSha256,
  planFileParts,
  putUploadPart,
  signPartUpload,
  UploadTransportError,
} from "./uploadTransfer";

const encoder = new TextEncoder();

describe("流式 SHA-256", () => {
  it("匹配标准空串与 abc 向量", () => {
    expect(sha256Hex(new Uint8Array())).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(sha256Hex(encoder.encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("跨块更新与一次性结果一致", () => {
    const text =
      "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    const data = encoder.encode(text);
    const expected = sha256Hex(data);

    const hasher = createSha256Hasher();
    hasher.update(data.subarray(0, 7));
    hasher.update(data.subarray(7, 19));
    hasher.update(data.subarray(19));
    expect(sha256HexOfChunks([data.subarray(0, 7), data.subarray(7)])).toBe(
      expected,
    );
    expect(bytesToHex(hasher.digest())).toBe(expected);
  });

  it("base64 输出与对象存储校验头一致", () => {
    expect(bytesToBase64(encoder.encode("foo"))).toBe("Zm9v");
    expect(bytesToBase64(encoder.encode("fo"))).toBe("Zm8=");
    expect(bytesToBase64(encoder.encode("f"))).toBe("Zg==");
  });
});

describe("上传分片与传输", () => {
  it("按服务端分片大小规划连续分片，最后一片可更小", () => {
    const partSize = 5 * 1024 * 1024;
    const parts = planFileParts(partSize * 2 + 100, partSize);
    expect(parts).toHaveLength(3);
    expect(parts.map((part) => part.partNumber)).toEqual([1, 2, 3]);
    expect(parts[2]?.sizeBytes).toBe(100);
  });

  it("拒绝低于契约最小值的分片大小", () => {
    expect(() => planFileParts(1024, 512)).toThrow(UploadTransportError);
  });

  it("生成 API 与对象存储两种校验和表示", async () => {
    const checksum = await computePartChecksum(new Blob(["abc"]));
    expect(checksum.prefixed).toBe(
      "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(checksum.base64).toBe(
      "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
    );
  });

  it("流式计算文件 Hash 并返回契约前缀", async () => {
    await expect(hashFileSha256(new Blob(["abc"]))).resolves.toBe(
      "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("先计算分片 SHA256 再请求签名 URL，且只使用服务端返回头", async () => {
    const serverHeaders = { "Content-Length": "3" };
    const requestUrl = vi.fn(async () => ({
      part_number: 1,
      method: "PUT" as const,
      url: "https://storage.example/part-1?sig=server",
      headers: serverHeaders,
      expires_at: "2026-11-08T12:15:00Z",
    }));

    const signed = await signPartUpload(new Blob(["abc"]), requestUrl);

    expect(requestUrl).toHaveBeenCalledWith(
      "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(signed.request.headers).toEqual({ "Content-Length": "3" });
    expect(signed.request.headers).not.toHaveProperty(
      "x-amz-checksum-sha256",
    );
    expect(signed.checksum.base64).toBe(
      "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
    );
  });

  it("服务端返回校验和头时原样保留，不重算、不覆盖签名", async () => {
    const serverHeaders = {
      "x-amz-checksum-sha256": "server-bound-base64",
      "Content-Length": "3",
    };
    const signed = await signPartUpload(new Blob(["abc"]), async () => ({
      part_number: 1,
      method: "PUT",
      url: "https://storage.example/part-1",
      headers: serverHeaders,
      expires_at: "2026-11-08T12:15:00Z",
    }));

    expect(signed.request.headers).toBe(serverHeaders);
  });

  it("PUT 分片返回 ETag；非 2xx 按可重试性分类", async () => {
    const request = {
      part_number: 1,
      method: "PUT" as const,
      url: "https://storage.example/part-1",
      headers: { "x-amz-checksum-sha256": "abc" },
      expires_at: "2026-11-08T12:15:00Z",
    };
    const fetchOk = vi.fn(async () =>
      new Response("", { status: 200, headers: { ETag: '"etag-1"' } }),
    );
    await expect(
      putUploadPart(request, new Blob(["abc"]), fetchOk as typeof fetch),
    ).resolves.toBe('"etag-1"');
    expect(fetchOk).toHaveBeenCalledWith(
      request.url,
      expect.objectContaining({
        method: "PUT",
        headers: request.headers,
      }),
    );

    const fetchDenied = vi.fn(async () => new Response("denied", { status: 403 }));
    await expect(
      putUploadPart(request, new Blob(["abc"]), fetchDenied as typeof fetch),
    ).rejects.toMatchObject({ status: 403, retryable: false });

    const fetchUnavailable = vi.fn(
      async () => new Response("unavailable", { status: 503 }),
    );
    await expect(
      putUploadPart(
        request,
        new Blob(["abc"]),
        fetchUnavailable as typeof fetch,
      ),
    ).rejects.toMatchObject({ status: 503, retryable: true });

    const fetchNoEtag = vi.fn(async () => new Response("", { status: 200 }));
    await expect(
      putUploadPart(request, new Blob(["abc"]), fetchNoEtag as typeof fetch),
    ).rejects.toMatchObject({ code: "etag_missing", retryable: true });
  });
});
