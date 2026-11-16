/**
 * 标注提交质量检查（N4）的响应解析。
 *
 * 三态 `passed` 是本模块唯一的硬要求：解析层必须把 `null` 原样保留，
 * 任何「缺字段当作通过」「缺字段当作无结论」的兜底都会让 UI 说谎。
 * 契约把 `passed` 声明为必填的 `boolean | null`，因此缺失即契约违约，
 * 直接抛错由 API client 统一转成 `invalid_response`。
 */

import type { CursorPage } from "../types/api";
import {
  QUALITY_CHECK_STATUS_VALUES,
  QUALITY_RULE_SEVERITY_VALUES,
  type QualityCheckRun,
  type QualityFinding,
} from "../types/quality";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readInteger,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

/**
 * 读取三态布尔：`true` / `false` / `null` 三种取值都必须显式出现。
 *
 * 不用 `readOptionalBoolean`：那个工具把「缺失」和「null」都收敛成 `null`，
 * 在这里会把契约违约悄悄变成「无结论」——方向安全的兜底应当是报错而不是猜测。
 */
function readTriStateBoolean(
  record: Record<string, unknown>,
  key: string,
  path: string,
): boolean | null {
  if (!(key in record) || record[key] === undefined) {
    throw new ContractValidationError(`${path} 缺失（三态判定不得缺省）`);
  }
  const value = record[key];
  if (value === null) {
    return null;
  }
  if (typeof value !== "boolean") {
    throw new ContractValidationError(`${path} 必须是布尔值或 null`);
  }
  return value;
}

export function parseQualityFinding(
  value: unknown,
  path = "质量检查结果项",
): QualityFinding {
  const record = asRecord(value, path);
  const severity = readString(record, "severity", `${path}.severity`);
  const matchedSeverity = QUALITY_RULE_SEVERITY_VALUES.find(
    (item) => item === severity,
  );
  if (matchedSeverity === undefined) {
    throw new ContractValidationError(
      `${path}.severity 必须是 ${QUALITY_RULE_SEVERITY_VALUES.join(" / ")}`,
    );
  }
  return {
    annotation_id: readUuid(
      record,
      "annotation_id",
      `${path}.annotation_id`,
    ),
    rule_id: readString(record, "rule_id", `${path}.rule_id`),
    rule_version: readPositiveInteger(
      record,
      "rule_version",
      `${path}.rule_version`,
    ),
    severity: matchedSeverity,
    passed: readTriStateBoolean(record, "passed", `${path}.passed`),
    message: readString(record, "message", `${path}.message`),
  };
}

export function parseQualityCheckRun(
  value: unknown,
  path = "质量检查",
): QualityCheckRun {
  const record = asRecord(value, path);
  const status = readString(record, "status", `${path}.status`);
  const matchedStatus = QUALITY_CHECK_STATUS_VALUES.find(
    (item) => item === status,
  );
  if (matchedStatus === undefined) {
    throw new ContractValidationError(
      `${path}.status 必须是 ${QUALITY_CHECK_STATUS_VALUES.join(" / ")}`,
    );
  }

  const rawFindings = record.findings;
  if (!Array.isArray(rawFindings)) {
    throw new ContractValidationError(`${path}.findings 必须是数组`);
  }

  return {
    run_id: readUuid(record, "run_id", `${path}.run_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    rule_set_version: readString(
      record,
      "rule_set_version",
      `${path}.rule_set_version`,
    ),
    status: matchedStatus,
    error_count: readInteger(record, "error_count", `${path}.error_count`),
    warn_count: readInteger(record, "warn_count", `${path}.warn_count`),
    started_at: readString(record, "started_at", `${path}.started_at`),
    finished_at: readString(record, "finished_at", `${path}.finished_at`),
    findings: rawFindings.map((item, index) =>
      parseQualityFinding(item, `${path}.findings[${index}]`),
    ),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    // 契约必填而后端未返回，按可选读取：缺失不视为错误。
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseQualityCheckRunPage(
  value: unknown,
): CursorPage<QualityCheckRun> {
  return parseCursorPage(value, "质量检查历史", parseQualityCheckRun);
}
