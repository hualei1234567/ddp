/**
 * 质量模块表单的纯校验逻辑（不依赖 React，可单测）。
 *
 * 所有上界都来自契约，不复制服务端业务规则：
 * - `GoldSetCreate`：code 1–64、name ≤200、max_exposure_per_annotator ≥1、
 *   items 为「对象数组且至少 1 项」（`schemas.stage3.yaml`）。
 * - `IAAReportCreate`：metric_definitions_version 1–64、iou_threshold 0–1。
 *
 * 黄金集 `items` 的**逐字段结构契约未声明**（`items.items` 是
 * `additionalProperties: true`），因此这里只校验契约声明的那一层，
 * 不猜 `episode_id` / `expected_labels` 的键名。
 */

import { GOLD_EXPOSURE_POLICY_VALUES, GOLD_ROTATION_POLICY_VALUES } from "../types/annotation";

export const GOLD_SET_CODE_MAX = 64;
export const GOLD_SET_NAME_MAX = 200;
export const METRIC_DEFINITIONS_VERSION_MAX = 64;

/** 解析「JSON 对象数组」文本；空文本视为空数组由调用方判定是否合法。 */
export function parseObjectArrayJson(
  text: string,
  label: string,
): Record<string, unknown>[] {
  const trimmed = text.trim();
  if (trimmed === "") {
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label}不是合法的 JSON`);
  }

  if (!Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 数组`);
  }

  return parsed.map((item, index) => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error(`${label}第 ${index + 1} 项必须是对象`);
    }
    return item as Record<string, unknown>;
  });
}

/** 解析「JSON 对象」文本；空文本视为空对象（契约 default 为 `{}`）。 */
export function parseObjectJson(
  text: string,
  label: string,
): Record<string, unknown> {
  const trimmed = text.trim();
  if (trimmed === "") {
    return {};
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label}不是合法的 JSON`);
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

function checkNonEmptyText(
  value: string | undefined,
  label: string,
  max: number,
): string | null {
  const text = (value ?? "").trim();
  if (text === "") {
    return `请输入${label}`;
  }
  if (text.length > max) {
    return `${label}最多 ${max} 字符`;
  }
  return null;
}

/** 返回第一条错误文案；`null` 表示通过。 */
export function validateGoldSetCode(value: string | undefined): string | null {
  return checkNonEmptyText(value, "编码", GOLD_SET_CODE_MAX);
}

export function validateGoldSetName(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  if (text.length > GOLD_SET_NAME_MAX) {
    return `名称最多 ${GOLD_SET_NAME_MAX} 字符`;
  }
  return null;
}

/** 契约要求「至少一个黄金项」；空数组由服务端也会拒绝，这里提前拦。 */
export function validateGoldSetItems(
  items: readonly Record<string, unknown>[],
): string | null {
  return items.length === 0 ? "至少需要 1 个黄金项" : null;
}

export function validateMaxExposure(value: number | null | undefined): string | null {
  if (value === null || value === undefined) {
    return "请输入每人曝光上限";
  }
  if (!Number.isInteger(value) || value < 1) {
    return "每人曝光上限必须是大于等于 1 的整数";
  }
  return null;
}

/**
 * 曝光 / 轮换策略只允许契约枚举内的值。
 * 表单来源是下拉框，这里作为最后一道校验，防止被篡改成契约外的值。
 */
export function validateExposurePolicy(value: string): string | null {
  return GOLD_EXPOSURE_POLICY_VALUES.some((item) => item === value)
    ? null
    : `曝光策略必须是 ${GOLD_EXPOSURE_POLICY_VALUES.join(" / ")}`;
}

export function validateRotationPolicy(value: string): string | null {
  return GOLD_ROTATION_POLICY_VALUES.some((item) => item === value)
    ? null
    : `轮换策略必须是 ${GOLD_ROTATION_POLICY_VALUES.join(" / ")}`;
}

export function validateMetricDefinitionsVersion(
  value: string | undefined,
): string | null {
  return checkNonEmptyText(
    value,
    "指标口径版本",
    METRIC_DEFINITIONS_VERSION_MAX,
  );
}

export function validateIouThreshold(
  value: number | null | undefined,
): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    return "IoU 阈值必须在 0 到 1 之间";
  }
  return null;
}
