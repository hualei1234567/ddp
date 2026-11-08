import type {
  AnnotationLabelType,
  AnnotationSchema,
  AnnotationWorkRecordInput,
  LabelDefinition,
  LabelValue,
} from "../types/annotation";

export interface TimeRangeInput {
  start: string;
  end: string;
}

/** 表单内部值：布尔未选择用 null，避免把“未填”误当成 false。 */
export type DraftFieldValue =
  | string
  | string[]
  | boolean
  | null
  | TimeRangeInput;

export type EpisodeFormValues = Record<string, DraftFieldValue>;

export interface FieldIssue {
  /** `${episodeId}.${labelKey}`；记录级错误使用 `${episodeId}.__record__`。 */
  field: string;
  message: string;
}

export interface LabelValidation {
  status: "valid" | "skip" | "error";
  value?: unknown;
  message?: string;
}

export interface EpisodeValueInput {
  episodeId: string;
  episodeVersion: number;
}

const TIME_RANGE_TYPES = new Set<AnnotationLabelType>([
  "TIME_RANGE",
  "ACTION_SEGMENT",
]);

function isTimeRangeValue(
  value: DraftFieldValue | undefined,
): value is TimeRangeInput {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as TimeRangeInput).start === "string" &&
    typeof (value as TimeRangeInput).end === "string"
  );
}

function isEmptyValue(value: DraftFieldValue | undefined): boolean {
  if (value === undefined || value === null) {
    return true;
  }
  if (typeof value === "string") {
    return value.trim() === "";
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  if (isTimeRangeValue(value)) {
    return value.start.trim() === "" && value.end.trim() === "";
  }
  return false;
}

function readSpecNumber(
  spec: Record<string, unknown>,
  key: string,
): number | undefined {
  const raw = spec[key];
  if (raw === undefined || raw === null) {
    return undefined;
  }
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim() !== "") {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function readEnumValues(
  spec: Record<string, unknown>,
): string[] | null {
  const raw = spec.enum_values;
  if (!Array.isArray(raw) || raw.length === 0) {
    return null;
  }
  if (!raw.every((item) => typeof item === "string")) {
    return null;
  }
  return raw;
}

function parseIntegerField(value: string, label: string): number {
  const trimmed = value.trim();
  if (trimmed === "" || !/^-?\d+$/.test(trimmed)) {
    throw new Error(`${label}必须是整数纳秒`);
  }
  const parsed = Number(trimmed);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label}超出安全整数范围`);
  }
  return parsed;
}

function validateDuration(
  startNs: number,
  endNs: number,
  spec: Record<string, unknown>,
): string | null {
  if (startNs >= endNs) {
    return "开始时间必须小于结束时间";
  }
  const minDuration = readSpecNumber(spec, "min_duration_ns");
  const maxDuration = readSpecNumber(spec, "max_duration_ns");
  const duration = endNs - startNs;
  if (minDuration !== undefined && duration < minDuration) {
    return `时间片段短于最小允许时长 ${minDuration} ns`;
  }
  if (maxDuration !== undefined && duration > maxDuration) {
    return `时间片段超过最大允许时长 ${maxDuration} ns`;
  }
  return null;
}

function parseTimeRangeValue(
  value: DraftFieldValue | undefined,
  spec: Record<string, unknown>,
): LabelValidation {
  if (!isTimeRangeValue(value)) {
    return { status: "error", message: "请填写开始与结束时间（纳秒）" };
  }
  try {
    const start = parseIntegerField(value.start, "开始时间");
    const end = parseIntegerField(value.end, "结束时间");
    const durationIssue = validateDuration(start, end, spec);
    if (durationIssue !== null) {
      return { status: "error", message: durationIssue };
    }
    return {
      status: "valid",
      value: { start_time_ns: start, end_time_ns: end, time_domain: "UTC" },
    };
  } catch (caught) {
    return {
      status: "error",
      message: caught instanceof Error ? caught.message : "时间片段格式非法",
    };
  }
}

function parseObjectTrack(
  text: string,
  spec: Record<string, unknown>,
): LabelValidation {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { status: "error", message: "OBJECT_TRACK 必须是合法 JSON 数组" };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return { status: "error", message: "OBJECT_TRACK 必须是非空数组" };
  }
  for (const item of parsed) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      return { status: "error", message: "OBJECT_TRACK 每个元素必须是对象" };
    }
    const record = item as Record<string, unknown>;
    const start = record.start_time_ns;
    const end = record.end_time_ns;
    if (
      typeof start !== "number" ||
      typeof end !== "number" ||
      !Number.isInteger(start) ||
      !Number.isInteger(end)
    ) {
      return {
        status: "error",
        message: "OBJECT_TRACK 每项必须包含整数 start_time_ns 与 end_time_ns",
      };
    }
    const durationIssue = validateDuration(start, end, spec);
    if (durationIssue !== null) {
      return { status: "error", message: durationIssue };
    }
  }
  return { status: "valid", value: parsed };
}

/** 按 Schema 标签定义校验单个表单值；与后端领域规则保持一致。 */
export function validateLabelValue(
  definition: LabelDefinition,
  raw: DraftFieldValue | undefined,
): LabelValidation {
  if (isEmptyValue(raw)) {
    return definition.required
      ? { status: "error", message: "该标签为必填项" }
      : { status: "skip" };
  }

  switch (definition.label_type) {
    case "ENUM": {
      const values = readEnumValues(definition.value_spec);
      if (values === null) {
        return { status: "error", message: "Schema 枚举定义不可用" };
      }
      if (typeof raw !== "string" || !values.includes(raw)) {
        return { status: "error", message: "请选择允许的枚举值" };
      }
      return { status: "valid", value: raw };
    }
    case "MULTI_ENUM": {
      const values = readEnumValues(definition.value_spec);
      if (values === null) {
        return { status: "error", message: "Schema 枚举定义不可用" };
      }
      if (!Array.isArray(raw) || raw.length === 0) {
        return { status: "error", message: "请至少选择一个枚举值" };
      }
      if (new Set(raw).size !== raw.length) {
        return { status: "error", message: "多选值不得重复" };
      }
      if (raw.some((item) => typeof item !== "string" || !values.includes(item))) {
        return { status: "error", message: "存在不在枚举内的值" };
      }
      return { status: "valid", value: raw };
    }
    case "BOOLEAN": {
      if (typeof raw !== "boolean") {
        return { status: "error", message: "请选择是或否" };
      }
      return { status: "valid", value: raw };
    }
    case "NUMBER": {
      if (typeof raw !== "string" || raw.trim() === "") {
        return { status: "error", message: "请输入数字" };
      }
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) {
        return { status: "error", message: "请输入有限数字" };
      }
      const min = readSpecNumber(definition.value_spec, "min_value");
      const max = readSpecNumber(definition.value_spec, "max_value");
      if (min !== undefined && parsed < min) {
        return { status: "error", message: `不得小于 ${min}` };
      }
      if (max !== undefined && parsed > max) {
        return { status: "error", message: `不得大于 ${max}` };
      }
      return { status: "valid", value: parsed };
    }
    case "STRING":
    case "FREE_TEXT": {
      if (typeof raw !== "string") {
        return { status: "error", message: "请输入文本" };
      }
      const maxLength = readSpecNumber(definition.value_spec, "max_length");
      if (maxLength !== undefined && raw.length > maxLength) {
        return { status: "error", message: `文本长度不得超过 ${maxLength}` };
      }
      return { status: "valid", value: raw };
    }
    case "TIME_RANGE":
    case "ACTION_SEGMENT":
      return parseTimeRangeValue(raw, definition.value_spec);
    case "OBJECT_TRACK": {
      if (typeof raw !== "string") {
        return { status: "error", message: "请输入 OBJECT_TRACK JSON" };
      }
      return parseObjectTrack(raw, definition.value_spec);
    }
    case "JSON": {
      if (typeof raw !== "string") {
        return { status: "error", message: "请输入 JSON" };
      }
      try {
        return { status: "valid", value: JSON.parse(raw) };
      } catch {
        return { status: "error", message: "JSON 格式非法" };
      }
    }
    default:
      return { status: "error", message: "不支持的标签类型" };
  }
}

/** 校验一条 Episode 的表单；错误字段带 episodeId 前缀，便于定位。 */
export function validateEpisodeValues(
  definitions: LabelDefinition[],
  episodeId: string,
  values: EpisodeFormValues,
): FieldIssue[] {
  const issues: FieldIssue[] = [];
  for (const definition of definitions) {
    const result = validateLabelValue(definition, values[definition.label_key]);
    if (result.status === "error") {
      issues.push({
        field: `${episodeId}.${definition.label_key}`,
        message: result.message ?? "标签值非法",
      });
    }
  }
  return issues;
}

export interface BuiltRecords {
  records: AnnotationWorkRecordInput[];
  issues: FieldIssue[];
}

/**
 * 将工作台表单值转换为提交 API 的记录载荷。
 *
 * 只写入用户已填写且通过校验的标签；必填缺失、类型/范围/时间片段错误
 * 都会阻止生成记录。服务端仍会再次做权威校验。
 */
export function buildSubmissionRecords(
  schema: AnnotationSchema,
  episodes: EpisodeValueInput[],
  valuesByEpisode: Record<string, EpisodeFormValues>,
): BuiltRecords {
  const records: AnnotationWorkRecordInput[] = [];
  const issues: FieldIssue[] = [];

  for (const episode of episodes) {
    const values = valuesByEpisode[episode.episodeId] ?? {};
    const labels: LabelValue[] = [];
    for (const definition of schema.labels) {
      const result = validateLabelValue(
        definition,
        values[definition.label_key],
      );
      if (result.status === "error") {
        issues.push({
          field: `${episode.episodeId}.${definition.label_key}`,
          message: result.message ?? "标签值非法",
        });
      } else if (result.status === "valid") {
        labels.push({
          label_key: definition.label_key,
          value: result.value,
          source: "HUMAN",
          label_status: "CONFIRMED",
        });
      }
    }
    if (labels.length === 0) {
      issues.push({
        field: `${episode.episodeId}.__record__`,
        message: "每条记录至少需要一个标签值",
      });
      continue;
    }
    records.push({
      episode_id: episode.episodeId,
      episode_version: episode.episodeVersion,
      labels,
    });
  }

  if (episodes.length === 0) {
    issues.push({
      field: "__record__",
      message: "当前分配没有可标注的 Episode",
    });
  }

  return { records, issues };
}

/** 从 Schema 生成空白表单值；布尔保持未选择，必填项由校验提示。 */
export function createEmptyFormValues(
  schema: AnnotationSchema,
): EpisodeFormValues {
  const values: EpisodeFormValues = {};
  for (const definition of schema.labels) {
    if (definition.label_type === "MULTI_ENUM") {
      values[definition.label_key] = [];
    } else if (TIME_RANGE_TYPES.has(definition.label_type)) {
      values[definition.label_key] = { start: "", end: "" };
    } else if (definition.label_type === "BOOLEAN") {
      values[definition.label_key] = null;
    } else {
      values[definition.label_key] = "";
    }
  }
  return values;
}
