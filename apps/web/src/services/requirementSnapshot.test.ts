import { describe, expect, it } from "vitest";

import { snapshotFromValues, type SnapshotFormValues } from "./requirementSnapshot";

const VALUES: SnapshotFormValues = {
  business_goal: "训练抓取策略",
  model_type: "diffusion-policy",
  training_type: "IMITATION",
  data_modalities: ["RGB", "PROPRIOCEPTION"],
  scene_targets: ["kitchen"],
  action_targets: ["pick"],
  robot_targets: ["arm-a"],
  device_targets: ["cam-a"],
  required_labels: ["action"],
  target_episode_count: 10,
  quality_threshold: 0.8,
  train_ratio: 0.8,
  validation_ratio: 0.1,
  test_ratio: 0.1,
  format: "LE_ROBOT",
  format_version: "ddp.lerobot/1.0.0",
  delivery_due_at: "2026-12-31T00:00:00Z",
  acceptance_method: "离线回放验收",
  priority: "P1",
  split_policy_id: "",
};

describe("训练需求快照转换", () => {
  it("把表单值转换为契约快照", () => {
    const snapshot = snapshotFromValues(VALUES);
    expect(snapshot.business_goal).toBe("训练抓取策略");
    expect(snapshot.quality_thresholds).toEqual({ min_quality_score: 0.8 });
    expect(snapshot.split_requirements).toMatchObject({
      train_ratio: 0.8,
      validation_ratio: 0.1,
      test_ratio: 0.1,
      split_policy_id: null,
    });
    expect(snapshot.required_schema_versions).toEqual([]);
  });

  it("空划分策略 ID 转为 null", () => {
    const snapshot = snapshotFromValues({ ...VALUES, split_policy_id: "  " });
    expect((snapshot.split_requirements as Record<string, unknown>).split_policy_id).toBeNull();
  });
});
