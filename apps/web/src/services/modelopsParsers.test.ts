import { describe, expect, it } from "vitest";

import { ContractValidationError } from "./validation";
import {
  parseEvaluation,
  parseFeedback,
  parseGapSummary,
  parseModel,
  parseModelVersion,
  parseRequirement,
} from "./modelopsParsers";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const MODEL = "22222222-2222-4222-8222-222222222222";
const VERSION = "33333333-3333-4333-8333-333333333333";
const USER = "44444444-4444-4444-8444-444444444444";
const DATASET_VERSION = "55555555-5555-4555-8555-555555555555";
const SHA = `sha256:${"a".repeat(64)}`;

function modelPayload() {
  return {
    model_id: MODEL,
    project_id: PROJECT,
    code: "ACT-PICK-001",
    name: "抓取策略模型",
    description: null,
    task_type: "IMITATION",
    framework: "pytorch",
    owner_id: USER,
    status: "ACTIVE",
    current_version_id: VERSION,
    tags: ["kitchen"],
    allowed_actions: ["deprecate", "archive"],
    version: 2,
    created_at: "2026-11-09T00:00:00Z",
    updated_at: "2026-11-09T00:00:00Z",
  };
}

function versionPayload() {
  return {
    model_version_id: VERSION,
    model_id: MODEL,
    project_id: PROJECT,
    version_no: 1,
    status: "RELEASED",
    artifact: {
      object_key: "models/policy.onnx",
      sha256: SHA,
      size_bytes: 128,
      format: "ONNX",
      verification_status: "VERIFIED",
    },
    runtime: {
      framework: "pytorch",
      framework_version: "2.4.0",
      python_version: "3.11",
      cuda_version: null,
      image_ref: null,
    },
    training_request_id: null,
    training_delivery_id: null,
    external_ref_id: null,
    dataset_version_ids: [DATASET_VERSION],
    metrics_summary: { success_rate: 0.95 },
    release_notes: null,
    validated_at: "2026-11-09T00:00:00Z",
    released_at: "2026-11-09T00:00:00Z",
    deprecated_at: null,
    deprecation_reason: null,
    allowed_actions: ["deprecate", "archive"],
    version: 3,
    created_at: "2026-11-09T00:00:00Z",
    updated_at: "2026-11-09T00:00:00Z",
  };
}

describe("阶段 5 响应解析", () => {
  it("解析 Model 并保留服务端动作", () => {
    const model = parseModel(modelPayload());
    expect(model.model_id).toBe(MODEL);
    expect(model.allowed_actions).toEqual(["deprecate", "archive"]);
    expect(model.task_type).toBe("IMITATION");
  });

  it("Model 缺少必填字段时抛契约错误", () => {
    const payload = modelPayload();
    delete (payload as Record<string, unknown>).owner_id;
    expect(() => parseModel(payload)).toThrow(ContractValidationError);
  });

  it("解析 ModelVersion 的产物校验和与运行环境", () => {
    const version = parseModelVersion(versionPayload());
    expect(version.artifact.sha256).toBe(SHA);
    expect(version.artifact.verification_status).toBe("VERIFIED");
    expect(version.runtime.framework_version).toBe("2.4.0");
    expect(version.dataset_version_ids).toEqual([DATASET_VERSION]);
  });

  it("解析 Evaluation 阈值与结论", () => {
    const evaluation = parseEvaluation({
      evaluation_id: "66666666-6666-4666-8666-666666666666",
      project_id: PROJECT,
      model_version_id: VERSION,
      dataset_version_id: DATASET_VERSION,
      baseline_evaluation_id: null,
      status: "SUCCEEDED",
      metrics: { success_rate: 0.42 },
      thresholds: { success_rate: { min: 0.9, max: null } },
      verdict: "FAIL",
      report_ref: { object_key: "reports/eval.json" },
      error_code: null,
      error_summary: null,
      started_at: null,
      finished_at: null,
      allowed_actions: [],
      version: 2,
      created_at: "2026-11-09T00:00:00Z",
      updated_at: "2026-11-09T00:00:00Z",
    });
    expect(evaluation.thresholds.success_rate?.min).toBe(0.9);
    expect(evaluation.verdict).toBe("FAIL");
  });

  it("解析 Feedback 与 Requirement 的缺口维度", () => {
    const feedback = parseFeedback({
      feedback_id: "77777777-7777-4777-8777-777777777777",
      project_id: PROJECT,
      model_version_id: VERSION,
      evaluation_id: null,
      inference_task_id: null,
      issue_type: "SCENE_COVERAGE",
      severity: "MAJOR",
      description: "厨房场景成功率仅 0.42",
      dimensions: [
        {
          dimension_type: "SCENE",
          dimension_value: "kitchen",
          current_count: 2,
          target_count: 10,
        },
      ],
      evidence_refs: ["reports/eval.json"],
      status: "OPEN",
      converted_requirement_id: null,
      resolution_note: null,
      allowed_actions: ["triage", "create-requirement"],
      version: 1,
      created_at: "2026-11-09T00:00:00Z",
      updated_at: "2026-11-09T00:00:00Z",
    });
    expect(feedback.dimensions[0]?.dimension_value).toBe("kitchen");

    const requirement = parseRequirement({
      requirement_id: "88888888-8888-4888-8888-888888888888",
      project_id: PROJECT,
      code: "DR-001",
      title: "补采厨房场景",
      description: null,
      source_type: "MODEL_FEEDBACK",
      source_feedback_ids: ["77777777-7777-4777-8777-777777777777"],
      source_evaluation_ids: [],
      dimensions: [
        {
          dimension_type: "SCENE",
          dimension_value: "kitchen",
          current_count: 2,
          target_count: 10,
        },
      ],
      target_episode_count: 8,
      quality_thresholds: {},
      priority: "P1",
      due_at: null,
      owner_id: USER,
      status: "DRAFT",
      linked_dataset_version_ids: [],
      retest_evaluation_ids: [],
      satisfied_at: null,
      satisfaction_evidence_ref: null,
      allowed_actions: ["submit", "cancel"],
      version: 1,
      created_at: "2026-11-09T00:00:00Z",
      updated_at: "2026-11-09T00:00:00Z",
    });
    expect(requirement.source_feedback_ids).toHaveLength(1);
    expect(requirement.target_episode_count).toBe(8);
  });

  it("解析数据缺口汇总并保留建议补采数量", () => {
    const summary = parseGapSummary({
      items: [
        {
          dimension_type: "SCENE",
          dimension_value: "kitchen",
          open_feedback_count: 1,
          current_count: 2,
          target_count: 10,
          suggested_episode_count: 8,
          requirement_ids: [],
        },
      ],
      freshness: "FRESH",
    });
    expect(summary.items[0]?.suggested_episode_count).toBe(8);
    expect(summary.freshness).toBe("FRESH");
  });
});
