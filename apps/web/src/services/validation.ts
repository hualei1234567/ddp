import type { ErrorEnvelope } from "../types/api";
import type { HealthResponse, PlatformMeta } from "../types/platform";

/** 外部响应进入 UI 前必须经过校验；校验失败由 API client 统一转为 ApiError。 */
export class ContractValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContractValidationError";
  }
}

export function asRecord(
  value: unknown,
  path: string,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

export function readString(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string {
  const value = record[key];
  if (typeof value !== "string") {
    throw new ContractValidationError(`${path} 必须是字符串`);
  }
  return value;
}

export function readOptionalString(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string") {
    throw new ContractValidationError(`${path} 必须是字符串或 null`);
  }
  return value;
}

export function readNumber(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ContractValidationError(`${path} 必须是有限数字`);
  }
  return value;
}

export function readOptionalNumber(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ContractValidationError(`${path} 必须是有限数字或 null`);
  }
  return value;
}

export function readInteger(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number {
  const value = readNumber(record, key, path);
  if (!Number.isInteger(value) || value < 0) {
    throw new ContractValidationError(`${path} 必须是非负整数`);
  }
  return value;
}

export function readStringArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value.map((item, index) => {
    if (typeof item !== "string") {
      throw new ContractValidationError(`${path}[${index}] 必须是字符串`);
    }
    return item;
  });
}

export function readOptionalStringArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string[] | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readStringArray(record, key, path);
}

export function readOptionalRecord(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown> | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  return asRecord(value, path);
}

export function parseHealthResponse(value: unknown): HealthResponse {
  const record = asRecord(value, "健康检查响应");
  return {
    status: readString(record, "status", "status"),
    service: readString(record, "service", "service"),
    version: readString(record, "version", "version"),
    env: readString(record, "env", "env"),
  };
}

export function parsePlatformMeta(value: unknown): PlatformMeta {
  const record = asRecord(value, "平台元信息响应");
  const rawModules = record.modules;
  if (!Array.isArray(rawModules)) {
    throw new ContractValidationError("modules 必须是数组");
  }
  const modules = rawModules.map((item, index) => {
    if (typeof item !== "string") {
      throw new ContractValidationError(`modules[${index}] 必须是字符串`);
    }
    return item;
  });

  return {
    name: readString(record, "name", "name"),
    version: readString(record, "version", "version"),
    env: readString(record, "env", "env"),
    phase: readString(record, "phase", "phase"),
    modules,
  };
}

/** 错误体不是契约结构时返回 null，由调用方使用 HTTP 兜底文案。 */
export function parseErrorEnvelope(value: unknown): ErrorEnvelope | null {
  try {
    const record = asRecord(value, "错误响应");
    const error = asRecord(record.error, "error");
    const code = readString(error, "code", "error.code");
    const message = readString(error, "message", "error.message");
    const rawRequestId = error.request_id;
    if (
      rawRequestId !== undefined &&
      rawRequestId !== null &&
      typeof rawRequestId !== "string"
    ) {
      throw new ContractValidationError("error.request_id 必须是字符串或 null");
    }

    return {
      error: {
        code,
        message,
        request_id: rawRequestId ?? null,
      },
    };
  } catch {
    return null;
  }
}
