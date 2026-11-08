/**
 * 设备列表筛选与详情 Tab 的 URL 状态。
 *
 * 只读写白名单参数，避免把任意查询参数从列表复制到详情或反向带出。
 * `project_id` 仍由 `useProjectId` 解析，这里只负责在跳转时显式携带。
 */

export interface DeviceListFilters {
  keyword: string;
  status: string;
  deviceType: string;
}

export const EMPTY_DEVICE_LIST_FILTERS: DeviceListFilters = {
  keyword: "",
  status: "",
  deviceType: "",
};

export const DEVICE_DETAIL_TAB_VALUES = [
  "overview",
  "sensors",
  "calibrations",
] as const;

export type DeviceDetailTab = (typeof DEVICE_DETAIL_TAB_VALUES)[number];

export const DEFAULT_DEVICE_DETAIL_TAB: DeviceDetailTab = "overview";

function readParam(params: URLSearchParams, key: string): string {
  return (params.get(key) ?? "").trim();
}

/** 从 URL 恢复列表筛选；未识别或空值统一回落为空筛选。 */
export function readDeviceListFilters(
  params: URLSearchParams,
): DeviceListFilters {
  return {
    keyword: readParam(params, "q"),
    status: readParam(params, "status"),
    deviceType: readParam(params, "device_type"),
  };
}

export function hasDeviceListFilters(filters: DeviceListFilters): boolean {
  return (
    filters.keyword !== "" ||
    filters.status !== "" ||
    filters.deviceType !== ""
  );
}

/** 详情 Tab 只接受白名单值；非法值回落概览，避免把未知 key 传给 Tabs。 */
export function resolveDeviceDetailTab(
  value: string | null | undefined,
): DeviceDetailTab {
  if (!value) {
    return DEFAULT_DEVICE_DETAIL_TAB;
  }
  return (DEVICE_DETAIL_TAB_VALUES as readonly string[]).includes(value)
    ? (value as DeviceDetailTab)
    : DEFAULT_DEVICE_DETAIL_TAB;
}

/**
 * 构建设备列表/详情链接的查询串。
 * 列表跳详情时携带筛选，详情返回列表与刷新后仍可从 URL 恢复；
 * 默认 Tab（overview）不写入 URL，保持地址简洁。
 */
export function buildDeviceSearch(
  projectId: string | null,
  filters: DeviceListFilters = EMPTY_DEVICE_LIST_FILTERS,
  tab?: DeviceDetailTab,
): string {
  const params = new URLSearchParams();
  if (projectId) {
    params.set("project_id", projectId);
  }
  if (filters.keyword !== "") {
    params.set("q", filters.keyword);
  }
  if (filters.status !== "") {
    params.set("status", filters.status);
  }
  if (filters.deviceType !== "") {
    params.set("device_type", filters.deviceType);
  }
  if (tab && tab !== DEFAULT_DEVICE_DETAIL_TAB) {
    params.set("tab", tab);
  }

  const query = params.toString();
  return query ? `?${query}` : "";
}
