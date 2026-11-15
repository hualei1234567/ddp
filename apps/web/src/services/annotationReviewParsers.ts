/** 复核与仲裁的响应解析（拆分自 annotationResultParsers）。 */

import type { CursorPage } from "../types/api";
import type {
  AnnotationArbitration,
  AnnotationReview,
  ArbitrationVote,
  ReviewChecklistItem,
  ReviewIssue,
} from "../types/annotation";
import {
  ANNOTATION_ARBITRATION_STATUS_VALUES,
  ANNOTATION_REVIEW_STATUS_VALUES,
  ARBITRATION_DECISION_VALUES,
  ARBITRATION_TRIGGER_VALUES,
  REVIEW_DECISION_VALUES,
  REVIEW_ISSUE_SEVERITY_VALUES,
} from "../types/annotation";
import {
  readArray,
} from "./annotationParseBase";
import {
  asRecord,
  parseCursorPage,
  readBoolean,
  readEnum,
  readInteger,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readUuid,
  readUuidArray,
} from "./validation";

export function parseReviewIssue(value: unknown, path: string): ReviewIssue {
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

export function parseChecklistItem(value: unknown, path: string): ReviewChecklistItem {
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
