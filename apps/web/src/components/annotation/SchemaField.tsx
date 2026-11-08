import { Input, Select, Space, Typography } from "antd";

import type {
  DraftFieldValue,
  TimeRangeInput,
} from "../../services/annotationValidation";
import type { LabelDefinition } from "../../types/annotation";

interface SchemaFieldProps {
  definition: LabelDefinition;
  value: DraftFieldValue | undefined;
  disabled: boolean;
  error?: string;
  onChange: (value: DraftFieldValue) => void;
}

function enumValues(definition: LabelDefinition): string[] {
  const raw = definition.value_spec.enum_values;
  return Array.isArray(raw)
    ? raw.filter((item): item is string => typeof item === "string")
    : [];
}

function asTimeRange(value: DraftFieldValue | undefined): TimeRangeInput {
  if (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as TimeRangeInput).start === "string" &&
    typeof (value as TimeRangeInput).end === "string"
  ) {
    return value as TimeRangeInput;
  }
  return { start: "", end: "" };
}

function numericSpec(
  definition: LabelDefinition,
  key: string,
): number | undefined {
  const raw = definition.value_spec[key];
  return typeof raw === "number" ? raw : undefined;
}

/**
 * 按 Schema 标签类型渲染表单控件。所有控件只编辑本地草稿，
 * 服务端仍是类型、范围与完整性校验的权威来源。
 */
export default function SchemaField({
  definition,
  value,
  disabled,
  error,
  onChange,
}: SchemaFieldProps) {
  const requiredMark = definition.required ? (
    <Typography.Text type="danger"> *</Typography.Text>
  ) : null;
  const commonLabel = (
    <Typography.Text strong>
      {definition.label_key}
      {requiredMark}
    </Typography.Text>
  );

  let control;
  switch (definition.label_type) {
    case "ENUM":
      control = (
        <Select
          aria-label={definition.label_key}
          value={typeof value === "string" && value !== "" ? value : undefined}
          options={enumValues(definition).map((item) => ({
            value: item,
            label: item,
          }))}
          disabled={disabled}
          allowClear={!definition.required}
          placeholder="请选择"
          onChange={(next) => onChange(next ?? "")}
        />
      );
      break;
    case "MULTI_ENUM":
      control = (
        <Select
          aria-label={definition.label_key}
          mode="multiple"
          value={Array.isArray(value) ? value : []}
          options={enumValues(definition).map((item) => ({
            value: item,
            label: item,
          }))}
          disabled={disabled}
          placeholder="请选择（可多选）"
          onChange={(next) => onChange(next)}
        />
      );
      break;
    case "BOOLEAN":
      control = (
        <Select
          aria-label={definition.label_key}
          value={typeof value === "boolean" ? value : undefined}
          options={[
            { value: true, label: "是" },
            { value: false, label: "否" },
          ]}
          disabled={disabled}
          allowClear={!definition.required}
          placeholder="请选择"
          onChange={(next) =>
            onChange(typeof next === "boolean" ? next : null)
          }
        />
      );
      break;
    case "NUMBER":
      control = (
        <Input
          aria-label={definition.label_key}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          inputMode="decimal"
          placeholder="请输入数字"
          onChange={(event) => onChange(event.target.value)}
          suffix={definition.value_spec.unit ? String(definition.value_spec.unit) : undefined}
        />
      );
      break;
    case "TIME_RANGE":
    case "ACTION_SEGMENT": {
      const range = asTimeRange(value);
      control = (
        <Space wrap>
          <Input
            aria-label={`${definition.label_key} 开始时间`}
            value={range.start}
            disabled={disabled}
            placeholder="开始 time_ns"
            onChange={(event) =>
              onChange({ ...range, start: event.target.value })
            }
            style={{ width: 190 }}
          />
          <Input
            aria-label={`${definition.label_key} 结束时间`}
            value={range.end}
            disabled={disabled}
            placeholder="结束 time_ns"
            onChange={(event) =>
              onChange({ ...range, end: event.target.value })
            }
            style={{ width: 190 }}
          />
        </Space>
      );
      const minDuration = numericSpec(definition, "min_duration_ns");
      if (minDuration !== undefined) {
        control = (
          <Space direction="vertical" size={4}>
            {control}
            <Typography.Text type="secondary">
              最小片段时长 {minDuration} ns
            </Typography.Text>
          </Space>
        );
      }
      break;
    }
    case "OBJECT_TRACK":
    case "JSON":
      control = (
        <Input.TextArea
          aria-label={definition.label_key}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          rows={4}
          placeholder={
            definition.label_type === "OBJECT_TRACK"
              ? '[{"start_time_ns": 0, "end_time_ns": 1000}]'
              : "请输入合法 JSON"
          }
          onChange={(event) => onChange(event.target.value)}
        />
      );
      break;
    case "STRING":
      control = (
        <Input
          aria-label={definition.label_key}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          maxLength={numericSpec(definition, "max_length")}
          onChange={(event) => onChange(event.target.value)}
        />
      );
      break;
    case "FREE_TEXT":
    default:
      control = (
        <Input.TextArea
          aria-label={definition.label_key}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          maxLength={numericSpec(definition, "max_length")}
          rows={3}
          onChange={(event) => onChange(event.target.value)}
        />
      );
      break;
  }

  return (
    <div className="ddp-annotation-field">
      <Space direction="vertical" size={4} className="ddp-stack">
        <Space wrap size={6}>
          {commonLabel}
          <Typography.Text type="secondary">
            {definition.label_type} · {definition.level}
          </Typography.Text>
        </Space>
        {definition.description ? (
          <Typography.Text type="secondary">
            {definition.description}
          </Typography.Text>
        ) : null}
        {control}
        {error ? (
          <Typography.Text type="danger">{error}</Typography.Text>
        ) : null}
      </Space>
    </div>
  );
}
