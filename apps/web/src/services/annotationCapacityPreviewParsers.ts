/**
 * 分配前容量预览解析（FR-020）。
 *
 * 严格解析：字段缺失、类型不符、枚举越界一律 `ContractValidationError`。
 * 两个容易写错的地方单独守住：
 *
 * 1. `max_*` 为 `null` = 部署未配置，**不是 0**；对应比率同样必须为 `null`，
 *    否则进度条会把"未配置"画成"零容量"；
 * 2. 布尔字段必须真的是布尔，不用 truthy 兜底——服务端口径变化时要报错，
 *    而不是悄悄把 `"false"` 当成 `false`。
 */
import type {
  AssignmentCapacityPreview,
  CapacityPreviewStatus,
} from "../types/annotationCapacityPreview";
import { CAPACITY_PREVIEW_STATUS_VALUES } from "../types/annotationCapacityPreview";
import {
  asRecord,
  ContractValidationError,
  readBoolean,
  readEnum,
  readInteger,
  readOptionalNumber,
  readUuid,
} from "./validation";

function readOptionalInteger(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  const value = readOptionalNumber(record, key, path);
  if (value === null) {
    return null;
  }
  if (!Number.isInteger(value) || value < 0) {
    throw new ContractValidationError(`${path} 必须是非负整数或 null`);
  }
  return value;
}

export function parseAssignmentCapacityPreview(
  value: unknown,
): AssignmentCapacityPreview {
  const record = asRecord(value, "容量预览");
  const maxOpenItems = readOptionalInteger(
    record,
    "max_open_items",
    "容量预览.max_open_items",
  );
  const maxPlannedMinutes = readOptionalInteger(
    record,
    "max_planned_minutes",
    "容量预览.max_planned_minutes",
  );
  const itemRatio = readOptionalNumber(
    record,
    "item_capacity_ratio",
    "容量预览.item_capacity_ratio",
  );
  const minutesRatio = readOptionalNumber(
    record,
    "minutes_capacity_ratio",
    "容量预览.minutes_capacity_ratio",
  );
  // 比率与上限必须同时有值或同时为空：只有其一说明服务端口径被破坏。
  if ((maxOpenItems === null) !== (itemRatio === null)) {
    throw new ContractValidationError(
      "容量预览.max_open_items 与 item_capacity_ratio 必须同时为空或同时有值",
    );
  }
  if ((maxPlannedMinutes === null) !== (minutesRatio === null)) {
    throw new ContractValidationError(
      "容量预览.max_planned_minutes 与 minutes_capacity_ratio 必须同时为空或同时有值",
    );
  }
  const willExceed = readBoolean(record, "will_exceed", "容量预览.will_exceed");
  const exceedsOpenItems = readBoolean(
    record,
    "exceeds_open_items",
    "容量预览.exceeds_open_items",
  );
  const exceedsPlannedMinutes = readBoolean(
    record,
    "exceeds_planned_minutes",
    "容量预览.exceeds_planned_minutes",
  );
  // 三个布尔必须互相自洽，否则 UI 会出现"整体不超但项数超"的矛盾提示。
  if (willExceed !== (exceedsOpenItems || exceedsPlannedMinutes)) {
    throw new ContractValidationError(
      "容量预览.will_exceed 必须等于 exceeds_open_items 与 exceeds_planned_minutes 的逻辑或",
    );
  }
  return {
    assignee_id: readUuid(record, "assignee_id", "容量预览.assignee_id"),
    open_item_count: readInteger(
      record,
      "open_item_count",
      "容量预览.open_item_count",
    ),
    open_planned_minutes: readInteger(
      record,
      "open_planned_minutes",
      "容量预览.open_planned_minutes",
    ),
    planned_item_count: readInteger(
      record,
      "planned_item_count",
      "容量预览.planned_item_count",
    ),
    planned_minutes: readInteger(
      record,
      "planned_minutes",
      "容量预览.planned_minutes",
    ),
    projected_item_count: readInteger(
      record,
      "projected_item_count",
      "容量预览.projected_item_count",
    ),
    projected_planned_minutes: readInteger(
      record,
      "projected_planned_minutes",
      "容量预览.projected_planned_minutes",
    ),
    max_open_items: maxOpenItems,
    max_planned_minutes: maxPlannedMinutes,
    item_capacity_ratio: itemRatio,
    minutes_capacity_ratio: minutesRatio,
    will_exceed: willExceed,
    exceeds_open_items: exceedsOpenItems,
    exceeds_planned_minutes: exceedsPlannedMinutes,
    status: readEnum<CapacityPreviewStatus>(
      record,
      "status",
      "容量预览.status",
      CAPACITY_PREVIEW_STATUS_VALUES,
    ),
  };
}

/** 根响应是数组：逐项解析，任何一项不合约即整体失败。 */
export function parseAssignmentCapacityPreviewList(
  value: unknown,
): AssignmentCapacityPreview[] {
  if (!Array.isArray(value)) {
    throw new ContractValidationError("容量预览必须是数组");
  }
  return value.map((item) => parseAssignmentCapacityPreview(item));
}
