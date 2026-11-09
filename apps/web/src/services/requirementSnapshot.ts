/** 训练需求快照表单值与契约对象的转换。 */

import type { RequirementSnapshot } from "../types/dataset";

export interface SnapshotFormValues {
  business_goal: string;
  model_type: string;
  training_type: string;
  data_modalities: string[];
  scene_targets: string[];
  action_targets: string[];
  robot_targets: string[];
  device_targets: string[];
  required_labels: string[];
  target_episode_count: number;
  quality_threshold: number;
  train_ratio: number;
  validation_ratio: number;
  test_ratio: number;
  format: string;
  format_version: string;
  delivery_due_at: string;
  acceptance_method: string;
  priority: string;
  split_policy_id: string;
}

export function snapshotFromValues(values: SnapshotFormValues): RequirementSnapshot {
  return {
    business_goal: values.business_goal,
    model_type: values.model_type,
    training_type: values.training_type,
    data_modalities: values.data_modalities,
    scene_targets: values.scene_targets,
    action_targets: values.action_targets,
    robot_targets: values.robot_targets,
    device_targets: values.device_targets,
    required_labels: values.required_labels,
    required_schema_versions: [],
    target_episode_count: values.target_episode_count,
    quality_thresholds: { min_quality_score: values.quality_threshold },
    split_requirements: {
      train_ratio: values.train_ratio,
      validation_ratio: values.validation_ratio,
      test_ratio: values.test_ratio,
      split_policy_id: values.split_policy_id?.trim() ? values.split_policy_id.trim() : null,
    },
    format: values.format,
    format_version: values.format_version,
    delivery_due_at: values.delivery_due_at,
    acceptance_method: values.acceptance_method,
    priority: values.priority,
  };
}

