import type { CursorPage } from "../types/api";
import type { CaptureSession, CaptureSessionAction, CaptureSessionArtifact, CaptureSessionCreateInput } from "../types/captureSession";
import { apiClient, type ApiClient } from "./api";
import { parseCaptureSession, parseCaptureSessionArtifactPage, parseCaptureSessionPage } from "./captureSessionParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import { assertPageScope, assertProjectScope, pageQuery, projectPath, requireIdentifier, type PageRequestOptions } from "./projectScope";

export interface CaptureSessionApi {
  list(projectId: string, options?: PageRequestOptions): Promise<CursorPage<CaptureSession>>;
  create(projectId: string, input: CaptureSessionCreateInput, options?: { idempotencyKey?: string; signal?: AbortSignal }): Promise<CaptureSession>;
  get(projectId: string, sessionId: string, options?: { signal?: AbortSignal }): Promise<CaptureSession>;
  action(projectId: string, sessionId: string, action: CaptureSessionAction, reason: string | null, options: { version: number; idempotencyKey?: string; signal?: AbortSignal }): Promise<CaptureSession>;
  listArtifacts(projectId: string, sessionId: string, options?: PageRequestOptions): Promise<CursorPage<CaptureSessionArtifact>>;
}

export function createCaptureSessionApi(client: ApiClient): CaptureSessionApi {
  const path = (projectId: string, suffix = "") => projectPath(projectId, `/capture-sessions${suffix}`);
  const detail = (projectId: string, sessionId: string) => path(projectId, `/${requireIdentifier(sessionId, "session_id")}`);
  return {
    list: (projectId, options) => client.get(path(projectId), { signal: options?.signal, query: pageQuery(options), validate: (value) => assertPageScope(parseCaptureSessionPage(value), projectId, "采集会话列表") }),
    create: (projectId, input, options) => client.request(path(projectId), {
      method: "POST", signal: options?.signal,
      headers: { "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey() }, body: input,
      validate: (value) => assertProjectScope(parseCaptureSession(value), projectId, "采集会话"),
    }),
    get: (projectId, sessionId, options) => client.get(detail(projectId, sessionId), { signal: options?.signal, validate: (value) => assertProjectScope(parseCaptureSession(value), projectId, "采集会话") }),
    action: (projectId, sessionId, action, reason, options) => client.request(`${detail(projectId, sessionId)}/actions/${action}`, {
      method: "POST", signal: options.signal,
      headers: { "If-Match": formatIfMatch(options.version), "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey() },
      body: reason === null ? {} : { reason },
      validate: (value) => assertProjectScope(parseCaptureSession(value), projectId, "采集会话"),
    }),
    listArtifacts: (projectId, sessionId, options) => client.get(`${detail(projectId, sessionId)}/artifacts`, { signal: options?.signal, query: pageQuery(options), validate: parseCaptureSessionArtifactPage }),
  };
}

export const captureSessionApi = createCaptureSessionApi(apiClient);
