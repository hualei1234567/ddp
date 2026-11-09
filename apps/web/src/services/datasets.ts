/** 阶段 4 数据集、策略与导出 API。 */

import type { CursorPage } from "../types/api";
import type {
  Dataset,
  DatasetGrant,
  DatasetItem,
  DatasetManifest,
  DatasetVersion,
  DedupPolicy,
  ExportArtifactDownload,
  ExportArtifactManifest,
  ExportJob,
  LeakagePolicy,
  LeakageReport,
  QualityPolicy,
  QualityReport,
  SelectionPreview,
  SplitPolicy,
} from "../types/dataset";
import { apiClient, type ApiClient } from "./api";
import {
  parseDataset,
  parseDatasetGrant,
  parseDatasetGrantPage,
  parseDatasetItemPage,
  parseDatasetManifest,
  parseDatasetPage,
  parseDatasetVersion,
  parseDatasetVersionPage,
  parseDedupPolicy,
  parseDedupPolicyPage,
  parseExportArtifactDownload,
  parseExportArtifactManifest,
  parseExportJob,
  parseExportJobPage,
  parseLeakagePolicy,
  parseLeakagePolicyPage,
  parseLeakageReport,
  parseQualityPolicy,
  parseQualityPolicyPage,
  parseQualityReport,
  parseSelectionPreview,
  parseSplitPolicy,
  parseSplitPolicyPage,
} from "./datasetParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface WriteOptions {
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface DatasetListOptions extends PageRequestOptions {
  status?: string;
  purpose?: string;
  usage_policy?: string;
}

export interface VersionListOptions extends PageRequestOptions {
  status?: string;
  validity_status?: string;
  leakage_status?: string;
}

export interface ExportListOptions extends PageRequestOptions {
  status?: string;
  format?: string;
}

export interface DatasetCreateInput {
  code: string;
  name: string;
  purpose: string;
  owner_id: string;
  usage_policy: string;
  description?: string | null;
  tags?: string[];
  default_dedup_policy_id?: string | null;
  default_split_policy_id?: string | null;
  default_leakage_policy_id?: string | null;
}

export type DatasetPatchInput = Partial<
  Pick<
    DatasetCreateInput,
    | "name"
    | "description"
    | "purpose"
    | "tags"
    | "usage_policy"
    | "default_dedup_policy_id"
    | "default_split_policy_id"
    | "default_leakage_policy_id"
  >
>;

export interface VersionCreateInput {
  selection_rule_version: string;
  selection_filters?: Record<string, unknown>;
  dedup_policy_id: string;
  dedup_policy_version: number;
  split_policy_id: string;
  split_policy_version: number;
  leakage_policy_id: string;
  leakage_policy_version: number;
  quality_policy_id: string;
  quality_policy_version: number;
  supersedes_version_id?: string | null;
  build_now?: boolean;
}

export interface PreviewInput {
  selection_filters?: Record<string, unknown>;
  dedup_policy_id: string;
  dedup_policy_version: number;
  split_policy_id: string;
  split_policy_version: number;
  leakage_policy_id: string;
  leakage_policy_version: number;
  quality_policy_id?: string | null;
  quality_policy_version?: number | null;
  sample_limit?: number;
}

export interface DedupPolicyInput {
  name?: string | null;
  exact_keys: string[];
  near_duplicate_enabled?: boolean;
  near_duplicate_enforcement?: string;
  retain_rule?: string;
}

export interface SplitPolicyInput {
  name?: string | null;
  strategy: string;
  train_ratio?: number;
  validation_ratio?: number;
  test_ratio?: number;
  seed: number;
  group_by: string[];
  empty_split_policy?: string;
}

export interface LeakagePolicyInput {
  name?: string | null;
  hard_rules: string[];
  soft_rules?: string[];
  waiver_allowed_rules?: string[];
}

export interface QualityPolicyInput {
  name?: string | null;
  min_selected_count?: number;
  allow_warn_quality?: boolean;
  require_leakage_pass?: boolean;
}

export interface GrantCreateInput {
  grantee_project_id: string;
  scope: string[];
  reason: string;
  expires_at: string;
  dataset_version_id?: string | null;
  grantee_team?: string | null;
}

export interface ExportCreateInput {
  dataset_version_id: string;
  format: string;
  format_version: string;
  exporter_id: string;
  exporter_version: string;
  mapping_version: string;
  config?: Record<string, unknown>;
}

function createHeaders(options?: CreateOptions): Record<string, string> {
  return { "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey() };
}

function writeHeaders(options: WriteOptions): Record<string, string> {
  return {
    "If-Match": formatIfMatch(options.version),
    "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
  };
}

export interface DatasetApi {
  list(projectId: string, options?: DatasetListOptions): Promise<CursorPage<Dataset>>;
  create(projectId: string, input: DatasetCreateInput, options?: CreateOptions): Promise<Dataset>;
  get(projectId: string, datasetId: string, options?: { signal?: AbortSignal }): Promise<Dataset>;
  update(
    projectId: string,
    datasetId: string,
    input: DatasetPatchInput,
    options: WriteOptions,
  ): Promise<Dataset>;
  act(
    projectId: string,
    datasetId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<Dataset>;
  listVersions(
    projectId: string,
    datasetId: string,
    options?: VersionListOptions,
  ): Promise<CursorPage<DatasetVersion>>;
  createVersion(
    projectId: string,
    datasetId: string,
    input: VersionCreateInput,
    options?: CreateOptions,
  ): Promise<DatasetVersion>;
  getVersion(
    projectId: string,
    datasetId: string,
    versionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DatasetVersion>;
  actVersion(
    projectId: string,
    datasetId: string,
    versionId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<DatasetVersion>;
  listItems(
    projectId: string,
    datasetId: string,
    versionId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<DatasetItem>>;
  previewSelection(
    projectId: string,
    datasetId: string,
    versionId: string,
    input: PreviewInput,
    options?: { signal?: AbortSignal },
  ): Promise<SelectionPreview>;
  getQualityReport(
    projectId: string,
    datasetId: string,
    versionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<QualityReport>;
  getLeakageReport(
    projectId: string,
    datasetId: string,
    versionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<LeakageReport>;
  getManifest(
    projectId: string,
    datasetId: string,
    versionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DatasetManifest>;
  listDedupPolicies(
    projectId: string,
    datasetId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<DedupPolicy>>;
  createDedupPolicy(
    projectId: string,
    datasetId: string,
    input: DedupPolicyInput,
    options?: CreateOptions,
  ): Promise<DedupPolicy>;
  getDedupPolicy(
    projectId: string,
    datasetId: string,
    policyId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DedupPolicy>;
  listSplitPolicies(
    projectId: string,
    datasetId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<SplitPolicy>>;
  createSplitPolicy(
    projectId: string,
    datasetId: string,
    input: SplitPolicyInput,
    options?: CreateOptions,
  ): Promise<SplitPolicy>;
  getSplitPolicy(
    projectId: string,
    datasetId: string,
    policyId: string,
    options?: { signal?: AbortSignal },
  ): Promise<SplitPolicy>;
  listLeakagePolicies(
    projectId: string,
    datasetId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<LeakagePolicy>>;
  createLeakagePolicy(
    projectId: string,
    datasetId: string,
    input: LeakagePolicyInput,
    options?: CreateOptions,
  ): Promise<LeakagePolicy>;
  getLeakagePolicy(
    projectId: string,
    datasetId: string,
    policyId: string,
    options?: { signal?: AbortSignal },
  ): Promise<LeakagePolicy>;
  listQualityPolicies(
    projectId: string,
    datasetId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<QualityPolicy>>;
  createQualityPolicy(
    projectId: string,
    datasetId: string,
    input: QualityPolicyInput,
    options?: CreateOptions,
  ): Promise<QualityPolicy>;
  getQualityPolicy(
    projectId: string,
    datasetId: string,
    policyId: string,
    options?: { signal?: AbortSignal },
  ): Promise<QualityPolicy>;
  listGrants(
    projectId: string,
    datasetId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<DatasetGrant>>;
  createGrant(
    projectId: string,
    datasetId: string,
    input: GrantCreateInput,
    options?: CreateOptions,
  ): Promise<DatasetGrant>;
  actGrant(
    projectId: string,
    datasetId: string,
    grantId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<DatasetGrant>;
  listExports(projectId: string, options?: ExportListOptions): Promise<CursorPage<ExportJob>>;
  createExport(
    projectId: string,
    input: ExportCreateInput,
    options?: CreateOptions,
  ): Promise<ExportJob>;
  getExport(
    projectId: string,
    exportJobId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ExportJob>;
  actExport(
    projectId: string,
    exportJobId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<ExportJob>;
  getExportArtifacts(
    projectId: string,
    exportJobId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ExportArtifactManifest>;
  getExportArtifactDownload(
    projectId: string,
    exportJobId: string,
    path: string,
    options?: { signal?: AbortSignal },
  ): Promise<ExportArtifactDownload>;
}

export function createDatasetApi(client: ApiClient): DatasetApi {
  const datasetPath = (projectId: string, datasetId: string, suffix = "") =>
    projectPath(
      projectId,
      `/datasets/${requireIdentifier(datasetId, "dataset_id")}${suffix}`,
    );
  const versionPath = (
    projectId: string,
    datasetId: string,
    versionId: string,
    suffix = "",
  ) =>
    datasetPath(
      projectId,
      datasetId,
      `/versions/${requireIdentifier(versionId, "version_id")}${suffix}`,
    );

  return {
    async list(projectId, options) {
      return client.get(projectPath(projectId, "/datasets"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          purpose: options?.purpose,
          usage_policy: options?.usage_policy,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseDatasetPage(value), projectId, "数据集列表"),
      });
    },
    async create(projectId, input, options) {
      return client.request(projectPath(projectId, "/datasets"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseDataset(value), projectId, "数据集"),
      });
    },
    async get(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseDataset(value), projectId, "数据集"),
      });
    },
    async update(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId), {
        method: "PATCH",
        signal: options.signal,
        headers: writeHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseDataset(value), projectId, "数据集"),
      });
    },
    async act(projectId, datasetId, action, options) {
      return client.request(
        datasetPath(projectId, datasetId, `/actions/${requireIdentifier(action, "action")}`),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseDataset(value), projectId, "数据集"),
        },
      );
    },
    async listVersions(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/versions"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          validity_status: options?.validity_status,
          leakage_status: options?.leakage_status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseDatasetVersionPage(value), projectId, "数据集版本列表"),
      });
    },
    async createVersion(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/versions"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseDatasetVersion(value), projectId, "数据集版本"),
      });
    },
    async getVersion(projectId, datasetId, versionId, options) {
      return client.get(versionPath(projectId, datasetId, versionId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseDatasetVersion(value), projectId, "数据集版本"),
      });
    },
    async actVersion(projectId, datasetId, versionId, action, options) {
      return client.request(
        versionPath(
          projectId,
          datasetId,
          versionId,
          `/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseDatasetVersion(value), projectId, "数据集版本"),
        },
      );
    },
    async listItems(projectId, datasetId, versionId, options) {
      return client.get(versionPath(projectId, datasetId, versionId, "/items"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseDatasetItemPage(value), projectId, "数据集条目列表"),
      });
    },
    async previewSelection(projectId, datasetId, versionId, input, options) {
      return client.request(
        versionPath(projectId, datasetId, versionId, "/selection-preview"),
        {
          method: "POST",
          signal: options?.signal,
          headers: createHeaders(),
          body: input,
          validate: (value) => parseSelectionPreview(value),
        },
      );
    },
    async getQualityReport(projectId, datasetId, versionId, options) {
      return client.get(versionPath(projectId, datasetId, versionId, "/quality-report"), {
        signal: options?.signal,
        validate: (value) => parseQualityReport(value),
      });
    },
    async getLeakageReport(projectId, datasetId, versionId, options) {
      return client.get(versionPath(projectId, datasetId, versionId, "/leakage-report"), {
        signal: options?.signal,
        validate: (value) => parseLeakageReport(value),
      });
    },
    async getManifest(projectId, datasetId, versionId, options) {
      return client.get(versionPath(projectId, datasetId, versionId, "/manifest"), {
        signal: options?.signal,
        validate: (value) => parseDatasetManifest(value),
      });
    },
    async listDedupPolicies(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/dedup-policies"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseDedupPolicyPage(value), projectId, "去重策略列表"),
      });
    },
    async createDedupPolicy(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/dedup-policies"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => parseDedupPolicy(value),
      });
    },
    async getDedupPolicy(projectId, datasetId, policyId, options) {
      return client.get(
        datasetPath(
          projectId,
          datasetId,
          `/dedup-policies/${requireIdentifier(policyId, "policy_id")}`,
        ),
        { signal: options?.signal, validate: (value) => parseDedupPolicy(value) },
      );
    },
    async listSplitPolicies(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/split-policies"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseSplitPolicyPage(value), projectId, "划分策略列表"),
      });
    },
    async createSplitPolicy(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/split-policies"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => parseSplitPolicy(value),
      });
    },
    async getSplitPolicy(projectId, datasetId, policyId, options) {
      return client.get(
        datasetPath(
          projectId,
          datasetId,
          `/split-policies/${requireIdentifier(policyId, "policy_id")}`,
        ),
        { signal: options?.signal, validate: (value) => parseSplitPolicy(value) },
      );
    },
    async listLeakagePolicies(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/leakage-policies"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseLeakagePolicyPage(value), projectId, "泄漏策略列表"),
      });
    },
    async createLeakagePolicy(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/leakage-policies"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => parseLeakagePolicy(value),
      });
    },
    async getLeakagePolicy(projectId, datasetId, policyId, options) {
      return client.get(
        datasetPath(
          projectId,
          datasetId,
          `/leakage-policies/${requireIdentifier(policyId, "policy_id")}`,
        ),
        { signal: options?.signal, validate: (value) => parseLeakagePolicy(value) },
      );
    },
    async listQualityPolicies(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/quality-policies"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseQualityPolicyPage(value), projectId, "质量策略列表"),
      });
    },
    async createQualityPolicy(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/quality-policies"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => parseQualityPolicy(value),
      });
    },
    async getQualityPolicy(projectId, datasetId, policyId, options) {
      return client.get(
        datasetPath(
          projectId,
          datasetId,
          `/quality-policies/${requireIdentifier(policyId, "policy_id")}`,
        ),
        { signal: options?.signal, validate: (value) => parseQualityPolicy(value) },
      );
    },
    async listGrants(projectId, datasetId, options) {
      return client.get(datasetPath(projectId, datasetId, "/grants"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseDatasetGrantPage(value), projectId, "数据集授权列表"),
      });
    },
    async createGrant(projectId, datasetId, input, options) {
      return client.request(datasetPath(projectId, datasetId, "/grants"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => parseDatasetGrant(value),
      });
    },
    async actGrant(projectId, datasetId, grantId, action, options) {
      return client.request(
        datasetPath(
          projectId,
          datasetId,
          `/grants/${requireIdentifier(grantId, "grant_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) => parseDatasetGrant(value),
        },
      );
    },
    async listExports(projectId, options) {
      return client.get(projectPath(projectId, "/exports"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          format: options?.format,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseExportJobPage(value), projectId, "导出任务列表"),
      });
    },
    async createExport(projectId, input, options) {
      return client.request(projectPath(projectId, "/exports"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseExportJob(value), projectId, "导出任务"),
      });
    },
    async getExport(projectId, exportJobId, options) {
      return client.get(
        projectPath(projectId, `/exports/${requireIdentifier(exportJobId, "export_job_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseExportJob(value), projectId, "导出任务"),
        },
      );
    },
    async actExport(projectId, exportJobId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/exports/${requireIdentifier(exportJobId, "export_job_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseExportJob(value), projectId, "导出任务"),
        },
      );
    },
    async getExportArtifacts(projectId, exportJobId, options) {
      return client.get(
        projectPath(
          projectId,
          `/exports/${requireIdentifier(exportJobId, "export_job_id")}/artifacts`,
        ),
        { signal: options?.signal, validate: (value) => parseExportArtifactManifest(value) },
      );
    },
    async getExportArtifactDownload(projectId, exportJobId, path, options) {
      const encoded = path
        .split("/")
        .map((segment) => encodeURIComponent(segment))
        .join("/");
      return client.get(
        projectPath(
          projectId,
          `/exports/${requireIdentifier(exportJobId, "export_job_id")}/artifacts/${encoded}/download`,
        ),
        { signal: options?.signal, validate: (value) => parseExportArtifactDownload(value) },
      );
    },
  };
}

export const datasetApi = createDatasetApi(apiClient);
