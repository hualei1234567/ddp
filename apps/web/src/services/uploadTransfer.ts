import type { UploadPartUrlResponse } from "../types/collection";
import {
  bytesToBase64,
  bytesToHex,
  createSha256Hasher,
} from "./sha256";

/** 单分片读取块；与对象存储分片大小无关，只影响本地 Hash 内存占用。 */
const HASH_READ_CHUNK_BYTES = 4 * 1024 * 1024;

/** 契约约束：除最后一片外，单分片最小 5 MiB。 */
const MIN_PART_SIZE_BYTES = 5 * 1024 * 1024;
const MAX_PARTS = 10_000;

export interface FilePart {
  partNumber: number;
  start: number;
  end: number;
  sizeBytes: number;
}

export class UploadTransportError extends Error {
  readonly status: number | null;
  readonly retryable: boolean;
  readonly code: string;

  constructor(
    message: string,
    options: {
      status?: number | null;
      retryable: boolean;
      code: string;
      cause?: unknown;
    },
  ) {
    super(
      message,
      options.cause === undefined ? undefined : { cause: options.cause },
    );
    this.name = "UploadTransportError";
    this.status = options.status ?? null;
    this.retryable = options.retryable;
    this.code = options.code;
  }
}

/** 按会话下发的分片大小规划分片；不跳号，最后一片可小于最小分片。 */
export function planFileParts(
  sizeBytes: number,
  partSizeBytes: number,
): FilePart[] {
  if (!Number.isInteger(sizeBytes) || sizeBytes < 1) {
    throw new UploadTransportError("文件大小必须为正整数", {
      retryable: false,
      code: "file_empty",
    });
  }
  if (!Number.isInteger(partSizeBytes) || partSizeBytes < 1) {
    throw new UploadTransportError("分片大小必须为正整数", {
      retryable: false,
      code: "part_size_invalid",
    });
  }
  if (partSizeBytes < MIN_PART_SIZE_BYTES && sizeBytes > partSizeBytes) {
    throw new UploadTransportError(
      "服务端下发的分片大小低于契约最小值（5 MiB），请刷新会话后重试",
      { retryable: false, code: "part_size_invalid" },
    );
  }
  const count = Math.ceil(sizeBytes / partSizeBytes);
  if (count > MAX_PARTS) {
    throw new UploadTransportError("分片数量超过契约上限（10000）", {
      retryable: false,
      code: "part_count_exceeded",
    });
  }
  const parts: FilePart[] = [];
  for (let index = 0; index < count; index += 1) {
    const start = index * partSizeBytes;
    const end = Math.min(sizeBytes, start + partSizeBytes);
    parts.push({
      partNumber: index + 1,
      start,
      end,
      sizeBytes: end - start,
    });
  }
  return parts;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new UploadTransportError("上传已取消", {
      retryable: false,
      code: "aborted",
    });
  }
}

/** 流式计算整文件 SHA256，返回契约表示 `sha256:<64hex>`。 */
export async function hashFileSha256(
  file: Blob,
  options: {
    signal?: AbortSignal;
    onProgress?: (hashedBytes: number, totalBytes: number) => void;
  } = {},
): Promise<string> {
  const hasher = createSha256Hasher();
  let offset = 0;
  while (offset < file.size) {
    throwIfAborted(options.signal);
    const end = Math.min(file.size, offset + HASH_READ_CHUNK_BYTES);
    const buffer = await file.slice(offset, end).arrayBuffer();
    hasher.update(new Uint8Array(buffer));
    offset = end;
    options.onProgress?.(offset, file.size);
  }
  return `sha256:${bytesToHex(hasher.digest())}`;
}

export interface PartChecksum {
  /** API 字段格式：`sha256:<64hex>`。 */
  prefixed: string;
  /** 对象存储 `x-amz-checksum-sha256` 要求 base64。 */
  base64: string;
}

export async function computePartChecksum(blob: Blob): Promise<PartChecksum> {
  const buffer = await blob.arrayBuffer();
  const digest = createSha256Hasher()
    .update(new Uint8Array(buffer))
    .digest();
  return {
    prefixed: `sha256:${bytesToHex(digest)}`,
    base64: bytesToBase64(digest),
  };
}

export interface SignedPartUpload {
  checksum: PartChecksum;
  /** 服务端签发的请求；headers 必须原样用于 PUT，不得自行增补。 */
  request: UploadPartUrlResponse;
}

/**
 * 先计算分片 SHA256，再带着该校验和向服务端申请预签名 URL。
 *
 * 服务端可能把校验和绑定进签名并在 `headers` 中返回校验头；客户端只使用
 * 服务端返回的头。若服务端未返回校验和头，本次 PUT 不带该校验头，分片
 * SHA256 仍通过后续 ack 提交，保持旧服务端兼容。
 */
export async function signPartUpload(
  blob: Blob,
  requestUrl: (sha256: string) => Promise<UploadPartUrlResponse>,
  signal?: AbortSignal,
): Promise<SignedPartUpload> {
  throwIfAborted(signal);
  const checksum = await computePartChecksum(blob);
  throwIfAborted(signal);
  const request = await requestUrl(checksum.prefixed);
  throwIfAborted(signal);
  return { checksum, request };
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 429 || status >= 500;
}

/**
 * 将分片字节直接 PUT 到对象存储预签名 URL。
 * 该请求不携带 API Token，也不经过统一 API client；URL 与必填头由服务端签发。
 */
export async function putUploadPart(
  request: UploadPartUrlResponse,
  body: Blob,
  fetchImpl: typeof fetch = globalThis.fetch,
  signal?: AbortSignal,
): Promise<string> {
  throwIfAborted(signal);
  let response: Response;
  try {
    response = await fetchImpl(request.url, {
      method: request.method,
      headers: request.headers,
      body,
      signal,
    });
  } catch (caught) {
    if (signal?.aborted) {
      throw new UploadTransportError("上传已取消", {
        retryable: false,
        code: "aborted",
        cause: caught,
      });
    }
    throw new UploadTransportError("分片上传网络中断，可重试", {
      retryable: true,
      code: "network",
      cause: caught,
    });
  }

  if (!response.ok) {
    const retryable = isRetryableStatus(response.status);
    await response.text().catch(() => "");
    throw new UploadTransportError(
      retryable
        ? `对象存储暂时不可用（HTTP ${response.status}），可重试`
        : `对象存储拒绝分片（HTTP ${response.status}）`,
      {
        status: response.status,
        retryable,
        code: retryable ? "object_storage_unavailable" : "part_upload_denied",
      },
    );
  }

  const etag = response.headers.get("ETag") ?? response.headers.get("etag");
  if (!etag) {
    throw new UploadTransportError(
      "对象存储未返回 ETag，无法确认分片；请刷新会话后重试",
      { status: response.status, retryable: true, code: "etag_missing" },
    );
  }
  await response.text().catch(() => "");
  return etag;
}
