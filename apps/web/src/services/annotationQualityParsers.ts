/** 黄金集、质检样本与 IAA 报告的响应解析（拆分自 annotationResultParsers）。 */

import type { CursorPage } from "../types/api";
import type {
  GoldExpectedLabel,
  GoldSet,
  GoldSetItem,
  IAAMetricResult,
  IAAReport,
  QualitySample,
} from "../types/annotation";
import {
  GOLD_EXPOSURE_POLICY_VALUES,
  GOLD_ITEM_DIFFICULTY_VALUES,
  GOLD_ROTATION_POLICY_VALUES,
  GOLD_SET_STATUS_VALUES,
  IAA_METRIC_VALUES,
  IAA_REPORT_STATUS_VALUES,
  IAA_VALUE_STATUS_VALUES,
  QUALITY_SAMPLE_OUTCOME_VALUES,
  QUALITY_SAMPLE_STATUS_VALUES,
  QUALITY_SAMPLE_TYPE_VALUES,
} from "../types/annotation";
import {
  attachAllowedActions,
  type WithAllowedActions,
} from "./annotationAllowedActions";
import {
  readArray,
  readJsonValue,
  readOptionalBooleanValue,
} from "./annotationParseBase";
import {
  readOptionalPositiveInteger,
  readOptionalRecordArray,
  readRecord,
  readRecordArray,
} from "./annotationParsers";
import { parseChecklistItem, parseReviewIssue } from "./annotationReviewParsers";
import {
  ContractValidationError,
  asRecord,
  parseCursorPage,
  readBoolean,
  readEnum,
  readInteger,
  readNumber,
  readOptionalNumber,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readStringArray,
  readUuid,
  readUuidArray,
} from "./validation";

function parseGoldExpectedLabel(
  value: unknown,
  path: string,
): GoldExpectedLabel {
  const record = asRecord(value, path);
  const parsed: GoldExpectedLabel = {
    label_key: readString(record, "label_key", `${path}.label_key`),
    value: readJsonValue(record, "value", `${path}.value`),
  };
  if (record.tolerance !== undefined && record.tolerance !== null) {
    parsed.tolerance = asRecord(record.tolerance, `${path}.tolerance`);
  }
  return parsed;
}

function parseGoldSetItem(value: unknown, path: string): GoldSetItem {
  const record = asRecord(value, path);
  const parsed: GoldSetItem = {
    gold_item_id: readUuid(record, "gold_item_id", `${path}.gold_item_id`),
    episode_id: readUuid(record, "episode_id", `${path}.episode_id`),
    difficulty: readEnum(
      record,
      "difficulty",
      `${path}.difficulty`,
      GOLD_ITEM_DIFFICULTY_VALUES,
    ),
    weight: readNumber(record, "weight", `${path}.weight`),
    active: readBoolean(record, "active", `${path}.active`),
    answers_redacted:
      readOptionalBooleanValue(
        record,
        "answers_redacted",
        `${path}.answers_redacted`,
      ) ?? false,
  };
  const episodeVersion = readOptionalPositiveInteger(
    record,
    "episode_version",
    `${path}.episode_version`,
  );
  if (episodeVersion !== null) {
    parsed.episode_version = episodeVersion;
  }
  if (record.time_range !== undefined && record.time_range !== null) {
    parsed.time_range = asRecord(record.time_range, `${path}.time_range`);
  }
  if (record.expected_labels !== undefined && record.expected_labels !== null) {
    parsed.expected_labels = readArray(
      record,
      "expected_labels",
      `${path}.expected_labels`,
      parseGoldExpectedLabel,
    );
  }
  if (parsed.weight < 0) {
    throw new ContractValidationError(`${path}.weight 不得为负`);
  }
  return parsed;
}

export function parseGoldSet(
  value: unknown,
  path = "黄金集",
): WithAllowedActions<GoldSet> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    gold_set_id: readUuid(record, "gold_set_id", `${path}.gold_set_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readOptionalString(record, "name", `${path}.name`),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      GOLD_SET_STATUS_VALUES,
    ),
    schema_id: readUuid(record, "schema_id", `${path}.schema_id`),
    schema_version: readPositiveInteger(
      record,
      "schema_version",
      `${path}.schema_version`,
    ),
    schema_sha256: readSha256Checksum(
      record,
      "schema_sha256",
      `${path}.schema_sha256`,
    ),
    blind: readBoolean(record, "blind", `${path}.blind`),
    exposure_policy: readEnum(
      record,
      "exposure_policy",
      `${path}.exposure_policy`,
      GOLD_EXPOSURE_POLICY_VALUES,
    ),
    max_exposure_per_annotator: readPositiveInteger(
      record,
      "max_exposure_per_annotator",
      `${path}.max_exposure_per_annotator`,
    ),
    rotation_policy: readEnum(
      record,
      "rotation_policy",
      `${path}.rotation_policy`,
      GOLD_ROTATION_POLICY_VALUES,
    ),
    items: readArray(record, "items", `${path}.items`, parseGoldSetItem),
    version: readPositiveInteger(record, "version", `${path}.version`),
  }, record, path);
}

export function parseGoldSetPage(
  value: unknown,
): CursorPage<WithAllowedActions<GoldSet>> {
  return parseCursorPage(value, "黄金集列表", parseGoldSet);
}

export function parseQualitySample(
  value: unknown,
  path = "质检抽检",
): WithAllowedActions<QualitySample> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    quality_sample_id: readUuid(
      record,
      "quality_sample_id",
      `${path}.quality_sample_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    annotator_id: readUuid(record, "annotator_id", `${path}.annotator_id`),
    sampling_policy_version: readString(
      record,
      "sampling_policy_version",
      `${path}.sampling_policy_version`,
    ),
    sample_type: readEnum(
      record,
      "sample_type",
      `${path}.sample_type`,
      QUALITY_SAMPLE_TYPE_VALUES,
    ),
    sample_reason: readString(
      record,
      "sample_reason",
      `${path}.sample_reason`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      QUALITY_SAMPLE_STATUS_VALUES,
    ),
    stratum: readOptionalString(record, "stratum", `${path}.stratum`),
    item_refs: readUuidArray(record, "item_refs", `${path}.item_refs`),
    reviewer_id: readOptionalUuid(
      record,
      "reviewer_id",
      `${path}.reviewer_id`,
    ),
    review_id: readOptionalUuid(record, "review_id", `${path}.review_id`),
    arbitration_id: readOptionalUuid(
      record,
      "arbitration_id",
      `${path}.arbitration_id`,
    ),
    due_at: readOptionalString(record, "due_at", `${path}.due_at`),
    outcome:
      record.outcome === undefined || record.outcome === null
        ? null
        : readEnum(
            record,
            "outcome",
            `${path}.outcome`,
            QUALITY_SAMPLE_OUTCOME_VALUES,
          ),
    reason: readOptionalString(record, "reason", `${path}.reason`),
    closed_at: readOptionalString(record, "closed_at", `${path}.closed_at`),
    metadata: readRecord(record, "metadata", `${path}.metadata`),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseQualitySamplePage(
  value: unknown,
): CursorPage<WithAllowedActions<QualitySample>> {
  return parseCursorPage(value, "质检抽检列表", parseQualitySample);
}

function parseIAAMetric(value: unknown, path: string): IAAMetricResult {
  const record = asRecord(value, path);
  const parsed: IAAMetricResult = {
    metric: readEnum(
      record,
      "metric",
      `${path}.metric`,
      IAA_METRIC_VALUES,
    ),
    label_key: readString(record, "label_key", `${path}.label_key`),
    value: readOptionalNumber(record, "value", `${path}.value`),
    numerator: readOptionalNumber(
      record,
      "numerator",
      `${path}.numerator`,
    ),
    denominator: readOptionalNumber(
      record,
      "denominator",
      `${path}.denominator`,
    ),
    sample_size: readInteger(record, "sample_size", `${path}.sample_size`),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      IAA_VALUE_STATUS_VALUES,
    ),
  };
  const unit = readOptionalString(record, "unit", `${path}.unit`);
  if (unit !== null) {
    parsed.unit = unit;
  }
  if (record.config !== undefined && record.config !== null) {
    parsed.config = asRecord(record.config, `${path}.config`);
  }
  return parsed;
}

export function parseIAAReport(value: unknown, path = "IAA 报告"): IAAReport {
  const record = asRecord(value, path);
  return {
    report_id: readUuid(record, "report_id", `${path}.report_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    schema_id: readUuid(record, "schema_id", `${path}.schema_id`),
    schema_version: readPositiveInteger(
      record,
      "schema_version",
      `${path}.schema_version`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      IAA_REPORT_STATUS_VALUES,
    ),
    metric_definitions_version: readString(
      record,
      "metric_definitions_version",
      `${path}.metric_definitions_version`,
    ),
    sample_item_count: readInteger(
      record,
      "sample_item_count",
      `${path}.sample_item_count`,
    ),
    overlap_item_count: readInteger(
      record,
      "overlap_item_count",
      `${path}.overlap_item_count`,
    ),
    annotator_count: readInteger(
      record,
      "annotator_count",
      `${path}.annotator_count`,
    ),
    metrics: readArray(record, "metrics", `${path}.metrics`, parseIAAMetric),
    excluded_items: readOptionalRecordArray(
      record,
      "excluded_items",
      `${path}.excluded_items`,
    ),
    computed_at: readOptionalString(
      record,
      "computed_at",
      `${path}.computed_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
  };
}

export function parseIAAReportPage(
  value: unknown,
): CursorPage<IAAReport> {
  return parseCursorPage(value, "IAA 报告列表", parseIAAReport);
}

export {
  parseIAAMetric,
  parseGoldSetItem,
  parseReviewIssue,
  parseChecklistItem,
};

/** 黄金集评估结果（`POST /gold-sets/{id}/evaluations`）。 */
export interface GoldEvaluation {
  evaluation_id: string;
  project_id: string;
  gold_set_id: string;
  task_id: string;
  submission_id: string;
  evaluated_count: number;
  passed_count: number;
  /** `null` 表示没有可评估项，**不是 0%**。 */
  accuracy: number | null;
  status: string;
  sample_size: number;
  results: Record<string, unknown>[];
  computed_at: string;
}

/** 黄金集注入结果（`POST /gold-sets/{id}/injections`）。 */
export interface GoldInjection {
  gold_set_id: string;
  items: Record<string, unknown>[];
  exposure_ids: string[];
}

export function parseGoldEvaluation(
  value: unknown,
  path = "黄金集评估",
): GoldEvaluation {
  const record = asRecord(value, path);
  return {
    evaluation_id: readUuid(
      record,
      "evaluation_id",
      `${path}.evaluation_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    gold_set_id: readUuid(record, "gold_set_id", `${path}.gold_set_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    evaluated_count: readInteger(
      record,
      "evaluated_count",
      `${path}.evaluated_count`,
    ),
    passed_count: readInteger(
      record,
      "passed_count",
      `${path}.passed_count`,
    ),
    accuracy: readOptionalNumber(record, "accuracy", `${path}.accuracy`),
    status: readString(record, "status", `${path}.status`),
    sample_size: readInteger(record, "sample_size", `${path}.sample_size`),
    results: readRecordArray(record, "results", `${path}.results`),
    computed_at: readString(record, "computed_at", `${path}.computed_at`),
  };
}

export function parseGoldInjection(
  value: unknown,
  path = "黄金集注入",
): GoldInjection {
  const record = asRecord(value, path);
  return {
    gold_set_id: readUuid(record, "gold_set_id", `${path}.gold_set_id`),
    items: readRecordArray(record, "items", `${path}.items`),
    exposure_ids: readStringArray(
      record,
      "exposure_ids",
      `${path}.exposure_ids`,
    ),
  };
}
