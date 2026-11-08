import type {
  CompleteUploadInput,
  CompleteUploadResponse,
  UploadPartAckRequest,
  UploadPartAckResponse,
  UploadPartList,
  UploadPartUrlResponse,
  UploadSession,
  UploadSessionCreateInput,
} from "../types/collection";
import { apiClient, type ApiClient } from "./api";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertProjectScope,
  projectPath,
  requireIdentifier,
} from "./projectScope";
import {
  parseCompleteUploadResponse,
  parseUploadPartAck,
  parseUploadPartList,
  parseUploadPartUrl,
  parseUploadSession,
} from "./uploadParsers";

/** 与服务端 `UploadPartAckRequest` 一致。 */
export type UploadPartAckInput = UploadPartAckRequest;

export interface UploadWriteOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface UploadCompleteOptions extends UploadWriteOptions {
  version: number;
}

export interface UploadAbortOptions extends UploadCompleteOptions {
  reason?: string | null;
}

/**
 * 分片 URL 请求：可选提交分片 SHA256，用于服务端把校验和绑定进
 * 预签名（响应 headers 会带回 x-amz-checksum-sha256）。
 */
export interface UploadPartUrlOptions {
  signal?: AbortSignal;
  /** 契约格式 `sha256:<64hex>`。 */
  sha256?: string;
}

export interface UploadsApi {
  createSession: (
    projectId: string,
    input: UploadSessionCreateInput,
    options?: UploadWriteOptions,
  ) => Promise<UploadSession>;
  getSession: (
    projectId: string,
    uploadSessionId: string,
    options?: Pick<UploadWriteOptions, "signal">,
  ) => Promise<UploadSession>;
  createPartUrl: (
    projectId: string,
    uploadSessionId: string,
    partNumber: number,
    options?: UploadPartUrlOptions,
  ) => Promise<UploadPartUrlResponse>;
  ackPart: (
    projectId: string,
    uploadSessionId: string,
    partNumber: number,
    input: UploadPartAckInput,
    options?: Pick<UploadWriteOptions, "signal">,
  ) => Promise<UploadPartAckResponse>;
  listParts: (
    projectId: string,
    uploadSessionId: string,
    options?: Pick<UploadWriteOptions, "signal">,
  ) => Promise<UploadPartList>;
  complete: (
    projectId: string,
    uploadSessionId: string,
    input: CompleteUploadInput,
    options: UploadCompleteOptions,
  ) => Promise<CompleteUploadResponse>;
  abort: (
    projectId: string,
    uploadSessionId: string,
    options: UploadAbortOptions,
  ) => Promise<UploadSession>;
}

function sessionPath(projectId: string, uploadSessionId: string): string {
  return projectPath(
    projectId,
    `/upload-sessions/${requireIdentifier(uploadSessionId, "upload_session_id")}`,
  );
}

function writeHeaders(
  options: UploadWriteOptions,
  version?: number,
): Record<string, string> {
  const headers: Record<string, string> = {
    "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
  };
  if (version !== undefined) {
    headers["If-Match"] = formatIfMatch(version);
  }
  return headers;
}

/**
 * 阶段 2 上传会话 API（DRAFT）。
 * 分片字节直接 PUT 到对象存储的预签名 URL，不经过统一 API client；
 * 这里只负责会话、分片确认与完成，不接触对象存储长期凭据。
 */
export function createUploadsApi(client: ApiClient): UploadsApi {
  return {
    async createSession(projectId, input, options = {}) {
      return client.request(projectPath(projectId, "/upload-sessions"), {
        method: "POST",
        signal: options.signal,
        headers: writeHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseUploadSession(value), projectId, "上传会话"),
      });
    },

    async getSession(projectId, uploadSessionId, options = {}) {
      return client.get(sessionPath(projectId, uploadSessionId), {
        signal: options.signal,
        validate: (value) =>
          assertProjectScope(parseUploadSession(value), projectId, "上传会话"),
      });
    },

    async createPartUrl(projectId, uploadSessionId, partNumber, options = {}) {
      return client.request(
        `${sessionPath(projectId, uploadSessionId)}/parts/${partNumber}/url`,
        {
          method: "POST",
          signal: options.signal,
          // 旧服务端/旧会话仍兼容无请求体；客户端有校验和时显式绑定。
          body:
            options.sha256 === undefined
              ? undefined
              : { sha256: options.sha256 },
          validate: (value) => parseUploadPartUrl(value),
        },
      );
    },

    async ackPart(
      projectId,
      uploadSessionId,
      partNumber,
      input,
      options = {},
    ) {
      return client.request(
        `${sessionPath(projectId, uploadSessionId)}/parts/${partNumber}/ack`,
        {
          method: "POST",
          signal: options.signal,
          body: input,
          validate: (value) => parseUploadPartAck(value),
        },
      );
    },

    async listParts(projectId, uploadSessionId, options = {}) {
      return client.get(`${sessionPath(projectId, uploadSessionId)}/parts`, {
        signal: options.signal,
        validate: (value) => parseUploadPartList(value),
      });
    },

    async complete(projectId, uploadSessionId, input, options) {
      return client.request(
        `${sessionPath(projectId, uploadSessionId)}/complete`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options, options.version),
          body: input,
          validate: (value) => parseCompleteUploadResponse(value),
        },
      );
    },

    async abort(projectId, uploadSessionId, options) {
      return client.request(
        `${sessionPath(projectId, uploadSessionId)}/abort`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options, options.version),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseUploadSession(value),
              projectId,
              "上传会话",
            ),
        },
      );
    },
  };
}

export const uploadsApi = createUploadsApi(apiClient);
