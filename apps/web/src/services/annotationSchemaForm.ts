/**
 * Schema 定义表单与契约之间的转换（纯函数，可单测）。
 *
 * `value_spec` 在契约里是自由对象；表单里用 JSON 文本编辑，这里统一解析并
 * 校验为对象。解析失败直接抛错，不静默当成 `{}`——那会把用户的输入吞掉。
 */
import type {
  AnnotationLabelType,
  AnnotationLevel,
  LabelDefinition,
  LabelVisibility,
} from "../types/annotation";

export interface LabelFormValues {
  label_key: string;
  label_type: AnnotationLabelType;
  required: boolean;
  level: AnnotationLevel;
  visibility: LabelVisibility;
  description?: string | null;
  value_spec_text?: string | null;
  quality_rules?: string[];
}

export function emptyLabelFormValues(): LabelFormValues {
  return {
    label_key: "",
    label_type: "ENUM",
    required: false,
    level: "EPISODE",
    visibility: "ANNOTATOR",
    description: null,
    value_spec_text: "",
    quality_rules: [],
  };
}

export function labelsToFormValues(
  labels: readonly LabelDefinition[],
): LabelFormValues[] {
  return labels.map((label) => ({
    label_key: label.label_key,
    label_type: label.label_type,
    required: label.required,
    level: label.level,
    visibility: label.visibility,
    description: label.description,
    value_spec_text:
      Object.keys(label.value_spec).length === 0
        ? ""
        : JSON.stringify(label.value_spec),
    quality_rules: [...label.quality_rules],
  }));
}

function parseValueSpec(text: string | null | undefined, labelKey: string) {
  const trimmed = (text ?? "").trim();
  if (trimmed === "") {
    return {};
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`标签「${labelKey}」的值定义不是合法 JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`标签「${labelKey}」的值定义必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

export function formValuesToLabels(
  values: readonly LabelFormValues[],
): LabelDefinition[] {
  if (values.length === 0) {
    throw new Error("Schema 至少需要一个标签定义");
  }
  const seen = new Set<string>();
  return values.map((value) => {
    const labelKey = value.label_key.trim();
    if (labelKey === "") {
      throw new Error("标签键不能为空");
    }
    if (seen.has(labelKey)) {
      throw new Error(`标签键「${labelKey}」重复`);
    }
    seen.add(labelKey);
    return {
      label_key: labelKey,
      label_type: value.label_type,
      required: value.required,
      level: value.level,
      visibility: value.visibility,
      description: value.description?.trim() ? value.description.trim() : null,
      value_spec: parseValueSpec(value.value_spec_text, labelKey),
      quality_rules: (value.quality_rules ?? []).filter(
        (rule) => rule.trim() !== "",
      ),
    };
  });
}
