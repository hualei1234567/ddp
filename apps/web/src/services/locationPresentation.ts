/** 阶段 9 场地类型的中文展示映射。 */

import type { StatusPresentation } from "./datasetPresentation";
import { LOCATION_KIND_VALUES } from "../types/location";

const LOCATION_KIND: Record<string, StatusPresentation> = {
  SITE: { label: "基地", color: "blue" },
  ROOM: { label: "房间", color: "cyan" },
  LAB: { label: "实验室", color: "purple" },
};

/** 兜底保留原始码：服务端新增层级时页面仍可读，不静默折叠成「未知」。 */
export function presentLocationKind(value: string): StatusPresentation {
  return LOCATION_KIND[value] ?? { label: value, color: "default" };
}

export function locationKindOptions(): Array<{ value: string; label: string }> {
  return LOCATION_KIND_VALUES.map((value) => ({
    value,
    label: presentLocationKind(value).label,
  }));
}
