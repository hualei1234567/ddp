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
  QUALITY_DIFF_KIND_VALUES,
  QUALITY_DIFF_STATUS_VALUES,
  QUALITY_FINDING_STATUS_VALUES,
  QUEUED_QUALITY_RUN_LIFECYCLE_VALUES,
  QUALITY_RUN_STATUS_VALUES,
  QUALITY_SEVERITY_VALUES,
  QUALITY_SUBJECT_TYPE_VALUES,
  type QualityCategorySummary,
  type QualityDiffStatus,
  type QualityEvidenceLocation,
  type QualityEvidenceTimeRange,
  type QualityFindingDiff,
  type QualityRun,
  type QualityRunDiffReport,
  type QualityRunFinding,
  type QueuedQualityRun,
} from "../types/qualityRun";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readEnum,
  readInteger,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

/** 契约 `rule_set_version` 的 pattern；与后端内容 SHA 同源。 */
const RULE_SET_VERSION_PATTERN = /^sha256:[0-9a-f]{64}$/;

/**
 * 差异条目的单侧结论：允许 null（该侧没有这条结论），出现时必须是
 * 封闭枚举之一——与 findings 的四态一致，`WARN` 在差异里不存在。
 */
function readDiffStatus(
  record: Record<string, unknown>,
  key: string,
  path: string,
): QualityDiffStatus | null {
  const value = record[key];
  if (value === undefined || value === null) {
    return null;
  }
  const matched = QUALITY_DIFF_STATUS_VALUES.find((item) => item === value);
  if (matched === undefined) {
    throw new ContractValidationError(
      `${path} 必须是 PASS / FAIL / NOT_APPLICABLE / ERROR 或 null`,
    );
  }
  return matched;
}

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
/** 纳秒字符串：只接受十进制整数形态；出现小数或科学计数法说明生产方写错了。 */
const NANOSECOND_PATTERN = /^-?[0-9]+$/;

function readNanosecondString(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string {
  const value = readString(record, key, path);
  if (!NANOSECOND_PATTERN.test(value)) {
    throw new ContractValidationError(`${path} 必须是十进制整数纳秒字符串`);
  }
  return value;
}

/**
 * 证据时间区间（契约 `QualityEvidenceTimeRange`）。
 *
 * 起止必须 `start < end`：只有一端、或倒置的区间都是缺陷数据，宁可解析失败
 * 也不要让时间轴渲染出一个负宽度的高亮块。
 */
export function parseQualityEvidenceTimeRange(
  value: unknown,
  path = "证据时间区间",
): QualityEvidenceTimeRange {
  const record = asRecord(value, path);
  const start = readNanosecondString(record, "start_time_ns", `${path}.start_time_ns`);
  const end = readNanosecondString(record, "end_time_ns", `${path}.end_time_ns`);
  if (BigInt(start) >= BigInt(end)) {
    throw new ContractValidationError(`${path}.start_time_ns 必须小于 end_time_ns`);
  }
  return { start_time_ns: start, end_time_ns: end };
}

/**
 * 证据定位（契约 `QualityEvidenceLocation`，V51-010）。
 *
 * 三个维度相互独立且都可空，但整体存在时至少要有一个非空维度——三者全空的
 * 对象是缺陷数据：服务端应返回 `null` 表达"无法定位"。
 */
export function parseQualityEvidenceLocation(
  value: unknown,
  path = "证据定位",
): QualityEvidenceLocation {
  const record = asRecord(value, path);
  const rawRange = readOptionalRecord(record, "time_range", `${path}.time_range`);
  const time_range =
    rawRange === null
      ? null
      : parseQualityEvidenceTimeRange(rawRange, `${path}.time_range`);
  const stream_id = readOptionalString(record, "stream_id", `${path}.stream_id`);
  const field_path = readOptionalString(record, "field_path", `${path}.field_path`);
  if (time_range === null && stream_id === null && field_path === null) {
    throw new ContractValidationError(
      `${path} 不能三个维度全空：无法定位时应返回 null`,
    );
  }
  return { time_range, stream_id, field_path };
}

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
    evidence_location: readEvidenceLocation(record, `${path}.evidence_location`),
    score: readUnitScore(record, "score", `${path}.score`),
    created_at: readString(record, "created_at", `${path}.created_at`),
  };
}

function readEvidenceLocation(
  record: Record<string, unknown>,
  path: string,
): QualityEvidenceLocation | null {
  const value = record.evidence_location;
  if (value === undefined || value === null) {
    return null;
  }
  return parseQualityEvidenceLocation(value, path);
}

export function parseQualityRunFindingPage(
  value: unknown,
  path = "质量结论列表",
): CursorPage<QualityRunFinding> {
  return parseCursorPage(value, path, (item, itemPath) =>
    parseQualityRunFinding(item, itemPath),
  );
}

/** 历史运行分页；条目复用单条运行解析，保持六类齐全等全部校验。 */
export function parseQualityRunPage(
  value: unknown,
  path = "质量运行列表",
): CursorPage<QualityRun> {
  return parseCursorPage(value, path, (item, itemPath) =>
    parseQualityRun(item, itemPath),
  );
}

// ---------------------------------------------------------------------------
// V31-033：排队回执与差异报告解析（契约 1.30.0）
// ---------------------------------------------------------------------------

/**
 * 排队回执（契约 `QueuedQualityRun`）。
 *
 * 该 schema **没有结论字段**是 ADR-0045 的类型事实：解析层不补任何
 * `status` / `score` 默认值，页面因此无法把「已排队」渲染成「检查通过」。
 */
export function parseQueuedQualityRun(
  value: unknown,
  path = "排队回执",
): QueuedQualityRun {
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
    lifecycle: readEnum(
      record,
      "lifecycle",
      `${path}.lifecycle`,
      QUEUED_QUALITY_RUN_LIFECYCLE_VALUES,
    ),
    max_attempts: readPositiveInteger(
      record,
      "max_attempts",
      `${path}.max_attempts`,
    ),
    created_at: readString(record, "created_at", `${path}.created_at`),
    created_by: readUuid(record, "created_by", `${path}.created_by`),
  };
}

/**
 * 差异条目（契约 `QualityFindingDiff`）。
 *
 * 两侧结论允许为 null（该侧没有这条结论）但不允许缺字段或取值越界；
 * `NOT_APPLICABLE` 与 `PASS` 保持两类，不把「没判」合并进「通过」。
 */
export function parseQualityFindingDiff(
  value: unknown,
  path = "质量差异条目",
): QualityFindingDiff {
  const record = asRecord(value, path);
  return {
    rule_id: readString(record, "rule_id", `${path}.rule_id`),
    subject_ref: readOptionalString(record, "subject_ref", `${path}.subject_ref`) ?? "",
    kind: readEnum(record, "kind", `${path}.kind`, QUALITY_DIFF_KIND_VALUES),
    baseline_status: readDiffStatus(record, "baseline_status", `${path}.baseline_status`),
    candidate_status: readDiffStatus(record, "candidate_status", `${path}.candidate_status`),
  };
}

/** 差异报告（契约 `QualityRunDiff`）。 */
export function parseQualityRunDiff(
  value: unknown,
  path = "质量差异报告",
): QualityRunDiffReport {
  const record = asRecord(value, path);
  const baselineRunId = readUuid(
    record,
    "baseline_run_id",
    `${path}.baseline_run_id`,
  );
  const candidateRunId = readUuid(
    record,
    "candidate_run_id",
    `${path}.candidate_run_id`,
  );
  const rawFindings = record.findings;
  if (!Array.isArray(rawFindings)) {
    throw new ContractValidationError(`${path}.findings 必须是数组`);
  }
  const findings = rawFindings.map((item, index) =>
    parseQualityFindingDiff(item, `${path}.findings[${index}]`),
  );
  const counts = {
    NEW: readInteger(record, "new_count", `${path}.new_count`),
    RESOLVED: readInteger(record, "resolved_count", `${path}.resolved_count`),
    UNCHANGED: readInteger(record, "unchanged_count", `${path}.unchanged_count`),
    CHANGED: readInteger(record, "changed_count", `${path}.changed_count`),
  } as const;
  // 汇总数与明细一致才放行：否则 UI 顶部的分类计数可能和列表对不上。
  QUALITY_DIFF_KIND_VALUES.forEach((kind) => {
    const actual = findings.filter((item) => item.kind === kind).length;
    if (counts[kind] !== actual) {
      throw new ContractValidationError(
        `${path}.${kind.toLowerCase()}_count 与 findings 中 ${kind} 条数不一致`,
      );
    }
  });
  return {
    baseline_run_id: baselineRunId,
    candidate_run_id: candidateRunId,
    findings,
    new_count: counts.NEW,
    resolved_count: counts.RESOLVED,
    unchanged_count: counts.UNCHANGED,
    changed_count: counts.CHANGED,
    has_regression: readBoolean(record, "has_regression", `${path}.has_regression`),
  };
}
