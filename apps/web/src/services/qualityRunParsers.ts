/**
 * N4-004 统一质量运行的响应解析（六类分层）。
 *
 * 解析层是"不让页面说谎"的最后一道闸门：
 * - 六类必须齐全且顺序固定，缺一类就报错——否则 UI 会把缺失类别当作通过；
 * - `status` / `severity` / `category` 使用封闭枚举，未知值直接拒绝；
 * - `score` 允许为 null（没有可判定结论），但出现时必须落在 0..1。
 *
 * 三态兜底一律采用"报错而不是猜测"，与 `qualityCheckParsers.ts` 同口径。
 */

import type { CursorPage } from "../types/api";
import {
  QUALITY_CATEGORY_STATUS_VALUES,
  QUALITY_CATEGORY_VALUES,
  QUALITY_FINDING_STATUS_VALUES,
  QUALITY_RUN_STATUS_VALUES,
  QUALITY_SEVERITY_VALUES,
  QUALITY_SUBJECT_TYPE_VALUES,
  type QualityCategorySummary,
  type QualityRun,
  type QualityRunFinding,
} from "../types/qualityRun";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readEnum,
  readInteger,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readString,
  readUuid,
} from "./validation";

/** 契约 `rule_set_version` 的 pattern；与后端内容 SHA 同源。 */
const RULE_SET_VERSION_PATTERN = /^sha256:[0-9a-f]{64}$/;

function readUnitScore(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  const value = readOptionalNumber(record, key, path);
  if (value !== null && (value < 0 || value > 1)) {
    throw new ContractValidationError(`${path} 必须落在 0..1 或为 null`);
  }
  return value;
}

export function parseQualityCategorySummary(
  value: unknown,
  path = "质量类别聚合",
): QualityCategorySummary {
  const record = asRecord(value, path);
  return {
    category: readEnum(
      record,
      "category",
      `${path}.category`,
      QUALITY_CATEGORY_VALUES,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      QUALITY_CATEGORY_STATUS_VALUES,
    ),
    finding_count: readInteger(
      record,
      "finding_count",
      `${path}.finding_count`,
    ),
    pass_count: readInteger(record, "pass_count", `${path}.pass_count`),
    fail_count: readInteger(record, "fail_count", `${path}.fail_count`),
    not_applicable_count: readInteger(
      record,
      "not_applicable_count",
      `${path}.not_applicable_count`,
    ),
    error_count: readInteger(record, "error_count", `${path}.error_count`),
    score: readUnitScore(record, "score", `${path}.score`),
  };
}

/**
 * 六类必须齐全、顺序固定（契约明文要求）。
 *
 * 不按"缺哪类补哪类"兜底：补齐出来的 `PASS` 是没有服务端依据的结论，
 * 正好违反"不得把没判当成通过"。
 */
function parseCategorySummaries(
  record: Record<string, unknown>,
  path: string,
): QualityCategorySummary[] {
  const raw = record.categories;
  if (!Array.isArray(raw)) {
    throw new ContractValidationError(`${path}.categories 必须是数组`);
  }
  if (raw.length !== QUALITY_CATEGORY_VALUES.length) {
    throw new ContractValidationError(
      `${path}.categories 必须恰好包含 ${QUALITY_CATEGORY_VALUES.length} 类（含 OVERALL）`,
    );
  }
  return raw.map((item, index) => {
    const summary = parseQualityCategorySummary(
      item,
      `${path}.categories[${index}]`,
    );
    const expected = QUALITY_CATEGORY_VALUES[index];
    if (summary.category !== expected) {
      throw new ContractValidationError(
        `${path}.categories[${index}].category 必须是 ${expected}（六类顺序固定）`,
      );
    }
    return summary;
  });
}

function readRuleSetVersion(
  record: Record<string, unknown>,
  path: string,
): string {
  const value = readString(record, "rule_set_version", `${path}.rule_set_version`);
  if (!RULE_SET_VERSION_PATTERN.test(value)) {
    throw new ContractValidationError(
      `${path}.rule_set_version 必须是 sha256:<64 位小写十六进制>`,
    );
  }
  return value;
}

export function parseQualityRun(value: unknown, path = "质量运行"): QualityRun {
  const record = asRecord(value, path);
  return {
    run_id: readUuid(record, "run_id", `${path}.run_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    subject_type: readEnum(
      record,
      "subject_type",
      `${path}.subject_type`,
      QUALITY_SUBJECT_TYPE_VALUES,
    ),
    subject_id: readUuid(record, "subject_id", `${path}.subject_id`),
    rule_set_version: readRuleSetVersion(record, path),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      QUALITY_RUN_STATUS_VALUES,
    ),
    score: readUnitScore(record, "score", `${path}.score`),
    finding_count: readInteger(record, "finding_count", `${path}.finding_count`),
    error_count: readInteger(record, "error_count", `${path}.error_count`),
    warn_count: readInteger(record, "warn_count", `${path}.warn_count`),
    categories: parseCategorySummaries(record, path),
    started_at: readString(record, "started_at", `${path}.started_at`),
    finished_at: readString(record, "finished_at", `${path}.finished_at`),
    created_at: readString(record, "created_at", `${path}.created_at`),
    created_by: readUuid(record, "created_by", `${path}.created_by`),
  };
}

/**
 * 单条结论。
 *
 * `status` 与 `score` 的一致性由服务端保证；前端只校验取值范围，
 * **不**根据 `status` 反推或改写 `score`（那会复制领域判定）。
 */
export function parseQualityRunFinding(
  value: unknown,
  path = "质量结论",
): QualityRunFinding {
  const record = asRecord(value, path);
  return {
    finding_id: readUuid(record, "finding_id", `${path}.finding_id`),
    run_id: readUuid(record, "run_id", `${path}.run_id`),
    category: readEnum(
      record,
      "category",
      `${path}.category`,
      QUALITY_CATEGORY_VALUES,
    ),
    rule_id: readString(record, "rule_id", `${path}.rule_id`),
    rule_version: readString(record, "rule_version", `${path}.rule_version`),
    severity: readEnum(
      record,
      "severity",
      `${path}.severity`,
      QUALITY_SEVERITY_VALUES,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      QUALITY_FINDING_STATUS_VALUES,
    ),
    subject_ref: readOptionalString(record, "subject_ref", `${path}.subject_ref`) ?? "",
    message: readString(record, "message", `${path}.message`),
    evidence: readOptionalRecord(record, "evidence", `${path}.evidence`),
    score: readUnitScore(record, "score", `${path}.score`),
    created_at: readString(record, "created_at", `${path}.created_at`),
  };
}

export function parseQualityRunFindingPage(
  value: unknown,
  path = "质量结论列表",
): CursorPage<QualityRunFinding> {
  return parseCursorPage(value, path, (item, itemPath) =>
    parseQualityRunFinding(item, itemPath),
  );
}
