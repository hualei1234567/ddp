/** 阶段 4 响应解析器：外部数据进入 UI 前必须经过校验。 */

import type {
  Dataset,
  DatasetGrant,
  DatasetItem,
  DatasetManifest,
  DatasetVersion,
  DedupPolicy,
  ExportArtifactManifest,
  ExportArtifactDownload,
  ExportJob,
  ExternalTrainingReference,
  LeakagePolicy,
  LeakageReport,
  QualityPolicy,
  QualityReport,
  SelectionPreview,
  SplitPolicy,
  TrainingDelivery,
  TrainingRequest,
  TrainingRequestRevision,
} from "../types/dataset";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readBoolean,
  readInteger,
  readNumber,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readStringArray,
  readUuid,
} from "./validation";

function readCountMap(
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
  for (const [name, count] of Object.entries(map)) {
    if (typeof count !== "number" || !Number.isFinite(count)) {
      throw new ContractValidationError(`${path}.${key}.${name} 必须是数字`);
    }
    result[name] = count;
  }
  return result;
}

function readPolicyRef(
  record: Record<string, unknown>,
  key: string,
  path: string,
): { policy_id: string; version: number; policy_sha256: string } | null {
  const value = readOptionalRecord(record, key, path);
  if (value === null) {
    return null;
  }
  return {
    policy_id: readUuid(value, "policy_id", `${path}.${key}`),
    version: readPositiveInteger(value, "version", `${path}.${key}`),
    policy_sha256: readSha256Checksum(value, "policy_sha256", `${path}.${key}`),
  };
}

function readRecordArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown>[] {
  const value = record[key];
  if (value === undefined || value === null) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path}.${key} 必须是数组`);
  }
  return value.map((item, index) => asRecord(item, `${path}.${key}[${index}]`));
}

export function parseDataset(value: unknown): Dataset {
  const record = asRecord(value, "dataset");
  return {
    dataset_id: readUuid(record, "dataset_id", "dataset"),
    project_id: readUuid(record, "project_id", "dataset"),
    dataset_lineage_id: readUuid(record, "dataset_lineage_id", "dataset"),
    code: readString(record, "code", "dataset"),
    name: readString(record, "name", "dataset"),
    description: readOptionalString(record, "description", "dataset"),
    purpose: readString(record, "purpose", "dataset"),
    owner_id: readUuid(record, "owner_id", "dataset"),
    status: readString(record, "status", "dataset"),
    current_version_id: record.current_version_id
      ? readUuid(record, "current_version_id", "dataset.current_version_id")
      : null,
    tags: record.tags ? readStringArray(record, "tags", "dataset") : [],
    usage_policy: readString(record, "usage_policy", "dataset"),
    frozen_version_count: record.frozen_version_count
      ? readInteger(record, "frozen_version_count", "dataset")
      : 0,
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "dataset")
      : [],
    version: readPositiveInteger(record, "version", "dataset"),
    created_at: readString(record, "created_at", "dataset"),
    updated_at: readString(record, "updated_at", "dataset"),
  };
}

export function parseDatasetPage(value: unknown) {
  return parseCursorPage(value, "datasets", parseDataset);
}

export function parseDatasetVersion(value: unknown): DatasetVersion {
  const record = asRecord(value, "datasetVersion");
  return {
    dataset_version_id: readUuid(record, "dataset_version_id", "datasetVersion"),
    project_id: readUuid(record, "project_id", "datasetVersion"),
    dataset_id: readUuid(record, "dataset_id", "datasetVersion"),
    dataset_lineage_id: readUuid(record, "dataset_lineage_id", "datasetVersion"),
    dataset_version: readPositiveInteger(record, "dataset_version", "datasetVersion"),
    status: readString(record, "status", "datasetVersion"),
    validity_status: readString(record, "validity_status", "datasetVersion"),
    selection_rule_version: readString(record, "selection_rule_version", "datasetVersion"),
    selection_filters: readOptionalRecord(record, "selection_filters", "datasetVersion") ?? {},
    dedup_policy_ref: readPolicyRef(record, "dedup_policy_ref", "datasetVersion"),
    split_policy_ref: readPolicyRef(record, "split_policy_ref", "datasetVersion"),
    leakage_policy_ref: readPolicyRef(record, "leakage_policy_ref", "datasetVersion"),
    quality_policy_ref: readPolicyRef(record, "quality_policy_ref", "datasetVersion"),
    candidate_count: readInteger(record, "candidate_count", "datasetVersion"),
    selected_count: readInteger(record, "selected_count", "datasetVersion"),
    excluded_count: readInteger(record, "excluded_count", "datasetVersion"),
    duplicated_count: readInteger(record, "duplicated_count", "datasetVersion"),
    item_count: readInteger(record, "item_count", "datasetVersion"),
    train_count: readInteger(record, "train_count", "datasetVersion"),
    validation_count: readInteger(record, "validation_count", "datasetVersion"),
    test_count: readInteger(record, "test_count", "datasetVersion"),
    total_duration_ns: readOptionalNumber(record, "total_duration_ns", "datasetVersion"),
    total_bytes: readOptionalNumber(record, "total_bytes", "datasetVersion"),
    manifest_key: readOptionalString(record, "manifest_key", "datasetVersion"),
    manifest_sha256: record.manifest_sha256
      ? readSha256Checksum(record, "manifest_sha256", "datasetVersion")
      : null,
    leakage_status: readOptionalString(record, "leakage_status", "datasetVersion"),
    build_attempt: readInteger(record, "build_attempt", "datasetVersion"),
    frozen_by: record.frozen_by
      ? readUuid(record, "frozen_by", "datasetVersion.frozen_by")
      : null,
    frozen_at: readOptionalString(record, "frozen_at", "datasetVersion"),
    supersedes_version_id: record.supersedes_version_id
      ? readUuid(record, "supersedes_version_id", "datasetVersion.supersedes_version_id")
      : null,
    blocking_issues: record.blocking_issues
      ? readStringArray(record, "blocking_issues", "datasetVersion")
      : [],
    warnings: record.warnings ? readStringArray(record, "warnings", "datasetVersion") : [],
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "datasetVersion")
      : [],
    version: readPositiveInteger(record, "version", "datasetVersion"),
    created_at: readString(record, "created_at", "datasetVersion"),
    updated_at: readString(record, "updated_at", "datasetVersion"),
  };
}

export function parseDatasetVersionPage(value: unknown) {
  return parseCursorPage(value, "datasetVersions", parseDatasetVersion);
}

export function parseDatasetItem(value: unknown): DatasetItem {
  const record = asRecord(value, "datasetItem");
  return {
    dataset_item_id: readUuid(record, "dataset_item_id", "datasetItem"),
    project_id: readUuid(record, "project_id", "datasetItem"),
    dataset_version_id: readUuid(record, "dataset_version_id", "datasetItem"),
    episode_id: readUuid(record, "episode_id", "datasetItem"),
    episode_version: readInteger(record, "episode_version", "datasetItem"),
    episode_lineage_id: readUuid(record, "episode_lineage_id", "datasetItem"),
    split: readString(record, "split", "datasetItem"),
    sample_role: readString(record, "sample_role", "datasetItem"),
    weight: readNumber(record, "weight", "datasetItem"),
    selected_reason: readOptionalString(record, "selected_reason", "datasetItem"),
    item_sha256: readSha256Checksum(record, "item_sha256", "datasetItem"),
    created_at: readString(record, "created_at", "datasetItem"),
  };
}

export function parseDatasetItemPage(value: unknown) {
  return parseCursorPage(value, "datasetItems", parseDatasetItem);
}

function parsePolicyBase(record: Record<string, unknown>, path: string) {
  return {
    project_id: readUuid(record, "project_id", path),
    dataset_id: readUuid(record, "dataset_id", path),
    name: readOptionalString(record, "name", path),
    version: readPositiveInteger(record, "version", path),
    policy_sha256: readSha256Checksum(record, "policy_sha256", path),
    created_at: readString(record, "created_at", path),
  };
}

export function parseDedupPolicy(value: unknown): DedupPolicy {
  const record = asRecord(value, "dedupPolicy");
  return {
    ...parsePolicyBase(record, "dedupPolicy"),
    dedup_policy_id: readUuid(record, "dedup_policy_id", "dedupPolicy"),
    exact_keys: readStringArray(record, "exact_keys", "dedupPolicy"),
    near_duplicate_enabled: readBoolean(record, "near_duplicate_enabled", "dedupPolicy"),
    near_duplicate_enforcement: readString(
      record,
      "near_duplicate_enforcement",
      "dedupPolicy",
    ),
    retain_rule: readString(record, "retain_rule", "dedupPolicy"),
  };
}

export function parseDedupPolicyPage(value: unknown) {
  return parseCursorPage(value, "dedupPolicies", parseDedupPolicy);
}

export function parseSplitPolicy(value: unknown): SplitPolicy {
  const record = asRecord(value, "splitPolicy");
  return {
    ...parsePolicyBase(record, "splitPolicy"),
    split_policy_id: readUuid(record, "split_policy_id", "splitPolicy"),
    strategy: readString(record, "strategy", "splitPolicy"),
    train_ratio: readNumber(record, "train_ratio", "splitPolicy"),
    validation_ratio: readNumber(record, "validation_ratio", "splitPolicy"),
    test_ratio: readNumber(record, "test_ratio", "splitPolicy"),
    seed: readInteger(record, "seed", "splitPolicy"),
    group_by: readStringArray(record, "group_by", "splitPolicy"),
    empty_split_policy: readString(record, "empty_split_policy", "splitPolicy"),
  };
}

export function parseSplitPolicyPage(value: unknown) {
  return parseCursorPage(value, "splitPolicies", parseSplitPolicy);
}

export function parseLeakagePolicy(value: unknown): LeakagePolicy {
  const record = asRecord(value, "leakagePolicy");
  return {
    ...parsePolicyBase(record, "leakagePolicy"),
    leakage_policy_id: readUuid(record, "leakage_policy_id", "leakagePolicy"),
    hard_rules: readStringArray(record, "hard_rules", "leakagePolicy"),
    soft_rules: readStringArray(record, "soft_rules", "leakagePolicy"),
    waiver_allowed_rules: record.waiver_allowed_rules
      ? readStringArray(record, "waiver_allowed_rules", "leakagePolicy")
      : [],
  };
}

export function parseLeakagePolicyPage(value: unknown) {
  return parseCursorPage(value, "leakagePolicies", parseLeakagePolicy);
}

export function parseQualityPolicy(value: unknown): QualityPolicy {
  const record = asRecord(value, "qualityPolicy");
  return {
    ...parsePolicyBase(record, "qualityPolicy"),
    quality_policy_id: readUuid(record, "quality_policy_id", "qualityPolicy"),
    min_selected_count: readInteger(record, "min_selected_count", "qualityPolicy"),
    allow_warn_quality: readBoolean(record, "allow_warn_quality", "qualityPolicy"),
    require_leakage_pass: readBoolean(record, "require_leakage_pass", "qualityPolicy"),
  };
}

export function parseQualityPolicyPage(value: unknown) {
  return parseCursorPage(value, "qualityPolicies", parseQualityPolicy);
}

export function parseSelectionPreview(value: unknown): SelectionPreview {
  const record = asRecord(value, "selectionPreview");
  return {
    candidate_count: readInteger(record, "candidate_count", "selectionPreview"),
    selected_count: readInteger(record, "selected_count", "selectionPreview"),
    excluded_count: readInteger(record, "excluded_count", "selectionPreview"),
    duplicated_count: readInteger(record, "duplicated_count", "selectionPreview"),
    split_counts: readCountMap(record, "split_counts", "selectionPreview"),
    split_preview_status: readString(record, "split_preview_status", "selectionPreview"),
    exclusion_summary: readCountMap(record, "exclusion_summary", "selectionPreview"),
    warnings: record.warnings ? readStringArray(record, "warnings", "selectionPreview") : [],
    blocking_issues: record.blocking_issues
      ? readStringArray(record, "blocking_issues", "selectionPreview")
      : [],
    preview_sha256: readSha256Checksum(record, "preview_sha256", "selectionPreview"),
  };
}

export function parseQualityReport(value: unknown): QualityReport {
  const record = asRecord(value, "qualityReport");
  return {
    quality_report_id: readUuid(record, "quality_report_id", "qualityReport"),
    status: readString(record, "status", "qualityReport"),
    candidate_count: readInteger(record, "candidate_count", "qualityReport"),
    selected_count: readInteger(record, "selected_count", "qualityReport"),
    excluded_count: readInteger(record, "excluded_count", "qualityReport"),
    duplicated_count: readInteger(record, "duplicated_count", "qualityReport"),
    split_counts: readCountMap(record, "split_counts", "qualityReport"),
    warnings: record.warnings ? readStringArray(record, "warnings", "qualityReport") : [],
    blocking_issues: record.blocking_issues
      ? readStringArray(record, "blocking_issues", "qualityReport")
      : [],
    report_sha256: readSha256Checksum(record, "report_sha256", "qualityReport"),
    created_at: readString(record, "created_at", "qualityReport"),
  };
}

export function parseLeakageReport(value: unknown): LeakageReport {
  const record = asRecord(value, "leakageReport");
  return {
    leakage_report_id: readUuid(record, "leakage_report_id", "leakageReport"),
    leakage_status: readString(record, "leakage_status", "leakageReport"),
    checked_rule_count: readInteger(record, "checked_rule_count", "leakageReport"),
    violation_count: readInteger(record, "violation_count", "leakageReport"),
    hard_violation_count: readInteger(record, "hard_violation_count", "leakageReport"),
    soft_violation_count: readInteger(record, "soft_violation_count", "leakageReport"),
    violations: readRecordArray(record, "violations", "leakageReport").map((item) => ({
      rule: readString(item, "rule", "leakageReport.violation"),
      severity: readString(item, "severity", "leakageReport.violation"),
      item_id: readOptionalString(item, "item_id", "leakageReport.violation"),
      detail: readOptionalString(item, "detail", "leakageReport.violation"),
    })),
    waivers: readRecordArray(record, "waivers", "leakageReport"),
    unknown_reason: readOptionalString(record, "unknown_reason", "leakageReport"),
    report_sha256: readSha256Checksum(record, "report_sha256", "leakageReport"),
    checked_at: readString(record, "checked_at", "leakageReport"),
  };
}

export function parseDatasetManifest(value: unknown): DatasetManifest {
  const record = asRecord(value, "manifest");
  return {
    dataset_version_id: readUuid(record, "dataset_version_id", "manifest"),
    dataset_version: readPositiveInteger(record, "dataset_version", "manifest"),
    project_id: readUuid(record, "project_id", "manifest"),
    split_counts: readCountMap(record, "split_counts", "manifest"),
    items: readRecordArray(record, "items", "manifest"),
    quality_report_sha256: readString(record, "quality_report_sha256", "manifest"),
    leakage_report_sha256: readString(record, "leakage_report_sha256", "manifest"),
    manifest_sha256: readSha256Checksum(record, "manifest_sha256", "manifest"),
    size_bytes: readOptionalNumber(record, "size_bytes", "manifest"),
  };
}

export function parseDatasetGrant(value: unknown): DatasetGrant {
  const record = asRecord(value, "datasetGrant");
  return {
    grant_id: readUuid(record, "grant_id", "datasetGrant"),
    project_id: readUuid(record, "project_id", "datasetGrant"),
    dataset_id: readUuid(record, "dataset_id", "datasetGrant"),
    dataset_version_id: record.dataset_version_id
      ? readUuid(record, "dataset_version_id", "datasetGrant.dataset_version_id")
      : null,
    grantee_project_id: readUuid(record, "grantee_project_id", "datasetGrant"),
    grantee_team: readOptionalString(record, "grantee_team", "datasetGrant"),
    scope: readStringArray(record, "scope", "datasetGrant"),
    status: readString(record, "status", "datasetGrant"),
    reason: readString(record, "reason", "datasetGrant"),
    approved_at: readOptionalString(record, "approved_at", "datasetGrant"),
    expires_at: readString(record, "expires_at", "datasetGrant"),
    revoked_at: readOptionalString(record, "revoked_at", "datasetGrant"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "datasetGrant")
      : [],
    version: readPositiveInteger(record, "version", "datasetGrant"),
  };
}

export function parseDatasetGrantPage(value: unknown) {
  return parseCursorPage(value, "datasetGrants", parseDatasetGrant);
}

export function parseExportJob(value: unknown): ExportJob {
  const record = asRecord(value, "exportJob");
  return {
    export_job_id: readUuid(record, "export_job_id", "exportJob"),
    project_id: readUuid(record, "project_id", "exportJob"),
    dataset_version_id: readUuid(record, "dataset_version_id", "exportJob"),
    status: readString(record, "status", "exportJob"),
    format: readString(record, "format", "exportJob"),
    format_version: readString(record, "format_version", "exportJob"),
    exporter_id: readString(record, "exporter_id", "exportJob"),
    exporter_version: readString(record, "exporter_version", "exportJob"),
    mapping_version: readString(record, "mapping_version", "exportJob"),
    output_prefix: readString(record, "output_prefix", "exportJob"),
    artifact_count: record.artifact_count ? readInteger(record, "artifact_count", "exportJob") : 0,
    total_bytes: record.total_bytes ? readInteger(record, "total_bytes", "exportJob") : 0,
    aggregate_sha256: record.aggregate_sha256
      ? readSha256Checksum(record, "aggregate_sha256", "exportJob")
      : null,
    attempt: readInteger(record, "attempt", "exportJob"),
    max_attempts: readInteger(record, "max_attempts", "exportJob"),
    verification_status: readString(record, "verification_status", "exportJob"),
    error_code: readOptionalString(record, "error_code", "exportJob"),
    error_summary: readOptionalString(record, "error_summary", "exportJob"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "exportJob")
      : [],
    version: readPositiveInteger(record, "version", "exportJob"),
    created_at: readString(record, "created_at", "exportJob"),
    updated_at: readString(record, "updated_at", "exportJob"),
    started_at: readOptionalString(record, "started_at", "exportJob"),
    finished_at: readOptionalString(record, "finished_at", "exportJob"),
  };
}

export function parseExportJobPage(value: unknown) {
  return parseCursorPage(value, "exportJobs", parseExportJob);
}

export function parseExportArtifactManifest(value: unknown): ExportArtifactManifest {
  const record = asRecord(value, "artifactManifest");
  return {
    export_job_id: readUuid(record, "export_job_id", "artifactManifest"),
    aggregate_sha256: record.aggregate_sha256
      ? readSha256Checksum(record, "aggregate_sha256", "artifactManifest")
      : null,
    artifacts: readRecordArray(record, "artifacts", "artifactManifest").map((item) => ({
      path: readString(item, "path", "artifactManifest.artifact"),
      sha256: readSha256Checksum(item, "sha256", "artifactManifest.artifact"),
      size_bytes: readInteger(item, "size_bytes", "artifactManifest.artifact"),
    })),
  };
}

export function parseExportArtifactDownload(value: unknown): ExportArtifactDownload {
  const record = asRecord(value, "artifactDownload");
  return {
    path: readString(record, "path", "artifactDownload"),
    sha256: readSha256Checksum(record, "sha256", "artifactDownload"),
    size_bytes: readInteger(record, "size_bytes", "artifactDownload"),
    download_url: readString(record, "download_url", "artifactDownload"),
    download_url_expires_at: readString(record, "download_url_expires_at", "artifactDownload"),
  };
}

export function parseTrainingRequest(value: unknown): TrainingRequest {
  const record = asRecord(value, "trainingRequest");
  return {
    training_request_id: readUuid(record, "training_request_id", "trainingRequest"),
    project_id: readUuid(record, "project_id", "trainingRequest"),
    code: readString(record, "code", "trainingRequest"),
    title: readString(record, "title", "trainingRequest"),
    status: readString(record, "status", "trainingRequest"),
    current_revision_id: record.current_revision_id
      ? readUuid(record, "current_revision_id", "trainingRequest.current_revision_id")
      : null,
    current_revision_no: record.current_revision_no
      ? readInteger(record, "current_revision_no", "trainingRequest")
      : null,
    priority: readString(record, "priority", "trainingRequest"),
    owner_id: readUuid(record, "owner_id", "trainingRequest"),
    blocked_reason: readOptionalString(record, "blocked_reason", "trainingRequest"),
    cancel_reason: readOptionalString(record, "cancel_reason", "trainingRequest"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "trainingRequest")
      : [],
    version: readPositiveInteger(record, "version", "trainingRequest"),
    created_at: readString(record, "created_at", "trainingRequest"),
    updated_at: readString(record, "updated_at", "trainingRequest"),
  };
}

export function parseTrainingRequestPage(value: unknown) {
  return parseCursorPage(value, "trainingRequests", parseTrainingRequest);
}

export function parseTrainingRequestRevision(value: unknown): TrainingRequestRevision {
  const record = asRecord(value, "trainingRevision");
  return {
    revision_id: readUuid(record, "revision_id", "trainingRevision"),
    training_request_id: readUuid(record, "training_request_id", "trainingRevision"),
    project_id: readUuid(record, "project_id", "trainingRevision"),
    revision_no: readPositiveInteger(record, "revision_no", "trainingRevision"),
    status: readString(record, "status", "trainingRevision"),
    snapshot: asRecord(record.snapshot, "trainingRevision.snapshot"),
    change_reason: readOptionalString(record, "change_reason", "trainingRevision"),
    approved_at: readOptionalString(record, "approved_at", "trainingRevision"),
    rejection_reason: readOptionalString(record, "rejection_reason", "trainingRevision"),
    revision_sha256: readSha256Checksum(record, "revision_sha256", "trainingRevision"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "trainingRevision")
      : [],
    version: readPositiveInteger(record, "version", "trainingRevision"),
    created_at: readString(record, "created_at", "trainingRevision"),
  };
}

export function parseTrainingRequestRevisionPage(value: unknown) {
  return parseCursorPage(value, "trainingRevisions", parseTrainingRequestRevision);
}

export function parseTrainingDelivery(value: unknown): TrainingDelivery {
  const record = asRecord(value, "trainingDelivery");
  return {
    training_delivery_id: readUuid(record, "training_delivery_id", "trainingDelivery"),
    project_id: readUuid(record, "project_id", "trainingDelivery"),
    training_request_id: readUuid(record, "training_request_id", "trainingDelivery"),
    training_request_revision_id: readUuid(
      record,
      "training_request_revision_id",
      "trainingDelivery",
    ),
    dataset_version_id: readUuid(record, "dataset_version_id", "trainingDelivery"),
    export_job_id: readUuid(record, "export_job_id", "trainingDelivery"),
    delivery_note_no: readString(record, "delivery_note_no", "trainingDelivery"),
    recipient_id: readUuid(record, "recipient_id", "trainingDelivery"),
    recipient_team: readOptionalString(record, "recipient_team", "trainingDelivery"),
    artifact_manifest_sha256: readSha256Checksum(
      record,
      "artifact_manifest_sha256",
      "trainingDelivery",
    ),
    aggregate_sha256: readSha256Checksum(record, "aggregate_sha256", "trainingDelivery"),
    item_count: readInteger(record, "item_count", "trainingDelivery"),
    split_counts: readCountMap(record, "split_counts", "trainingDelivery"),
    quality_report_ref: readOptionalString(record, "quality_report_ref", "trainingDelivery"),
    status: readString(record, "status", "trainingDelivery"),
    delivered_at: readOptionalString(record, "delivered_at", "trainingDelivery"),
    accepted_at: readOptionalString(record, "accepted_at", "trainingDelivery"),
    acceptance_opinion: readOptionalString(record, "acceptance_opinion", "trainingDelivery"),
    rejection_reason: readOptionalString(record, "rejection_reason", "trainingDelivery"),
    rework_ref: readOptionalString(record, "rework_ref", "trainingDelivery"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "trainingDelivery")
      : [],
    version: readPositiveInteger(record, "version", "trainingDelivery"),
    created_at: readString(record, "created_at", "trainingDelivery"),
  };
}

export function parseTrainingDeliveryPage(value: unknown) {
  return parseCursorPage(value, "trainingDeliveries", parseTrainingDelivery);
}

export function parseExternalTrainingReference(value: unknown): ExternalTrainingReference {
  const record = asRecord(value, "externalReference");
  return {
    external_ref_id: readUuid(record, "external_ref_id", "externalReference"),
    project_id: readUuid(record, "project_id", "externalReference"),
    adapter_id: readString(record, "adapter_id", "externalReference"),
    adapter_version: readString(record, "adapter_version", "externalReference"),
    training_delivery_id: readUuid(record, "training_delivery_id", "externalReference"),
    training_request_revision_id: readUuid(
      record,
      "training_request_revision_id",
      "externalReference",
    ),
    dataset_version_id: readUuid(record, "dataset_version_id", "externalReference"),
    external_run_id: readString(record, "external_run_id", "externalReference"),
    external_run_url: readOptionalString(record, "external_run_url", "externalReference"),
    external_status: readString(record, "external_status", "externalReference"),
    status_stale: record.status_stale
      ? readBoolean(record, "status_stale", "externalReference")
      : false,
    model_ref: readOptionalRecord(record, "model_ref", "externalReference"),
    evaluation_ref: readOptionalRecord(record, "evaluation_ref", "externalReference"),
    correlation_id: readString(record, "correlation_id", "externalReference"),
    idempotency_key: readString(record, "idempotency_key", "externalReference"),
    error_code: readOptionalString(record, "error_code", "externalReference"),
    error_summary: readOptionalString(record, "error_summary", "externalReference"),
    allowed_actions: record.allowed_actions
      ? readStringArray(record, "allowed_actions", "externalReference")
      : [],
    version: readPositiveInteger(record, "version", "externalReference"),
    submitted_at: readString(record, "submitted_at", "externalReference"),
    last_synced_at: readString(record, "last_synced_at", "externalReference"),
  };
}

export function parseExternalTrainingReferencePage(value: unknown) {
  return parseCursorPage(value, "externalReferences", parseExternalTrainingReference);
}
