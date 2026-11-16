/** 阶段 8/9 采集素材与场地的测试数据（与契约字段一一对应）。 */

import type {
  ActionSpec,
  CollectionPlan,
  CollectionSop,
  Scenario,
} from "../../types/collectionAsset";
import type { Location } from "../../types/location";

export const ASSET_PROJECT_ID = "99999999-9999-7999-8999-999999999999";
export const OTHER_ASSET_PROJECT_ID = "88888888-8888-7888-8888-888888888888";

export const PLAN_ID = "11111111-1111-7111-8111-111111111111";
export const SOP_ID = "22222222-2222-7222-8222-222222222222";
export const SCENARIO_ID = "33333333-3333-7333-8333-333333333333";
export const ACTION_SPEC_ID = "44444444-4444-7444-8444-444444444444";
export const LOCATION_ID = "55555555-5555-7555-8555-555555555555";

const BASE = {
  project_id: ASSET_PROJECT_ID,
  description: "说明",
  version: 3,
  created_at: "2026-11-01T08:00:00Z",
  updated_at: "2026-11-02T09:30:00Z",
  created_by: null,
  updated_by: null,
} as const;

export function planFixture(
  overrides: Partial<CollectionPlan> = {},
): CollectionPlan {
  return {
    ...BASE,
    plan_id: PLAN_ID,
    code: "PLAN-001",
    name: "仓储拣选采集方案",
    plan_version: 2,
    owner_id: null,
    status: "DRAFT",
    allowed_actions: ["activate"],
    ...overrides,
  };
}

export function sopFixture(
  overrides: Partial<CollectionSop> = {},
): CollectionSop {
  return {
    ...BASE,
    sop_id: SOP_ID,
    code: "SOP-001",
    name: "上料 SOP",
    sop_version: "1.0.0",
    steps: [{ order: 1, instruction: "检查夹爪" }],
    status: "DRAFT",
    allowed_actions: ["activate"],
    ...overrides,
  };
}

export function scenarioFixture(
  overrides: Partial<Scenario> = {},
): Scenario {
  return {
    ...BASE,
    scenario_id: SCENARIO_ID,
    code: "SCN-001",
    name: "货架补货",
    scene_labels: ["室内", "货架", "光照充足"],
    status: "DRAFT",
    allowed_actions: ["activate"],
    ...overrides,
  };
}

export function actionSpecFixture(
  overrides: Partial<ActionSpec> = {},
): ActionSpec {
  return {
    ...BASE,
    action_spec_id: ACTION_SPEC_ID,
    code: "ACT-001",
    name: "抓取动作库",
    spec_version: "1.0.0",
    actions: [{ name: "grasp", joints: [0, 1, 2] }],
    status: "DRAFT",
    allowed_actions: ["activate"],
    ...overrides,
  };
}

export function locationFixture(overrides: Partial<Location> = {}): Location {
  return {
    location_id: LOCATION_ID,
    project_id: ASSET_PROJECT_ID,
    code: "SITE-001",
    name: "上海一号基地",
    kind: "SITE",
    address: "上海市浦东新区",
    notes: null,
    version: 1,
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-02T09:30:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}
