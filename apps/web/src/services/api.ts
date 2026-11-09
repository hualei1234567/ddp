import type { ErrorEnvelope } from "../types/api";
import type { HealthResponse, PlatformMeta } from "../types/platform";
import { readAccessToken } from "./authToken";
import {
  parseErrorEnvelope,
  parseHealthResponse,
  parsePlatformMeta,
} from "./validation";

const DEFAULT_TIMEOUT_MS = 15_000;
const MAX_ERROR_TEXT_LENGTH = 200;

export type ApiErrorKind =
  | "aborted"
  | "network"
  | "timeout"
  | "http"
  | "invalid_response";

export interface ApiErrorOptions {
  kind: ApiErrorKind;
  message: string;
  status?: number;
  code?: string;
  requestId?: string | null;
  /** 契约错误体中的可选 details；仅用于稳定错误码的补充提示。 */
  details?: Record<string, unknown> | null;
  cause?: unknown;
}

/** 所有接口异常统一成一种类型，页面只按 kind/status 决定展示哪种状态。 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly code?: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(options: ApiErrorOptions) {
    super(
      options.message,
      options.cause === undefined ? undefined : { cause: options.cause },
    );
    this.name = "ApiError";
    this.kind = options.kind;
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId ?? null;
    this.details = options.details ?? null;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isGone(): boolean {
    return this.status === 410;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions<T> {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** 响应结构校验函数；外部数据进入 UI 前必须提供。 */
  validate?: (value: unknown) => T;
}

export interface ApiClientConfig {
  baseUrl?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) {
    return "";
  }
  const params = new URLSearchParams();
  const entries = Object.entries(query)
    .filter(([, value]) => value !== undefined && value !== null)
    .sort(([left], [right]) => left.localeCompare(right));

  for (const [key, value] of entries) {
    params.append(key, String(value));
  }

  const text = params.toString();
  return text ? `?${text}` : "";
}

function buildUrl(
  baseUrl: string,
  path: string,
  query?: Record<string, QueryValue>,
): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${baseUrl}${normalizedPath}${buildQuery(query)}`;
}

function createRequestId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `req-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 10)}`;
}

function isRawBody(value: unknown): value is BodyInit {
  return (
    typeof value === "string" ||
    (typeof FormData !== "undefined" && value instanceof FormData) ||
    (typeof Blob !== "undefined" && value instanceof Blob) ||
    (typeof URLSearchParams !== "undefined" &&
      value instanceof URLSearchParams) ||
    value instanceof ArrayBuffer
  );
}

function isAbortError(value: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      value instanceof DOMException &&
      value.name === "AbortError") ||
    (value instanceof Error && value.name === "AbortError")
  );
}

function toTransportError(caught: unknown, timedOut: boolean): ApiError {
  if (timedOut) {
    return new ApiError({
      kind: "timeout",
      message: "请求超时，请检查网络后重试。",
      cause: caught,
    });
  }
  if (isAbortError(caught)) {
    return new ApiError({
      kind: "aborted",
      message: "请求已取消。",
      cause: caught,
    });
  }
  return new ApiError({
    kind: "network",
    message: "无法连接服务，请检查网络或后端状态。",
    cause: caught,
  });
}

function toHttpError(
  response: Response,
  rawText: string,
  requestId: string,
): ApiError {
  let envelope: ErrorEnvelope | null = null;
  const trimmed = rawText.trim();
  if (trimmed !== "") {
    try {
      envelope = parseErrorEnvelope(JSON.parse(trimmed));
    } catch {
      envelope = null;
    }
  }

  const fallbackMessage = trimmed.slice(0, MAX_ERROR_TEXT_LENGTH);
  return new ApiError({
    kind: "http",
    status: response.status,
    code: envelope?.error.code ?? `http_${response.status}`,
    requestId: envelope?.error.request_id ?? requestId,
    details: envelope?.error.details ?? null,
    message:
      envelope?.error.message ||
      fallbackMessage ||
      `服务返回 HTTP ${response.status}`,
  });
}

async function parseResponse<T>(
  response: Response,
  validate: ((value: unknown) => T) | undefined,
  fallbackRequestId: string,
): Promise<T> {
  const requestId =
    response.headers.get("X-Request-Id") ?? fallbackRequestId;
  const rawText = await response.text();

  if (!response.ok) {
    throw toHttpError(response, rawText, requestId);
  }

  if (response.status === 204 || rawText.trim() === "") {
    return undefined as T;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawText);
  } catch (cause) {
    throw new ApiError({
      kind: "invalid_response",
      status: response.status,
      requestId,
      message: "服务返回了无法解析的 JSON。",
      cause,
    });
  }

  if (!validate) {
    return parsed as T;
  }

  try {
    return validate(parsed);
  } catch (cause) {
    throw new ApiError({
      kind: "invalid_response",
      status: response.status,
      requestId,
      message: "服务返回的数据结构不符合契约。",
      cause,
    });
  }
}

/**
 * 统一 API client：负责 URL/查询参数、超时、取消、request_id、错误体解析
 * 与响应结构校验。页面不得自行调用 fetch。
 */
export class ApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(config: ApiClientConfig = {}) {
    this.baseUrl = (
      config.baseUrl ??
      import.meta.env.VITE_API_BASE ??
      ""
    ).replace(/\/+$/, "");
    this.timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl =
      config.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  }

  async request<T>(path: string, options: RequestOptions<T> = {}): Promise<T> {
    const requestId = createRequestId();
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Request-Id": requestId,
      ...options.headers,
    };
    const accessToken = readAccessToken();
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`;
    }

    let payload: BodyInit | undefined;
    let isJsonBody = false;
    if (options.body !== undefined) {
      if (isRawBody(options.body)) {
        payload = options.body;
      } else {
        payload = JSON.stringify(options.body);
        isJsonBody = true;
      }
    }
    if (isJsonBody) {
      headers["Content-Type"] = "application/json";
    }

    const timeoutMs = options.timeoutMs ?? this.timeoutMs;
    const controller = new AbortController();
    let timedOut = false;
    const timer =
      Number.isFinite(timeoutMs) && timeoutMs > 0
        ? setTimeout(() => {
            timedOut = true;
            controller.abort();
          }, timeoutMs)
        : null;

    const externalSignal = options.signal;
    const onExternalAbort = () => controller.abort();

    if (externalSignal) {
      if (externalSignal.aborted) {
        if (timer) {
          clearTimeout(timer);
        }
        throw new ApiError({ kind: "aborted", message: "请求已取消。" });
      }
      externalSignal.addEventListener("abort", onExternalAbort, {
        once: true,
      });
    }

    try {
      let response: Response;
      try {
        response = await this.fetchImpl(
          buildUrl(this.baseUrl, path, options.query),
          {
            method: options.method ?? "GET",
            headers,
            body: payload,
            signal: controller.signal,
          },
        );
      } catch (caught) {
        throw toTransportError(caught, timedOut);
      }

      return await parseResponse(response, options.validate, requestId);
    } finally {
      if (timer) {
        clearTimeout(timer);
      }
      externalSignal?.removeEventListener("abort", onExternalAbort);
    }
  }

  get<T>(
    path: string,
    options: Omit<RequestOptions<T>, "method" | "body"> = {},
  ): Promise<T> {
    return this.request<T>(path, { ...options, method: "GET" });
  }
}

export const apiClient = new ApiClient();

/** 已接入契约的端点集中在这里，页面只依赖语义化方法。 */
export const api = {
  health: (
    options: Pick<RequestOptions<HealthResponse>, "signal"> = {},
  ): Promise<HealthResponse> =>
    apiClient.get("/health", { ...options, validate: parseHealthResponse }),
  meta: (
    options: Pick<RequestOptions<PlatformMeta>, "signal"> = {},
  ): Promise<PlatformMeta> =>
    apiClient.get("/api/v1/platform/meta", {
      ...options,
      validate: parsePlatformMeta,
    }),
};
