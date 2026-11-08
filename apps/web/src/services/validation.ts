import type { CursorPage, ErrorEnvelope } from "../types/api";
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

/** 标准 8-4-4-4-12 十六进制 UUID；不限制版本，兼容服务端未来升级。 */
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 后端按 `sha256:<64 位小写十六进制>` 下发标定校验和。 */
const SHA256_CHECKSUM_PATTERN = /^sha256:[0-9a-f]{64}$/;

export function readUuid(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string {
  const value = readString(record, key, path);
  if (!UUID_PATTERN.test(value)) {
    throw new ContractValidationError(`${path} 必须是 UUID`);
  }
  return value;
}

export function readOptionalUuid(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readUuid(record, key, path);
}

export function readUuidArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string[] {
  const values = readStringArray(record, key, path);
  values.forEach((value, index) => {
    if (!UUID_PATTERN.test(value)) {
      throw new ContractValidationError(`${path}[${index}] 必须是 UUID`);
    }
  });
  return values;
}

export function readEnum<T extends string>(
  record: Record<string, unknown>,
  key: string,
  path: string,
  allowed: readonly T[],
): T {
  const value = readString(record, key, path);
  const matched = allowed.find((item) => item === value);
  if (matched === undefined) {
    throw new ContractValidationError(
      `${path} 必须是以下枚举之一：${allowed.join(" / ")}`,
    );
  }
  return matched;
}

export function readSha256Checksum(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string {
  const value = readString(record, key, path);
  if (!SHA256_CHECKSUM_PATTERN.test(value)) {
    throw new ContractValidationError(
      `${path} 必须是 sha256:<64 位小写十六进制>`,
    );
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

/** 契约中的 version / sequence_no / 分片号等必须为正整数。 */
export function readPositiveInteger(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number {
  const value = readInteger(record, key, path);
  if (value < 1) {
    throw new ContractValidationError(`${path} 必须是正整数`);
  }
  return value;
}

export function readBoolean(
  record: Record<string, unknown>,
  key: string,
  path: string,
): boolean {
  const value = record[key];
  if (typeof value !== "boolean") {
    throw new ContractValidationError(`${path} 必须是布尔值`);
  }
  return value;
}

export function readOptionalBoolean(
  record: Record<string, unknown>,
  key: string,
  path: string,
): boolean | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "boolean") {
    throw new ContractValidationError(`${path} 必须是布尔值或 null`);
  }
  return value;
}

export function readIntegerArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value.map((item, index) => {
    if (typeof item !== "number" || !Number.isInteger(item) || item < 1) {
      throw new ContractValidationError(`${path}[${index}] 必须是正整数`);
    }
    return item;
  });
}

export function readOptionalIntegerArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number[] {
  if (record[key] === undefined || record[key] === null) {
    return [];
  }
  return readIntegerArray(record, key, path);
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

/** 阶段 0/1/2 共用的游标分页解析；列表条目仍由调用方逐项校验。 */
export function parseCursorPage<T>(
  value: unknown,
  path: string,
  parseItem: (item: unknown, itemPath: string) => T,
): CursorPage<T> {
  const record = asRecord(value, path);
  const rawItems = record.items;
  if (!Array.isArray(rawItems)) {
    throw new ContractValidationError(`${path}.items 必须是数组`);
  }
  const items = rawItems.map((item, index) =>
    parseItem(item, `${path}.items[${index}]`),
  );
  const rawNextCursor = record.next_cursor;
  if (
    rawNextCursor !== undefined &&
    rawNextCursor !== null &&
    typeof rawNextCursor !== "string"
  ) {
    throw new ContractValidationError(
      `${path}.next_cursor 必须是字符串或 null`,
    );
  }
  return { items, next_cursor: rawNextCursor ?? null };
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
    const rawDetails = error.details;
    const parsed: ErrorEnvelope = {
      error: {
        code,
        message,
        request_id: rawRequestId ?? null,
      },
    };
    if (rawDetails !== undefined && rawDetails !== null) {
      parsed.error.details = asRecord(rawDetails, "error.details");
    }
    return parsed;
  } catch {
    return null;
  }
}
