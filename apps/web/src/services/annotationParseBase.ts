/**
 * 标注结果解析的共享基座（拆分自 annotationResultParsers.ts，原 840 行）。
 *
 * 只放**被两个以上业务簇复用**的读取工具：
 * - `readJsonValue` / `readOptionalBooleanValue` / `readArray`：基础读取
 * - `parseLabelValue` / `parseEvidenceRef`：记录与复核都要用的子结构
 */

import type { LabelValue, EvidenceRef } from "../types/annotation";
import {
  LABEL_VALUE_SOURCE_VALUES,
  LABEL_VALUE_STATUS_VALUES,
} from "../types/annotation";
import {
  asRecord,
  ContractValidationError,
  readBoolean,
  readEnum,
  readNumber,
  readOptionalString,
  readSha256Checksum,
  readString,
} from "./validation";

export function readJsonValue(
  record: Record<string, unknown>,
  key: string,
  path: string,
): unknown {
  if (!(key in record) || record[key] === undefined) {
    throw new ContractValidationError(`${path} 缺失`);
  }
  return record[key];
}

export function readOptionalBooleanValue(
  record: Record<string, unknown>,
  key: string,
  path: string,
): boolean | undefined {
  if (record[key] === undefined || record[key] === null) {
    return undefined;
  }
  return readBoolean(record, key, path);
}

export function readArray<T>(
  record: Record<string, unknown>,
  key: string,
  path: string,
  parseItem: (value: unknown, itemPath: string) => T,
): T[] {
  const value = record[key] ?? [];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value.map((item, index) => parseItem(item, `${path}[${index}]`));
}

export function parseLabelValue(value: unknown, path: string): LabelValue {
  const record = asRecord(value, path);
  const parsed: LabelValue = {
    label_key: readString(record, "label_key", `${path}.label_key`),
    value: readJsonValue(record, "value", `${path}.value`),
    source: readEnum(
      record,
      "source",
      `${path}.source`,
      LABEL_VALUE_SOURCE_VALUES,
    ),
    label_status: readEnum(
      record,
      "label_status",
      `${path}.label_status`,
      LABEL_VALUE_STATUS_VALUES,
    ),
  };
  if (record.confidence !== undefined && record.confidence !== null) {
    const confidence = readNumber(record, "confidence", `${path}.confidence`);
    if (confidence < 0 || confidence > 1) {
      throw new ContractValidationError(`${path}.confidence 必须在 0 到 1 之间`);
    }
    parsed.confidence = confidence;
  }
  if (record.comment !== undefined) {
    parsed.comment = readOptionalString(record, "comment", `${path}.comment`);
  }
  return parsed;
}

export function parseEvidenceRef(value: unknown, path: string): EvidenceRef {
  const record = asRecord(value, path);
  const parsed: EvidenceRef = {
    evidence_id: readString(record, "evidence_id", `${path}.evidence_id`),
    object_key: readString(record, "object_key", `${path}.object_key`),
    sha256: readSha256Checksum(record, "sha256", `${path}.sha256`),
  };
  if (record.content_type !== undefined) {
    parsed.content_type = readOptionalString(
      record,
      "content_type",
      `${path}.content_type`,
    );
  }
  return parsed;
}
