import { describe, expect, it } from "vitest";

import { ContractValidationError } from "./validation";
import {
  parseDataset,
  parseDatasetVersion,
  parseExportJob,
  parseTrainingRequestRevision,
} from "./datasetParsers";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const DATASET = "22222222-2222-4222-8222-222222222222";
const LINEAGE = "33333333-3333-4333-8333-333333333333";
const USER = "44444444-4444-4444-8444-444444444444";

const SHA = `sha256:${"a".repeat(64)}`;

function datasetPayload() {
  return {
    dataset_id: DATASET,
    project_id: PROJECT,
    dataset_lineage_id: LINEAGE,
    code: "DS-001",
    name: "抓取数据集",
    description: null,
    purpose: "TRAINING",
    owner_id: USER,
    status: "DRAFT",
    current_version_id: null,
    tags: ["kitchen"],
    usage_policy: "TRAINING_ALLOWED",
    frozen_version_count: 0,
    allowed_actions: ["activate"],
    version: 1,
    created_at: "2026-11-09T00:00:00Z",
    updated_at: "2026-11-09T00:00:00Z",
  };
}

describe("阶段 4 响应解析", () => {
  it("解析 Dataset 并保留 allowed_actions 与版本", () => {
    const dataset = parseDataset(datasetPayload());
    expect(dataset.dataset_id).toBe(DATASET);
    expect(dataset.allowed_actions).toEqual(["activate"]);
    expect(dataset.version).toBe(1);
  });

  it("Dataset 缺少必填字段时抛契约错误", () => {
    const payload = datasetPayload();
    delete (payload as Record<string, unknown>).dataset_lineage_id;
    expect(() => parseDataset(payload)).toThrow(ContractValidationError);
  });

  it("解析 DatasetVersion 的策略引用与计数", () => {
    const version = parseDatasetVersion({
      dataset_version_id: "55555555-5555-4555-8555-555555555555",
      project_id: PROJECT,
      dataset_id: DATASET,
      dataset_lineage_id: LINEAGE,
      dataset_version: 3,
      status: "FROZEN",
      validity_status: "VALID",
      selection_rule_version: "ddp.selection/1.0.0",
      selection_filters: {},
      dedup_policy_ref: { policy_id: DATASET, policy_version: 1 },
      split_policy_ref: { policy_id: DATASET, policy_version: 2 },
      leakage_policy_ref: { policy_id: DATASET, policy_version: 3 },
      quality_policy_ref: { policy_id: DATASET, policy_version: 4 },
      candidate_count: 10,
      selected_count: 9,
      excluded_count: 1,
      duplicated_count: 0,
      item_count: 9,
      train_count: 7,
      validation_count: 1,
      test_count: 1,
      total_duration_ns: 1,
      total_bytes: 2,
      manifest_key: null,
      manifest_sha256: null,
      leakage_status: "PASS",
      build_attempt: 1,
      frozen_by: USER,
      frozen_at: "2026-11-09T01:00:00Z",
      supersedes_version_id: null,
      blocking_issues: [],
      warnings: [],
      allowed_actions: ["archive"],
      version: 5,
      created_at: "2026-11-09T00:00:00Z",
      updated_at: "2026-11-09T00:00:00Z",
    });
    expect(version.dataset_version).toBe(3);
    expect(version.dedup_policy_ref?.policy_version).toBe(1);
    expect(version.test_count).toBe(1);
  });

  it("解析 ExportJob 并识别 PARTIAL", () => {
    const job = parseExportJob({
      export_job_id: "66666666-6666-4666-8666-666666666666",
      project_id: PROJECT,
      dataset_version_id: "77777777-7777-4777-8777-777777777777",
      status: "PARTIAL",
      format: "LE_ROBOT",
      format_version: "ddp.lerobot/1.0.0",
      exporter_id: "lerobot-exporter",
      exporter_version: "1.0.0",
      mapping_version: "ddp.lerobot.mapping/1.0.0",
      output_prefix: "exports/1/",
      artifact_count: 3,
      total_bytes: 100,
      aggregate_sha256: null,
      attempt: 1,
      max_attempts: 3,
      verification_status: "UNVERIFIED",
      error_code: "export_partial",
      error_summary: "部分失败",
      allowed_actions: [],
      version: 2,
      created_at: "2026-11-09T00:00:00Z",
      updated_at: "2026-11-09T00:00:00Z",
      started_at: null,
      finished_at: null,
    });
    expect(job.status).toBe("PARTIAL");
    expect(job.verification_status).toBe("UNVERIFIED");
  });

  it("解析训练修订的 version 与 allowed_actions", () => {
    const revision = parseTrainingRequestRevision({
      revision_id: "88888888-8888-4888-8888-888888888888",
      training_request_id: "99999999-9999-4999-8999-999999999999",
      project_id: PROJECT,
      revision_no: 2,
      status: "DRAFT",
      snapshot: { business_goal: "训练抓取策略" },
      change_reason: null,
      approved_at: null,
      rejection_reason: null,
      revision_sha256: SHA,
      allowed_actions: ["submit"],
      version: 4,
      created_at: "2026-11-09T00:00:00Z",
    });
    expect(revision.allowed_actions).toEqual(["submit"]);
    expect(revision.version).toBe(4);
  });
});
