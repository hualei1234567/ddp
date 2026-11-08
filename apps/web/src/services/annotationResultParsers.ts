import type { CursorPage } from "../types/api";
import type {
  AnnotationArbitration,
  AnnotationRecord,
  AnnotationReview,
  AnnotationSubmission,
  ArbitrationVote,
  EvidenceRef,
  GoldExpectedLabel,
  GoldSet,
  GoldSetItem,
  IAAReport,
  IAAMetricResult,
  LabelValue,
  QualitySample,
  ReviewChecklistItem,
  ReviewIssue,
} from "../types/annotation";
import {
  ANNOTATION_ARBITRATION_STATUS_VALUES,
  ANNOTATION_RECORD_STATUS_VALUES,
  ANNOTATION_REVIEW_STATUS_VALUES,
  ANNOTATION_SUBMISSION_STATUS_VALUES,
  ARBITRATION_DECISION_VALUES,
  ARBITRATION_TRIGGER_VALUES,
  DATA_SOURCE_VALUES,
  GOLD_EXPOSURE_POLICY_VALUES,
  GOLD_ITEM_DIFFICULTY_VALUES,
  GOLD_ROTATION_POLICY_VALUES,
  GOLD_SET_STATUS_VALUES,
  IAA_METRIC_VALUES,
  IAA_REPORT_STATUS_VALUES,
  IAA_VALUE_STATUS_VALUES,
  LABEL_VALUE_SOURCE_VALUES,
  LABEL_VALUE_STATUS_VALUES,
  QUALITY_SAMPLE_OUTCOME_VALUES,
  QUALITY_SAMPLE_STATUS_VALUES,
  QUALITY_SAMPLE_TYPE_VALUES,
  REVIEW_DECISION_VALUES,
  REVIEW_ISSUE_SEVERITY_VALUES,
} from "../types/annotation";
import {
  readOptionalPositiveInteger,
  readOptionalRecordArray,
  readRecord,
} from "./annotationParsers";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readEnum,
  readInteger,
  readNumber,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readUuid,
  readUuidArray,
} from "./validation";

/** JSON 值必须存在；JSON.parse 之后不会出现函数等非 JSON 类型。 */
function readJsonValue(
  record: Record<string, unknown>,
  key: string,
  path: string,
): unknown {
  if (!(key in record) || record[key] === undefined) {
    throw new ContractValidationError(`${path} 缺失`);
  }
  return record[key];
}

function readOptionalBooleanValue(
  record: Record<string, unknown>,
  key: string,
  path: string,
): boolean | undefined {
  if (record[key] === undefined || record[key] === null) {
    return undefined;
  }
  return readBoolean(record, key, path);
}

function readArray<T>(
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

function parseLabelValue(value: unknown, path: string): LabelValue {
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

function parseEvidenceRef(value: unknown, path: string): EvidenceRef {
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

export function parseAnnotationRecord(
  value: unknown,
  path = "标注记录",
): AnnotationRecord {
  const record = asRecord(value, path);
  return {
    annotation_id: readUuid(record, "annotation_id", `${path}.annotation_id`),
    annotation_lineage_id: readUuid(
      record,
      "annotation_lineage_id",
      `${path}.annotation_lineage_id`,
    ),
    annotation_version: readPositiveInteger(
      record,
      "annotation_version",
      `${path}.annotation_version`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    submission_id: readUuid(record, "submission_id", `${path}.submission_id`),
    episode_id: readUuid(record, "episode_id", `${path}.episode_id`),
    episode_version: readPositiveInteger(
      record,
      "episode_version",
      `${path}.episode_version`,
    ),
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
      ANNOTATION_RECORD_STATUS_VALUES,
    ),
    time_range: readOptionalRecord(
      record,
      "time_range",
      `${path}.time_range`,
    ),
    labels: readArray(record, "labels", `${path}.labels`, parseLabelValue),
    evidence_refs: readArray(
      record,
      "evidence_refs",
      `${path}.evidence_refs`,
      parseEvidenceRef,
    ),
    record_sha256: readSha256Checksum(
      record,
      "record_sha256",
      `${path}.record_sha256`,
    ),
    supersedes_id: readOptionalUuid(
      record,
      "supersedes_id",
      `${path}.supersedes_id`,
    ),
    approved_by: readOptionalUuid(
      record,
      "approved_by",
      `${path}.approved_by`,
    ),
    approved_at: readOptionalString(
      record,
      "approved_at",
      `${path}.approved_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseAnnotationRecordPage(
  value: unknown,
): CursorPage<AnnotationRecord> {
  return parseCursorPage(value, "标注记录列表", parseAnnotationRecord);
}

export function parseAnnotationSubmission(
  value: unknown,
  path = "标注提交",
): AnnotationSubmission {
  const record = asRecord(value, path);
  const submission: AnnotationSubmission = {
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    batch_id: readUuid(record, "batch_id", `${path}.batch_id`),
    assignment_id: readUuid(
      record,
      "assignment_id",
      `${path}.assignment_id`,
    ),
    annotator_id: readUuid(record, "annotator_id", `${path}.annotator_id`),
    submission_version: readPositiveInteger(
      record,
      "submission_version",
      `${path}.submission_version`,
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
    records_digest: readSha256Checksum(
      record,
      "records_digest",
      `${path}.records_digest`,
    ),
    item_count: readInteger(record, "item_count", `${path}.item_count`),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_SUBMISSION_STATUS_VALUES,
    ),
    data_source: readEnum(
      record,
      "data_source",
      `${path}.data_source`,
      DATA_SOURCE_VALUES,
    ),
    record_refs: readUuidArray(record, "record_refs", `${path}.record_refs`),
    records: readArray(
      record,
      "records",
      `${path}.records`,
      parseAnnotationRecord,
    ),
    review_id: readOptionalUuid(record, "review_id", `${path}.review_id`),
    arbitration_id: readOptionalUuid(
      record,
      "arbitration_id",
      `${path}.arbitration_id`,
    ),
    supersedes_id: readOptionalUuid(
      record,
      "supersedes_id",
      `${path}.supersedes_id`,
    ),
    submitted_at: readOptionalString(
      record,
      "submitted_at",
      `${path}.submitted_at`,
    ),
    decided_at: readOptionalString(
      record,
      "decided_at",
      `${path}.decided_at`,
    ),
    reason: readOptionalString(record, "reason", `${path}.reason`),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
  for (const item of submission.records) {
    if (item.project_id !== submission.project_id) {
      throw new ContractValidationError(
        `${path}.records 中存在与提交项目不一致的记录`,
      );
    }
    if (item.submission_id !== submission.submission_id) {
      throw new ContractValidationError(
        `${path}.records 中存在与提交 ID 不一致的记录`,
      );
    }
  }
  return submission;
}

export function parseAnnotationSubmissionPage(
  value: unknown,
): CursorPage<AnnotationSubmission> {
  return parseCursorPage(
    value,
    "标注提交列表",
    parseAnnotationSubmission,
  );
}

function parseReviewIssue(value: unknown, path: string): ReviewIssue {
  const record = asRecord(value, path);
  return {
    code: readString(record, "code", `${path}.code`),
    severity: readEnum(
      record,
      "severity",
      `${path}.severity`,
      REVIEW_ISSUE_SEVERITY_VALUES,
    ),
    message: readString(record, "message", `${path}.message`),
    label_key: readOptionalString(record, "label_key", `${path}.label_key`),
    item_ref: readOptionalString(record, "item_ref", `${path}.item_ref`),
    evidence_ref: readOptionalString(
      record,
      "evidence_ref",
      `${path}.evidence_ref`,
    ),
  };
}

function parseChecklistItem(value: unknown, path: string): ReviewChecklistItem {
  const record = asRecord(value, path);
  return {
    key: readString(record, "key", `${path}.key`),
    passed: readBoolean(record, "passed", `${path}.passed`),
    comment: readOptionalString(record, "comment", `${path}.comment`),
  };
}

export function parseAnnotationReview(
  value: unknown,
  path = "标注复核",
): AnnotationReview {
  const record = asRecord(value, path);
  return {
    review_id: readUuid(record, "review_id", `${path}.review_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    reviewer_id: readUuid(record, "reviewer_id", `${path}.reviewer_id`),
    review_round: readPositiveInteger(
      record,
      "review_round",
      `${path}.review_round`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_REVIEW_STATUS_VALUES,
    ),
    decision:
      record.decision === undefined || record.decision === null
        ? null
        : readEnum(
            record,
            "decision",
            `${path}.decision`,
            REVIEW_DECISION_VALUES,
          ),
    issues: readArray(record, "issues", `${path}.issues`, parseReviewIssue),
    checklist: readArray(
      record,
      "checklist",
      `${path}.checklist`,
      parseChecklistItem,
    ),
    review_duration_sec:
      record.review_duration_sec === undefined ||
      record.review_duration_sec === null
        ? null
        : readInteger(
            record,
            "review_duration_sec",
            `${path}.review_duration_sec`,
          ),
    reason: readOptionalString(record, "reason", `${path}.reason`),
    decided_at: readOptionalString(
      record,
      "decided_at",
      `${path}.decided_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseAnnotationReviewPage(
  value: unknown,
): CursorPage<AnnotationReview> {
  return parseCursorPage(value, "标注复核列表", parseAnnotationReview);
}

function parseArbitrationVote(value: unknown, path: string): ArbitrationVote {
  const record = asRecord(value, path);
  return {
    arbiter_id: readUuid(record, "arbiter_id", `${path}.arbiter_id`),
    decision: readEnum(
      record,
      "decision",
      `${path}.decision`,
      ARBITRATION_DECISION_VALUES,
    ),
    reason: readOptionalString(record, "reason", `${path}.reason`),
    voted_at: readString(record, "voted_at", `${path}.voted_at`),
  };
}

export function parseAnnotationArbitration(
  value: unknown,
  path = "标注仲裁",
): AnnotationArbitration {
  const record = asRecord(value, path);
  return {
    arbitration_id: readUuid(
      record,
      "arbitration_id",
      `${path}.arbitration_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    submission_id: readUuid(
      record,
      "submission_id",
      `${path}.submission_id`,
    ),
    review_id: readOptionalUuid(record, "review_id", `${path}.review_id`),
    arbiter_ids: readUuidArray(
      record,
      "arbiter_ids",
      `${path}.arbiter_ids`,
    ),
    quorum: readPositiveInteger(record, "quorum", `${path}.quorum`),
    trigger: readEnum(
      record,
      "trigger",
      `${path}.trigger`,
      ARBITRATION_TRIGGER_VALUES,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_ARBITRATION_STATUS_VALUES,
    ),
    votes: readArray(
      record,
      "votes",
      `${path}.votes`,
      parseArbitrationVote,
    ),
    effective_vote_count: readInteger(
      record,
      "effective_vote_count",
      `${path}.effective_vote_count`,
    ),
    final_decision:
      record.final_decision === undefined || record.final_decision === null
        ? null
        : readEnum(
            record,
            "final_decision",
            `${path}.final_decision`,
            ["APPROVE", "REJECT", "REWORK"] as const,
          ),
    reason: readOptionalString(record, "reason", `${path}.reason`),
    resolved_at: readOptionalString(
      record,
      "resolved_at",
      `${path}.resolved_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseAnnotationArbitrationPage(
  value: unknown,
): CursorPage<AnnotationArbitration> {
  return parseCursorPage(
    value,
    "标注仲裁列表",
    parseAnnotationArbitration,
  );
}

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

export function parseGoldSet(value: unknown, path = "黄金集"): GoldSet {
  const record = asRecord(value, path);
  return {
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
  };
}

export function parseGoldSetPage(value: unknown): CursorPage<GoldSet> {
  return parseCursorPage(value, "黄金集列表", parseGoldSet);
}

export function parseQualitySample(
  value: unknown,
  path = "质检抽检",
): QualitySample {
  const record = asRecord(value, path);
  return {
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
  };
}

export function parseQualitySamplePage(
  value: unknown,
): CursorPage<QualitySample> {
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
