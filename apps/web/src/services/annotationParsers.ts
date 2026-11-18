import type { CursorPage } from "../types/api";
import type {
  AnnotationAssignment,
  AnnotationBatch,
  AnnotationSampleSet,
  AnnotationSchema,
  AnnotationTask,
  LabelDefinition,
  SampleEpisodeRef,
} from "../types/annotation";
import {
  ANNOTATION_ASSIGNMENT_STATUS_VALUES,
  ANNOTATION_BATCH_STATUS_VALUES,
  ANNOTATION_LABEL_TYPE_VALUES,
  ANNOTATION_LEVEL_VALUES,
  ANNOTATION_SCHEMA_STATUS_VALUES,
  ANNOTATION_TASK_STATUS_VALUES,
  ASSIGNMENT_ROLE_VALUES,
  DATA_SOURCE_VALUES,
  DEDUP_KEY_VALUES,
  LABEL_VISIBILITY_VALUES,
  REVIEW_MODE_VALUES,
  SAMPLE_ROLE_VALUES,
  SAMPLE_SET_STATUS_VALUES,
  SAMPLE_TIME_DOMAIN_VALUES,
  SAMPLE_STRATEGY_VALUES,
  TASK_PRIORITY_VALUES,
} from "../types/annotation";
import {
  attachAllowedActions,
  type WithAllowedActions,
} from "./annotationAllowedActions";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readEnum,
  readInteger,
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

/** 可选正整数：服务端在未计算版本字段时可能返回 null。 */
export function readOptionalPositiveInteger(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readPositiveInteger(record, key, path);
}

/** 必填 JSON 对象；缺失或非对象直接拒绝，不静默当作空对象。 */
export function readRecord(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown> {
  if (record[key] === undefined || record[key] === null) {
    throw new ContractValidationError(`${path} 必须是对象`);
  }
  return asRecord(record[key], path);
}

/** 对象数组；缺失时返回空数组，元素逐个严格校验。 */
export function readRecordArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown>[] {
  const value = record[key] ?? [];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value.map((item, index) => asRecord(item, `${path}[${index}]`));
}

function readOptionalRecordArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown>[] {
  if (record[key] === undefined || record[key] === null) {
    return [];
  }
  return readRecordArray(record, key, path);
}

function readEnumArray<T extends string>(
  record: Record<string, unknown>,
  key: string,
  path: string,
  allowed: readonly T[],
): T[] {
  return readStringArray(record, key, path).map((value, index) => {
    const matched = allowed.find((item) => item === value);
    if (matched === undefined) {
      throw new ContractValidationError(
        `${path}[${index}] 必须是以下枚举之一：${allowed.join(" / ")}`,
      );
    }
    return matched;
  });
}

function parseLabelDefinition(
  value: unknown,
  path: string,
): LabelDefinition {
  const record = asRecord(value, path);
  return {
    label_key: readString(record, "label_key", `${path}.label_key`),
    label_type: readEnum(
      record,
      "label_type",
      `${path}.label_type`,
      ANNOTATION_LABEL_TYPE_VALUES,
    ),
    required: readBoolean(record, "required", `${path}.required`),
    level: readEnum(
      record,
      "level",
      `${path}.level`,
      ANNOTATION_LEVEL_VALUES,
    ),
    visibility: readEnum(
      record,
      "visibility",
      `${path}.visibility`,
      LABEL_VISIBILITY_VALUES,
    ),
    description: readOptionalString(
      record,
      "description",
      `${path}.description`,
    ),
    value_spec: readRecord(record, "value_spec", `${path}.value_spec`),
    quality_rules: readStringArray(
      record,
      "quality_rules",
      `${path}.quality_rules`,
    ),
  };
}

export function parseAnnotationSchema(
  value: unknown,
  path = "标注 Schema",
): WithAllowedActions<AnnotationSchema> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    schema_id: readUuid(record, "schema_id", `${path}.schema_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    description: readOptionalString(
      record,
      "description",
      `${path}.description`,
    ),
    schema_lineage_id: readUuid(
      record,
      "schema_lineage_id",
      `${path}.schema_lineage_id`,
    ),
    schema_version: readPositiveInteger(
      record,
      "schema_version",
      `${path}.schema_version`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_SCHEMA_STATUS_VALUES,
    ),
    annotation_levels: readEnumArray(
      record,
      "annotation_levels",
      `${path}.annotation_levels`,
      ANNOTATION_LEVEL_VALUES,
    ),
    labels: readArray(record, "labels", `${path}.labels`, parseLabelDefinition),
    compatibility: readString(
      record,
      "compatibility",
      `${path}.compatibility`,
    ),
    schema_sha256: readSha256Checksum(
      record,
      "schema_sha256",
      `${path}.schema_sha256`,
    ),
    supersedes_id: readOptionalUuid(
      record,
      "supersedes_id",
      `${path}.supersedes_id`,
    ),
    published_at: readOptionalString(
      record,
      "published_at",
      `${path}.published_at`,
    ),
    deprecated_at: readOptionalString(
      record,
      "deprecated_at",
      `${path}.deprecated_at`,
    ),
    revoked_at: readOptionalString(
      record,
      "revoked_at",
      `${path}.revoked_at`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseAnnotationSchemaPage(
  value: unknown,
): CursorPage<WithAllowedActions<AnnotationSchema>> {
  return parseCursorPage(value, "标注 Schema 列表", parseAnnotationSchema);
}

function parseSampleEpisodeRef(
  value: unknown,
  path: string,
): SampleEpisodeRef {
  const record = asRecord(value, path);
  return {
    episode_id: readUuid(record, "episode_id", `${path}.episode_id`),
    episode_lineage_id: readUuid(
      record,
      "episode_lineage_id",
      `${path}.episode_lineage_id`,
    ),
    episode_version: readPositiveInteger(
      record,
      "episode_version",
      `${path}.episode_version`,
    ),
    sample_role: readEnum(
      record,
      "sample_role",
      `${path}.sample_role`,
      SAMPLE_ROLE_VALUES,
    ),
    selected_reason: readString(
      record,
      "selected_reason",
      `${path}.selected_reason`,
    ),
    stratum: readOptionalString(record, "stratum", `${path}.stratum`),
    time_domain: readEnum(
      record,
      "time_domain",
      `${path}.time_domain`,
      SAMPLE_TIME_DOMAIN_VALUES,
    ),
    start_time_ns: readOptionalNumber(
      record,
      "start_time_ns",
      `${path}.start_time_ns`,
    ),
    end_time_ns: readOptionalNumber(
      record,
      "end_time_ns",
      `${path}.end_time_ns`,
    ),
  };
}

export function parseAnnotationSampleSet(
  value: unknown,
  path = "标注样本集",
): WithAllowedActions<AnnotationSampleSet> {
  const record = asRecord(value, path);
  const snapshotSha = record.snapshot_sha256;
  return attachAllowedActions({
    sample_set_id: readUuid(
      record,
      "sample_set_id",
      `${path}.sample_set_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readOptionalString(record, "name", `${path}.name`),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      SAMPLE_SET_STATUS_VALUES,
    ),
    selection_rule_version: readString(
      record,
      "selection_rule_version",
      `${path}.selection_rule_version`,
    ),
    strategy: readEnum(
      record,
      "strategy",
      `${path}.strategy`,
      SAMPLE_STRATEGY_VALUES,
    ),
    filters: readRecord(record, "filters", `${path}.filters`),
    strata: readRecordArray(record, "strata", `${path}.strata`),
    episode_refs: readArray(
      record,
      "episode_refs",
      `${path}.episode_refs`,
      parseSampleEpisodeRef,
    ),
    dedup_key: readEnum(
      record,
      "dedup_key",
      `${path}.dedup_key`,
      DEDUP_KEY_VALUES,
    ),
    candidate_count: readInteger(
      record,
      "candidate_count",
      `${path}.candidate_count`,
    ),
    selected_count: readInteger(
      record,
      "selected_count",
      `${path}.selected_count`,
    ),
    excluded_count: readInteger(
      record,
      "excluded_count",
      `${path}.excluded_count`,
    ),
    deduplicated_count: readInteger(
      record,
      "deduplicated_count",
      `${path}.deduplicated_count`,
    ),
    snapshot_sha256:
      snapshotSha === undefined || snapshotSha === null
        ? null
        : readSha256Checksum(record, "snapshot_sha256", `${path}.snapshot_sha256`),
    snapshot_at: readOptionalString(
      record,
      "snapshot_at",
      `${path}.snapshot_at`,
    ),
    cancelled_reason: readOptionalString(
      record,
      "cancelled_reason",
      `${path}.cancelled_reason`,
    ),
    data_source: readEnum(
      record,
      "data_source",
      `${path}.data_source`,
      DATA_SOURCE_VALUES,
    ),
    exclusion_summary: readRecord(
      record,
      "exclusion_summary",
      `${path}.exclusion_summary`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseAnnotationSampleSetPage(
  value: unknown,
): CursorPage<WithAllowedActions<AnnotationSampleSet>> {
  return parseCursorPage(value, "标注样本集列表", parseAnnotationSampleSet);
}

export function parseAnnotationTask(
  value: unknown,
  path = "标注任务",
): WithAllowedActions<AnnotationTask> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    description: readOptionalString(
      record,
      "description",
      `${path}.description`,
    ),
    sample_set_id: readUuid(
      record,
      "sample_set_id",
      `${path}.sample_set_id`,
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
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_TASK_STATUS_VALUES,
    ),
    target_item_count: readInteger(
      record,
      "target_item_count",
      `${path}.target_item_count`,
    ),
    target_episode_count: readInteger(
      record,
      "target_episode_count",
      `${path}.target_episode_count`,
    ),
    priority: readEnum(
      record,
      "priority",
      `${path}.priority`,
      TASK_PRIORITY_VALUES,
    ),
    assignment_mode: readString(
      record,
      "assignment_mode",
      `${path}.assignment_mode`,
    ),
    review_mode: readEnum(
      record,
      "review_mode",
      `${path}.review_mode`,
      REVIEW_MODE_VALUES,
    ),
    iaa_policy: readRecord(record, "iaa_policy", `${path}.iaa_policy`),
    quality_policy: readRecord(
      record,
      "quality_policy",
      `${path}.quality_policy`,
    ),
    progress: readRecord(record, "progress", `${path}.progress`),
    quality_summary: readRecord(
      record,
      "quality_summary",
      `${path}.quality_summary`,
    ),
    due_at: readOptionalString(record, "due_at", `${path}.due_at`),
    is_overdue: readBoolean(record, "is_overdue", `${path}.is_overdue`),
    accepted_at: readOptionalString(
      record,
      "accepted_at",
      `${path}.accepted_at`,
    ),
    completed_at: readOptionalString(
      record,
      "completed_at",
      `${path}.completed_at`,
    ),
    gold_set_id: readOptionalUuid(
      record,
      "gold_set_id",
      `${path}.gold_set_id`,
    ),
    iaa_report_id: readOptionalUuid(
      record,
      "iaa_report_id",
      `${path}.iaa_report_id`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    blocked_reason: readOptionalString(
      record,
      "blocked_reason",
      `${path}.blocked_reason`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseAnnotationTaskPage(
  value: unknown,
): CursorPage<WithAllowedActions<AnnotationTask>> {
  return parseCursorPage(value, "标注任务列表", parseAnnotationTask);
}

export function parseAnnotationBatch(
  value: unknown,
  path = "标注批次",
): WithAllowedActions<AnnotationBatch> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    batch_id: readUuid(record, "batch_id", `${path}.batch_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    code: readString(record, "code", `${path}.code`),
    sequence_no: readPositiveInteger(
      record,
      "sequence_no",
      `${path}.sequence_no`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_BATCH_STATUS_VALUES,
    ),
    name: readOptionalString(record, "name", `${path}.name`),
    stratum: readOptionalString(record, "stratum", `${path}.stratum`),
    item_refs: readUuidArray(record, "item_refs", `${path}.item_refs`),
    planned_item_count: readInteger(
      record,
      "planned_item_count",
      `${path}.planned_item_count`,
    ),
    assignee_ids: readUuidArray(
      record,
      "assignee_ids",
      `${path}.assignee_ids`,
    ),
    reviewer_pool_ids: readUuidArray(
      record,
      "reviewer_pool_ids",
      `${path}.reviewer_pool_ids`,
    ),
    due_at: readOptionalString(record, "due_at", `${path}.due_at`),
    submitted_item_count: readInteger(
      record,
      "submitted_item_count",
      `${path}.submitted_item_count`,
    ),
    approved_item_count: readInteger(
      record,
      "approved_item_count",
      `${path}.approved_item_count`,
    ),
    rejected_item_count: readInteger(
      record,
      "rejected_item_count",
      `${path}.rejected_item_count`,
    ),
    rework_count: readInteger(record, "rework_count", `${path}.rework_count`),
    quality_summary: readRecord(
      record,
      "quality_summary",
      `${path}.quality_summary`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseAnnotationBatchPage(
  value: unknown,
): CursorPage<WithAllowedActions<AnnotationBatch>> {
  return parseCursorPage(value, "标注批次列表", parseAnnotationBatch);
}

export function parseAnnotationAssignment(
  value: unknown,
  path = "标注分配",
): WithAllowedActions<AnnotationAssignment> {
  const record = asRecord(value, path);
  return attachAllowedActions({
    assignment_id: readUuid(
      record,
      "assignment_id",
      `${path}.assignment_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    batch_id: readUuid(record, "batch_id", `${path}.batch_id`),
    assignee_id: readUuid(record, "assignee_id", `${path}.assignee_id`),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_ASSIGNMENT_STATUS_VALUES,
    ),
    assignment_role: readEnum(
      record,
      "assignment_role",
      `${path}.assignment_role`,
      ASSIGNMENT_ROLE_VALUES,
    ),
    planned_item_count: readInteger(
      record,
      "planned_item_count",
      `${path}.planned_item_count`,
    ),
    claimed_item_count: readInteger(
      record,
      "claimed_item_count",
      `${path}.claimed_item_count`,
    ),
    submitted_item_count: readInteger(
      record,
      "submitted_item_count",
      `${path}.submitted_item_count`,
    ),
    approved_item_count: readInteger(
      record,
      "approved_item_count",
      `${path}.approved_item_count`,
    ),
    planned_minutes: readInteger(
      record,
      "planned_minutes",
      `${path}.planned_minutes`,
    ),
    actual_minutes: readOptionalNumber(
      record,
      "actual_minutes",
      `${path}.actual_minutes`,
    ),
    lease_expires_at: readOptionalString(
      record,
      "lease_expires_at",
      `${path}.lease_expires_at`,
    ),
    claimed_at: readOptionalString(record, "claimed_at", `${path}.claimed_at`),
    submitted_at: readOptionalString(
      record,
      "submitted_at",
      `${path}.submitted_at`,
    ),
    completed_at: readOptionalString(
      record,
      "completed_at",
      `${path}.completed_at`,
    ),
    revoked_reason: readOptionalString(
      record,
      "revoked_reason",
      `${path}.revoked_reason`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  }, record, path);
}

export function parseAnnotationAssignmentPage(
  value: unknown,
): CursorPage<WithAllowedActions<AnnotationAssignment>> {
  return parseCursorPage(value, "标注分配列表", parseAnnotationAssignment);
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

export { readOptionalRecordArray };
