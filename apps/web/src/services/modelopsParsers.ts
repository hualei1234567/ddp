/** 阶段 5 响应解析器。 */

import type {
  DataRequirement,
  Evaluation,
  GapDimension,
  GapSummary,
  InferenceService,
  InferenceTask,
  Model,
  ModelArtifact,
  ModelFeedback,
  ModelRuntime,
  ModelVersion,
} from "../types/modelops";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readStringArray,
  readUuid,
} from "./validation";

function readNumberMap(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, number> {
  const value = record[key];
  if (value === undefined || value === null) {
    return {};
  }
  const map = asRecord(value, `${path}.${key}`);
  const result: Record<string, number> = {};
  for (const [name, item] of Object.entries(map)) {
    if (typeof item !== "number" || !Number.isFinite(item)) {
      throw new ContractValidationError(`${path}.${key}.${name} 必须是数字`);
    }
    result[name] = item;
  }
  return result;
}

export function parseGapDimension(value: unknown, path = "dimension"): GapDimension {
  const record = asRecord(value, path);
  return {
    dimension_type: readString(record, "dimension_type", path),
    dimension_value: readString(record, "dimension_value", path),
    current_count: readOptionalNumber(record, "current_count", path),
    target_count: readOptionalNumber(record, "target_count", path),
  };
}

export function parseModel(value: unknown): Model {
  const record = asRecord(value, "model");
  return {
    model_id: readUuid(record, "model_id", "model"),
    project_id: readUuid(record, "project_id", "model"),
    code: readString(record, "code", "model"),
    name: readString(record, "name", "model"),
    description: readOptionalString(record, "description", "model"),
    task_type: readString(record, "task_type", "model"),
    framework: readString(record, "framework", "model"),
    owner_id: readUuid(record, "owner_id", "model"),
    status: readString(record, "status", "model"),
    current_version_id: readOptionalUuid(record, "current_version_id", "model"),
    tags: record.tags ? readStringArray(record, "tags", "model") : [],
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "model")
      : [],
    version: readPositiveInteger(record, "version", "model"),
    created_at: readString(record, "created_at", "model"),
    updated_at: readString(record, "updated_at", "model"),
  };
}

export function parseModelPage(value: unknown) {
  return parseCursorPage(value, "models", parseModel);
}

function parseArtifact(value: unknown): ModelArtifact {
  const record = asRecord(value, "modelVersion.artifact");
  return {
    object_key: readString(record, "object_key", "artifact"),
    sha256: readSha256Checksum(record, "sha256", "artifact"),
    size_bytes: readInteger(record, "size_bytes", "artifact"),
    format: readString(record, "format", "artifact"),
    verification_status: readOptionalString(record, "verification_status", "artifact") ?? "UNVERIFIED",
  };
}

function parseRuntime(value: unknown): ModelRuntime {
  const record = asRecord(value, "modelVersion.runtime");
  return {
    framework: readString(record, "framework", "runtime"),
    framework_version: readString(record, "framework_version", "runtime"),
    python_version: readOptionalString(record, "python_version", "runtime"),
    cuda_version: readOptionalString(record, "cuda_version", "runtime"),
    image_ref: readOptionalString(record, "image_ref", "runtime"),
  };
}

export function parseModelVersion(value: unknown): ModelVersion {
  const record = asRecord(value, "modelVersion");
  return {
    model_version_id: readUuid(record, "model_version_id", "modelVersion"),
    model_id: readUuid(record, "model_id", "modelVersion"),
    project_id: readUuid(record, "project_id", "modelVersion"),
    version_no: readPositiveInteger(record, "version_no", "modelVersion"),
    status: readString(record, "status", "modelVersion"),
    artifact: parseArtifact(record.artifact),
    runtime: parseRuntime(record.runtime),
    training_request_id: readOptionalUuid(record, "training_request_id", "modelVersion"),
    training_delivery_id: readOptionalUuid(record, "training_delivery_id", "modelVersion"),
    external_ref_id: readOptionalUuid(record, "external_ref_id", "modelVersion"),
    dataset_version_ids: record.dataset_version_ids
      ? readStringArray(record, "dataset_version_ids", "modelVersion")
      : [],
    metrics_summary: readNumberMap(record, "metrics_summary", "modelVersion"),
    release_notes: readOptionalString(record, "release_notes", "modelVersion"),
    validated_at: readOptionalString(record, "validated_at", "modelVersion"),
    released_at: readOptionalString(record, "released_at", "modelVersion"),
    deprecated_at: readOptionalString(record, "deprecated_at", "modelVersion"),
    deprecation_reason: readOptionalString(record, "deprecation_reason", "modelVersion"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "modelVersion")
      : [],
    version: readPositiveInteger(record, "version", "modelVersion"),
    created_at: readString(record, "created_at", "modelVersion"),
    updated_at: readString(record, "updated_at", "modelVersion"),
  };
}

export function parseModelVersionPage(value: unknown) {
  return parseCursorPage(value, "modelVersions", parseModelVersion);
}

export function parseEvaluation(value: unknown): Evaluation {
  const record = asRecord(value, "evaluation");
  const thresholdsRaw = record.thresholds;
  const thresholds: Evaluation["thresholds"] = {};
  if (thresholdsRaw && typeof thresholdsRaw === "object") {
    for (const [name, bound] of Object.entries(
      thresholdsRaw as Record<string, unknown>,
    )) {
      const entry = asRecord(bound, `evaluation.thresholds.${name}`);
      thresholds[name] = {
        min: entry.min === undefined ? null : readOptionalNumber(entry, "min", name),
        max: entry.max === undefined ? null : readOptionalNumber(entry, "max", name),
      };
    }
  }
  return {
    evaluation_id: readUuid(record, "evaluation_id", "evaluation"),
    project_id: readUuid(record, "project_id", "evaluation"),
    model_version_id: readUuid(record, "model_version_id", "evaluation"),
    dataset_version_id: readUuid(record, "dataset_version_id", "evaluation"),
    baseline_evaluation_id: readOptionalUuid(record, "baseline_evaluation_id", "evaluation"),
    status: readString(record, "status", "evaluation"),
    metrics: readNumberMap(record, "metrics", "evaluation"),
    thresholds,
    verdict: readString(record, "verdict", "evaluation"),
    report_ref: record.report_ref
      ? asRecord(record.report_ref, "evaluation.report_ref")
      : null,
    error_code: readOptionalString(record, "error_code", "evaluation"),
    error_summary: readOptionalString(record, "error_summary", "evaluation"),
    started_at: readOptionalString(record, "started_at", "evaluation"),
    finished_at: readOptionalString(record, "finished_at", "evaluation"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "evaluation")
      : [],
    version: readPositiveInteger(record, "version", "evaluation"),
    created_at: readString(record, "created_at", "evaluation"),
    updated_at: readString(record, "updated_at", "evaluation"),
  };
}

export function parseEvaluationPage(value: unknown) {
  return parseCursorPage(value, "evaluations", parseEvaluation);
}

export function parseInferenceService(value: unknown): InferenceService {
  const record = asRecord(value, "inferenceService");
  return {
    service_id: readUuid(record, "service_id", "inferenceService"),
    project_id: readUuid(record, "project_id", "inferenceService"),
    model_version_id: readUuid(record, "model_version_id", "inferenceService"),
    environment: readString(record, "environment", "inferenceService"),
    endpoint_url: readString(record, "endpoint_url", "inferenceService"),
    resource_ref: readOptionalString(record, "resource_ref", "inferenceService"),
    config: record.config ? asRecord(record.config, "inferenceService.config") : {},
    status: readString(record, "status", "inferenceService"),
    health_status: readString(record, "health_status", "inferenceService"),
    deployed_at: readOptionalString(record, "deployed_at", "inferenceService"),
    stopped_at: readOptionalString(record, "stopped_at", "inferenceService"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "inferenceService")
      : [],
    version: readPositiveInteger(record, "version", "inferenceService"),
    created_at: readString(record, "created_at", "inferenceService"),
    updated_at: readString(record, "updated_at", "inferenceService"),
  };
}

export function parseInferenceServicePage(value: unknown) {
  return parseCursorPage(value, "inferenceServices", parseInferenceService);
}

export function parseInferenceTask(value: unknown): InferenceTask {
  const record = asRecord(value, "inferenceTask");
  return {
    task_id: readUuid(record, "task_id", "inferenceTask"),
    project_id: readUuid(record, "project_id", "inferenceTask"),
    model_version_id: readUuid(record, "model_version_id", "inferenceTask"),
    service_id: readOptionalUuid(record, "service_id", "inferenceTask"),
    dataset_version_id: readOptionalUuid(record, "dataset_version_id", "inferenceTask"),
    episode_ids: record.episode_ids
      ? readStringArray(record, "episode_ids", "inferenceTask")
      : [],
    status: readString(record, "status", "inferenceTask"),
    result_ref: record.result_ref
      ? asRecord(record.result_ref, "inferenceTask.result_ref")
      : null,
    metrics: readNumberMap(record, "metrics", "inferenceTask"),
    log_ref: readOptionalString(record, "log_ref", "inferenceTask"),
    error_code: readOptionalString(record, "error_code", "inferenceTask"),
    error_summary: readOptionalString(record, "error_summary", "inferenceTask"),
    started_at: readOptionalString(record, "started_at", "inferenceTask"),
    finished_at: readOptionalString(record, "finished_at", "inferenceTask"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "inferenceTask")
      : [],
    version: readPositiveInteger(record, "version", "inferenceTask"),
    created_at: readString(record, "created_at", "inferenceTask"),
    updated_at: readString(record, "updated_at", "inferenceTask"),
  };
}

export function parseInferenceTaskPage(value: unknown) {
  return parseCursorPage(value, "inferenceTasks", parseInferenceTask);
}

export function parseFeedback(value: unknown): ModelFeedback {
  const record = asRecord(value, "modelFeedback");
  const dimensions = record.dimensions;
  if (!Array.isArray(dimensions)) {
    throw new ContractValidationError("modelFeedback.dimensions 必须是数组");
  }
  return {
    feedback_id: readUuid(record, "feedback_id", "modelFeedback"),
    project_id: readUuid(record, "project_id", "modelFeedback"),
    model_version_id: readUuid(record, "model_version_id", "modelFeedback"),
    evaluation_id: readOptionalUuid(record, "evaluation_id", "modelFeedback"),
    inference_task_id: readOptionalUuid(record, "inference_task_id", "modelFeedback"),
    issue_type: readString(record, "issue_type", "modelFeedback"),
    severity: readString(record, "severity", "modelFeedback"),
    description: readString(record, "description", "modelFeedback"),
    dimensions: dimensions.map((item, index) =>
      parseGapDimension(item, `modelFeedback.dimensions[${index}]`),
    ),
    evidence_refs: record.evidence_refs
      ? readStringArray(record, "evidence_refs", "modelFeedback")
      : [],
    status: readString(record, "status", "modelFeedback"),
    converted_requirement_id: readOptionalUuid(
      record,
      "converted_requirement_id",
      "modelFeedback",
    ),
    resolution_note: readOptionalString(record, "resolution_note", "modelFeedback"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "modelFeedback")
      : [],
    version: readPositiveInteger(record, "version", "modelFeedback"),
    created_at: readString(record, "created_at", "modelFeedback"),
    updated_at: readString(record, "updated_at", "modelFeedback"),
  };
}

export function parseFeedbackPage(value: unknown) {
  return parseCursorPage(value, "modelFeedback", parseFeedback);
}

export function parseRequirement(value: unknown): DataRequirement {
  const record = asRecord(value, "dataRequirement");
  const dimensions = record.dimensions;
  if (!Array.isArray(dimensions)) {
    throw new ContractValidationError("dataRequirement.dimensions 必须是数组");
  }
  return {
    requirement_id: readUuid(record, "requirement_id", "dataRequirement"),
    project_id: readUuid(record, "project_id", "dataRequirement"),
    code: readString(record, "code", "dataRequirement"),
    title: readString(record, "title", "dataRequirement"),
    description: readOptionalString(record, "description", "dataRequirement"),
    source_type: readString(record, "source_type", "dataRequirement"),
    source_feedback_ids: record.source_feedback_ids
      ? readStringArray(record, "source_feedback_ids", "dataRequirement")
      : [],
    source_evaluation_ids: record.source_evaluation_ids
      ? readStringArray(record, "source_evaluation_ids", "dataRequirement")
      : [],
    dimensions: dimensions.map((item, index) =>
      parseGapDimension(item, `dataRequirement.dimensions[${index}]`),
    ),
    target_episode_count: readInteger(record, "target_episode_count", "dataRequirement"),
    quality_thresholds: record.quality_thresholds
      ? asRecord(record.quality_thresholds, "dataRequirement.quality_thresholds")
      : {},
    priority: readString(record, "priority", "dataRequirement"),
    due_at: readOptionalString(record, "due_at", "dataRequirement"),
    owner_id: readUuid(record, "owner_id", "dataRequirement"),
    status: readString(record, "status", "dataRequirement"),
    linked_dataset_version_ids: record.linked_dataset_version_ids
      ? readStringArray(record, "linked_dataset_version_ids", "dataRequirement")
      : [],
    retest_evaluation_ids: record.retest_evaluation_ids
      ? readStringArray(record, "retest_evaluation_ids", "dataRequirement")
      : [],
    satisfied_at: readOptionalString(record, "satisfied_at", "dataRequirement"),
    satisfaction_evidence_ref: readOptionalString(
      record,
      "satisfaction_evidence_ref",
      "dataRequirement",
    ),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "dataRequirement")
      : [],
    version: readPositiveInteger(record, "version", "dataRequirement"),
    created_at: readString(record, "created_at", "dataRequirement"),
    updated_at: readString(record, "updated_at", "dataRequirement"),
  };
}

export function parseRequirementPage(value: unknown) {
  return parseCursorPage(value, "dataRequirements", parseRequirement);
}

export function parseGapSummary(value: unknown): GapSummary {
  const record = asRecord(value, "dataGaps");
  const items = record.items;
  if (!Array.isArray(items)) {
    throw new ContractValidationError("dataGaps.items 必须是数组");
  }
  return {
    items: items.map((item, index) => {
      const path = `dataGaps.items[${index}]`;
      const row = asRecord(item, path);
      return {
        dimension_type: readString(row, "dimension_type", path),
        dimension_value: readString(row, "dimension_value", path),
        open_feedback_count: readInteger(row, "open_feedback_count", path),
        current_count: readOptionalNumber(row, "current_count", path),
        target_count: readOptionalNumber(row, "target_count", path),
        suggested_episode_count: readInteger(row, "suggested_episode_count", path),
        requirement_ids: row.requirement_ids
          ? readStringArray(row, "requirement_ids", path)
          : [],
      };
    }),
    freshness: readString(record, "freshness", "dataGaps"),
  };
}

export function readThresholdNumber(
  bounds: { min?: number | null; max?: number | null } | undefined,
): string {
  if (!bounds) return "—";
  const parts: string[] = [];
  if (bounds.min !== undefined && bounds.min !== null) parts.push(`≥${bounds.min}`);
  if (bounds.max !== undefined && bounds.max !== null) parts.push(`≤${bounds.max}`);
  return parts.join(" / ") || "—";
}

export function parseBooleanFlag(record: Record<string, unknown>, key: string, path: string) {
  return record[key] === undefined ? false : readBoolean(record, key, path);
}
