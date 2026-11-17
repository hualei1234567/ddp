/**
 * 阶段 9 场地（多基地）领域类型。
 *
 * 契约：`packages/contracts/fragments/schemas.locations.yaml`。
 *
 * 两点刻意的缺失，前端不得自行补：
 * - **没有 `status`**：后端没有「启用 / 停用」概念，页面不要造一个状态位；
 * - **没有 `DELETE`**：场地被设备与采集任务以复合外键引用，后端不提供删除，
 *   页面也不要画删除按钮。
 */

export const LOCATION_KIND_VALUES = ["SITE", "ROOM", "LAB"] as const;

export type LocationKind = (typeof LOCATION_KIND_VALUES)[number];

export interface Location {
  location_id: string;
  project_id: string;
  code: string;
  name: string;
  kind: LocationKind;
  address: string | null;
  notes: string | null;
  /** 乐观锁版本号；PATCH 必须回传为 `If-Match`。 */
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface LocationCreateInput {
  code: string;
  name: string;
  kind: LocationKind;
  address: string | null;
  notes: string | null;
}

/**
 * PATCH 请求体：`code` 不可改（它是被设备与任务引用的业务标识）。
 * 显式 `null` 只对 `address` / `notes` 生效（清空）。
 */
export interface LocationPatchInput {
  name?: string;
  kind?: LocationKind;
  address?: string | null;
  notes?: string | null;
}
