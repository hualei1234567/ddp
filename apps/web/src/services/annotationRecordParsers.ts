/** 标注记录与提交的响应解析（拆分自 annotationResultParsers）。 */

import type { CursorPage } from "../types/api";
import type {
  AnnotationRecord,
  AnnotationSubmission,
} from "../types/annotation";
import {
  ANNOTATION_RECORD_STATUS_VALUES,
  ANNOTATION_SUBMISSION_STATUS_VALUES,
  DATA_SOURCE_VALUES,
} from "../types/annotation";
import {
  parseEvidenceRef,
  parseLabelValue,
  readArray,
} from "./annotationParseBase";
import {
  ContractValidationError,
  asRecord,
  parseCursorPage,
  readEnum,
  readInteger,
  readOptionalRecord,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readUuid,
  readUuidArray,
} from "./validation";

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
