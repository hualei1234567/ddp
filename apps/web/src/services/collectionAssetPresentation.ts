/**
 * 采集素材的中文展示映射与类型收窄。
 *
 * 只做展示：状态与动作取值以服务端返回为准，未识别的值原样展示，
 * 不据此推断权限或按钮（按钮只看 `allowed_actions`）。
 */

import type { StatusPresentation } from "./datasetPresentation";
import type {
  ActionSpec,
  CollectionAsset,
  CollectionAssetKind,
  CollectionPlan,
  CollectionSop,
  Scenario,
} from "../types/collectionAsset";

const ASSET_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  ACTIVE: { label: "启用中", color: "green" },
  ARCHIVED: { label: "已归档", color: "default" },
};

const ASSET_ACTION: Record<string, string> = {
  activate: "激活",
  archive: "归档",
};

const KIND_LABEL: Record<CollectionAssetKind, string> = {
  plans: "采集方案",
  sops: "SOP",
  scenarios: "场景",
  "action-specs": "动作库",
};

/** 每类素材的版本列（场景不版本化，故为空）。 */
const KIND_VERSION_LABEL: Record<CollectionAssetKind, string> = {
  plans: "方案版本",
  sops: "SOP 版本",
  scenarios: "",
  "action-specs": "动作库版本",
};

/** 每类素材的执行内容（创建后不可改，只能新建版本行）。 */
const KIND_EXECUTION_LABEL: Record<CollectionAssetKind, string> = {
  plans: "",
  sops: "步骤",
  scenarios: "",
  "action-specs": "动作",
};

export function presentAssetStatus(value: string): StatusPresentation {
  return ASSET_STATUS[value] ?? { label: value, color: "default" };
}

export function presentAssetAction(action: string): string {
  return ASSET_ACTION[action] ?? action;
}

export function assetKindLabel(kind: CollectionAssetKind): string {
  return KIND_LABEL[kind];
}

export function assetVersionLabel(kind: CollectionAssetKind): string {
  return KIND_VERSION_LABEL[kind];
}

export function assetExecutionLabel(kind: CollectionAssetKind): string {
  return KIND_EXECUTION_LABEL[kind];
}

export function isCollectionPlan(asset: CollectionAsset): asset is CollectionPlan {
  return "plan_version" in asset;
}

export function isCollectionSop(asset: CollectionAsset): asset is CollectionSop {
  return "steps" in asset;
}

export function isScenario(asset: CollectionAsset): asset is Scenario {
  return "scene_labels" in asset;
}

export function isActionSpec(asset: CollectionAsset): asset is ActionSpec {
  return "actions" in asset;
}

/** 四类素材主键字段名不同，列表去重与路由跳转都需要统一取法。 */
export function assetId(asset: CollectionAsset): string {
  if (isCollectionPlan(asset)) {
    return asset.plan_id;
  }
  if (isCollectionSop(asset)) {
    return asset.sop_id;
  }
  if (isScenario(asset)) {
    return asset.scenario_id;
  }
  return asset.action_spec_id;
}

/** 执行内容只读展示：结构由服务端定义，前端不解释字段含义。 */
export function formatExecutionJson(items: readonly Record<string, unknown>[]): string {
  return JSON.stringify(items, null, 2);
}

/**
 * 归档终态不再允许修改（契约 `CollectionPlanPatch` 等：`ARCHIVED` 不允许任何修改）。
 * 前端用它决定是否展示「编辑」入口；服务端仍是权威，越权/越状态由服务端拒绝。
 */
export function isAssetEditable(status: string): boolean {
  return status !== "ARCHIVED";
}

/**
 * 删除只允许 `DRAFT`（契约：`ACTIVE` 可能被新任务挑选、`ARCHIVED` 是历史快照）。
 * 删除不在 `allowed_actions` 里，因此由状态决定入口；服务端仍会按引用关系拒绝。
 */
export function isAssetDeletable(status: string): boolean {
  return status === "DRAFT";
}

/** 概览列的「版本」展示：四类素材的版本列语义不同，不能混用同一个字段名。 */
export function assetVersionText(asset: CollectionAsset): string {
  if (isCollectionPlan(asset)) {
    return `v${asset.plan_version}`;
  }
  if (isCollectionSop(asset)) {
    return asset.sop_version;
  }
  if (isActionSpec(asset)) {
    return asset.spec_version;
  }
  return "—";
}
