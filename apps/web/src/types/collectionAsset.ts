/**
 * 阶段 8 采集素材（采集方案 / SOP / 场景 / 动作库）领域类型。
 *
 * 契约：`packages/contracts/fragments/schemas.stage8.yaml`。
 * 四类素材共用状态机与审计字段，差异只在各自的版本列与执行内容字段：
 * - `code` 四类都不可改（项目内业务键）；
 * - `plan_version` 不可改（采集方案的内容版本）；
 * - `steps` / `actions` 任何状态都不可改（改内容 = 新建版本行）；
 * - 场景不版本化，`scene_labels` 可改。
 *
 * `allowed_actions` 是服务端按当前状态算出的可执行动作，前端只按它渲染按钮，
 * 不自行实现状态机（见 `backend/app/domain/collection/asset_state_machines.py`）。
 */

/** 契约枚举：`DRAFT --activate--> ACTIVE --archive--> ARCHIVED`（终态）。 */
export const COLLECTION_ASSET_STATUS_VALUES = [
  "DRAFT",
  "ACTIVE",
  "ARCHIVED",
] as const;

export type CollectionAssetStatus =
  (typeof COLLECTION_ASSET_STATUS_VALUES)[number];

/** 契约 `CollectionAssetAction`：路径中只接受这两个值。 */
export const COLLECTION_ASSET_ACTION_VALUES = ["activate", "archive"] as const;

export type CollectionAssetAction =
  (typeof COLLECTION_ASSET_ACTION_VALUES)[number];

/**
 * 执行内容条目（SOP `steps` / 动作库 `actions`）。
 * 契约只约束为「对象」，前端不解释内部业务结构，只做只读展示。
 */
export type CollectionAssetStep = Record<string, unknown>;

export interface CollectionAssetBase {
  project_id: string;
  code: string;
  name: string;
  description: string | null;
  status: CollectionAssetStatus;
  /** 乐观锁版本号；写动作必须回传为 `If-Match`。 */
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
  /** 服务端允许的动作；`ARCHIVED` 为空数组。 */
  allowed_actions: string[];
}

export interface CollectionPlan extends CollectionAssetBase {
  plan_id: string;
  plan_version: number;
  owner_id: string | null;
}

export interface CollectionSop extends CollectionAssetBase {
  sop_id: string;
  sop_version: string;
  steps: CollectionAssetStep[];
}

export interface Scenario extends CollectionAssetBase {
  scenario_id: string;
  scene_labels: string[];
}

export interface ActionSpec extends CollectionAssetBase {
  action_spec_id: string;
  spec_version: string;
  actions: CollectionAssetStep[];
}

export type CollectionAsset =
  | CollectionPlan
  | CollectionSop
  | Scenario
  | ActionSpec;

/** 创建请求体（四类共用 code / name / description，其余按类型补充）。 */
export interface CollectionPlanCreateInput {
  code: string;
  name: string;
  plan_version: number;
  description: string | null;
}

export interface CollectionSopCreateInput {
  code: string;
  name: string;
  sop_version: string;
  steps: CollectionAssetStep[];
  description: string | null;
}

export interface ScenarioCreateInput {
  code: string;
  name: string;
  scene_labels: string[];
  description: string | null;
}

export interface ActionSpecCreateInput {
  code: string;
  name: string;
  spec_version: string;
  actions: CollectionAssetStep[];
  description: string | null;
}

export type CollectionAssetCreateInput =
  | CollectionPlanCreateInput
  | CollectionSopCreateInput
  | ScenarioCreateInput
  | ActionSpecCreateInput;

/** PATCH 只允许描述性字段；`code` / 版本列 / 执行内容刻意不在其中。 */
export interface CollectionAssetPatchInput {
  name?: string;
  description?: string | null;
}

/** 场景不版本化，标签可改（服务端保序去重）。 */
export interface ScenarioPatchInput extends CollectionAssetPatchInput {
  scene_labels?: string[];
}

/** 四类素材的 UI 路由段（与 API 的复数资源名分开：UI 更短，避免路径冗长）。 */
export const COLLECTION_ASSET_KINDS = [
  "plans",
  "sops",
  "scenarios",
  "action-specs",
] as const;

export type CollectionAssetKind = (typeof COLLECTION_ASSET_KINDS)[number];
