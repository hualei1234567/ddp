/**
 * 采集素材表单的纯校验逻辑（可单测，不依赖 React）。
 *
 * 上限直接来自契约 `schemas.stage8.yaml`：`scene_labels.maxItems = 32`、
 * 单个标签 `maxLength = 64`。这里只做输入约束，不复制业务规则——
 * 去重与最终校验仍由服务端负责。
 */

import type { CollectionAssetStep } from "../types/collectionAsset";

export const SCENE_LABELS_MAX = 32;
export const SCENE_LABEL_MAX_LENGTH = 64;

/** 执行内容用 JSON 文本录入：契约只要求「对象数组」，不额外约定字段。 */
export function parseStepJsonText(text: string): CollectionAssetStep[] {
  const trimmed = text.trim();
  if (trimmed === "") {
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error("不是合法的 JSON");
  }

  if (!Array.isArray(parsed)) {
    throw new Error("必须是 JSON 数组");
  }

  return parsed.map((item, index) => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new Error(`第 ${index + 1} 项必须是对象`);
    }
    return item as CollectionAssetStep;
  });
}

/** 去空白、去空项、保序去重；服务端仍会再规范化一次。 */
export function normalizeSceneLabels(labels: readonly string[]): string[] {
  const result: string[] = [];
  for (const raw of labels) {
    const value = raw.trim();
    if (value === "" || result.includes(value)) {
      continue;
    }
    result.push(value);
  }
  return result;
}

/** 返回第一条错误文案；`null` 表示通过。 */
export function validateSceneLabels(labels: readonly string[]): string | null {
  if (labels.length > SCENE_LABELS_MAX) {
    return `最多 ${SCENE_LABELS_MAX} 个标签`;
  }
  const tooLong = labels.find(
    (label) => label.length > SCENE_LABEL_MAX_LENGTH,
  );
  if (tooLong !== undefined) {
    return `单个标签最多 ${SCENE_LABEL_MAX_LENGTH} 字符`;
  }
  return null;
}

/** 描述为空字符串时提交 `null`（契约允许显式清空）。 */
export function normalizeDescription(value: string | undefined): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed === "" ? null : trimmed;
}
