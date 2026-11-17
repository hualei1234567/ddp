/**
 * 采集任务创建表单的纯转换逻辑（不依赖 React，可单测）。
 *
 * 契约约束（`CollectionTaskCreate`）：
 * - `code` 1–64、`name` 1–200，`priority` 取 `P0/P1/P2`；
 * - `target_episode_count` / `target_duration_sec` 为 ≥0 的整数；
 * - `sop_version` / `action_spec_version` 必须与对应 ID **成对出现**
 *   （服务端 `asset_references` 会拒绝只有 ID 或只有版本的请求）。
 *
 * 因此关联项在界面上是下拉选择，版本号从选中的素材行带出，不让用户手填。
 */

import {
  COLLECTION_PRIORITY_VALUES,
  type CollectionPriority,
  type CollectionTaskCreateInput,
} from "../types/collection";

export interface TaskCreateFormValues {
  code: string;
  name: string;
  priority: CollectionPriority;
  target_episode_count: number;
  target_duration_sec: number;
  plan_id?: string | null;
  sop_id?: string | null;
  scenario_id?: string | null;
  action_spec_id?: string | null;
  location_id?: string | null;
}

/** 选中素材时带出的冻结版本号。 */
export interface TaskLinkVersions {
  sop_version?: string | null;
  action_spec_version?: string | null;
}

function requireText(value: string | undefined, label: string, max: number): string {
  const text = (value ?? "").trim();
  if (text === "") {
    throw new Error(`请填写${label}`);
  }
  if (text.length > max) {
    throw new Error(`${label}最多 ${max} 字符`);
  }
  return text;
}

function requireNonNegativeInteger(
  value: number | undefined,
  label: string,
): number {
  if (value === undefined || !Number.isInteger(value) || value < 0) {
    throw new Error(`${label}必须是不小于 0 的整数`);
  }
  return value;
}

function optionalId(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

function optionalVersion(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

/**
 * 组装创建请求体。未选择的关联项整字段省略（不是空字符串），
 * 避免服务端把 `""` 当作非法 UUID 拒绝。
 */
export function buildTaskCreateInput(
  values: TaskCreateFormValues,
  versions: TaskLinkVersions = {},
): CollectionTaskCreateInput {
  if (!COLLECTION_PRIORITY_VALUES.includes(values.priority)) {
    throw new Error("优先级必须是 P0 / P1 / P2");
  }

  const input: CollectionTaskCreateInput = {
    code: requireText(values.code, "编码", 64),
    name: requireText(values.name, "名称", 200),
    priority: values.priority,
    target_episode_count: requireNonNegativeInteger(
      values.target_episode_count,
      "目标条数",
    ),
    target_duration_sec: requireNonNegativeInteger(
      values.target_duration_sec,
      "目标时长",
    ),
  };

  const planId = optionalId(values.plan_id);
  if (planId !== null) {
    input.plan_id = planId;
  }
  const scenarioId = optionalId(values.scenario_id);
  if (scenarioId !== null) {
    input.scenario_id = scenarioId;
  }
  const locationId = optionalId(values.location_id);
  if (locationId !== null) {
    input.location_id = locationId;
  }

  const sopId = optionalId(values.sop_id);
  const sopVersion = optionalVersion(versions.sop_version);
  if (sopId !== null) {
    if (sopVersion === null) {
      throw new Error("已选择 SOP，但未取到其版本号；请重新选择 SOP");
    }
    input.sop_id = sopId;
    input.sop_version = sopVersion;
  } else if (sopVersion !== null) {
    throw new Error("SOP 版本必须与 SOP 同时存在");
  }

  const actionSpecId = optionalId(values.action_spec_id);
  const actionSpecVersion = optionalVersion(versions.action_spec_version);
  if (actionSpecId !== null) {
    if (actionSpecVersion === null) {
      throw new Error("已选择动作库，但未取到其版本号；请重新选择动作库");
    }
    input.action_spec_id = actionSpecId;
    input.action_spec_version = actionSpecVersion;
  } else if (actionSpecVersion !== null) {
    throw new Error("动作库版本必须与动作库同时存在");
  }

  return input;
}
