/** 阶段 8 采集素材响应解析器（四类素材）。 */

import type { CursorPage } from "../types/api";
import {
  COLLECTION_ASSET_STATUS_VALUES,
  type ActionSpec,
  type CollectionAssetBase,
  type CollectionAssetStep,
  type CollectionPlan,
  type CollectionSop,
  type Scenario,
} from "../types/collectionAsset";
import {
  ContractValidationError,
  asRecord,
  parseCursorPage,
  readEnum,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readStringArray,
  readUuid,
} from "./validation";

function parseBase(
  record: Record<string, unknown>,
  path: string,
): CollectionAssetBase {
  return {
    project_id: readUuid(record, "project_id", path),
    code: readString(record, "code", path),
    name: readString(record, "name", path),
    description: readOptionalString(record, "description", path),
    // 契约声明了枚举：未识别状态说明服务端契约已变更，宁可报错也不静默
    // 当成可操作状态。
    status: readEnum(record, "status", path, COLLECTION_ASSET_STATUS_VALUES),
    version: readPositiveInteger(record, "version", path),
    created_at: readOptionalString(record, "created_at", path),
    updated_at: readOptionalString(record, "updated_at", path),
    created_by: readOptionalUuid(record, "created_by", path),
    updated_by: readOptionalUuid(record, "updated_by", path),
    // 动作取值由服务端与契约共同约束；这里只校验类型，保留未知动作原样，
    // 避免服务端新增动作时整页变成 invalid_response。
    allowed_actions: readStringArray(record, "allowed_actions", path),
  };
}

/** 执行内容条目：契约只要求「对象数组」，前端不解释内部结构。 */
function readStepArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): CollectionAssetStep[] {
  const raw = record[key];
  if (!Array.isArray(raw)) {
    throw new ContractValidationError(`${path}.${key} 必须是数组`);
  }
  return raw.map((item, index) =>
    asRecord(item, `${path}.${key}[${index}]`),
  );
}

export function parseCollectionPlan(
  value: unknown,
  path = "collectionPlan",
): CollectionPlan {
  const record = asRecord(value, path);
  return {
    ...parseBase(record, path),
    plan_id: readUuid(record, "plan_id", path),
    plan_version: readPositiveInteger(record, "plan_version", path),
    owner_id: readOptionalUuid(record, "owner_id", path),
  };
}

export function parseCollectionSop(
  value: unknown,
  path = "collectionSop",
): CollectionSop {
  const record = asRecord(value, path);
  return {
    ...parseBase(record, path),
    sop_id: readUuid(record, "sop_id", path),
    sop_version: readString(record, "sop_version", path),
    steps: readStepArray(record, "steps", path),
  };
}

export function parseScenario(value: unknown, path = "scenario"): Scenario {
  const record = asRecord(value, path);
  return {
    ...parseBase(record, path),
    scenario_id: readUuid(record, "scenario_id", path),
    scene_labels: readStringArray(record, "scene_labels", path),
  };
}

export function parseActionSpec(
  value: unknown,
  path = "actionSpec",
): ActionSpec {
  const record = asRecord(value, path);
  return {
    ...parseBase(record, path),
    action_spec_id: readUuid(record, "action_spec_id", path),
    spec_version: readString(record, "spec_version", path),
    actions: readStepArray(record, "actions", path),
  };
}

export function parseCollectionPlanPage(value: unknown): CursorPage<CollectionPlan> {
  return parseCursorPage(value, "collectionPlans", parseCollectionPlan);
}

export function parseCollectionSopPage(value: unknown): CursorPage<CollectionSop> {
  return parseCursorPage(value, "collectionSops", parseCollectionSop);
}

export function parseScenarioPage(value: unknown): CursorPage<Scenario> {
  return parseCursorPage(value, "scenarios", parseScenario);
}

export function parseActionSpecPage(value: unknown): CursorPage<ActionSpec> {
  return parseCursorPage(value, "actionSpecs", parseActionSpec);
}
