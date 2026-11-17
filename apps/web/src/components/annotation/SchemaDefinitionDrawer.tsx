import { Alert, Button, Drawer, Form, Input, Select, Space, Typography } from "antd";
import { useEffect, useState } from "react";

import type {
  AnnotationSchema,
  AnnotationLevel,
} from "../../types/annotation";
import {
  ANNOTATION_LEVEL_VALUES,
  SCHEMA_COMPATIBILITY_VALUES,
} from "../../types/annotation";
import {
  formValuesToLabels,
  labelsToFormValues,
  type LabelFormValues,
} from "../../services/annotationSchemaForm";
import type {
  AnnotationSchemaCreateInput,
  AnnotationSchemaPatchInput,
} from "../../services/annotationSchemaApi";
import LabelDefinitionEditor from "./LabelDefinitionEditor";

interface SchemaFormValues {
  code: string;
  name: string;
  description?: string | null;
  compatibility: string;
  annotation_levels: AnnotationLevel[];
  labels: LabelFormValues[];
}

export interface SchemaDefinitionSubmitPayload {
  mode: "create" | "edit";
  input: AnnotationSchemaCreateInput | AnnotationSchemaPatchInput;
}

interface SchemaDefinitionDrawerProps {
  open: boolean;
  mode: "create" | "edit";
  schema?: AnnotationSchema | null;
  onClose: () => void;
  onSubmit: (payload: SchemaDefinitionSubmitPayload) => Promise<void>;
}

const levelOptions = ANNOTATION_LEVEL_VALUES.map((value) => ({
  value,
  label: value,
}));
const compatibilityOptions = SCHEMA_COMPATIBILITY_VALUES.map((value) => ({
  value,
  label: value,
}));

function initialValues(mode: "create" | "edit", schema?: AnnotationSchema | null): SchemaFormValues {
  if (mode === "edit" && schema) {
    return {
      code: schema.code,
      name: schema.name,
      description: schema.description,
      compatibility: schema.compatibility,
      annotation_levels: schema.annotation_levels,
      labels: labelsToFormValues(schema.labels),
    };
  }
  return {
    code: "",
    name: "",
    description: null,
    compatibility: "BACKWARD_COMPATIBLE",
    annotation_levels: ["EPISODE"],
    labels: [],
  };
}

/**
 * 新建 / 编辑 Schema 抽屉。
 *
 * 编辑只对 DRAFT 开放（页面按状态决定入口）；服务端对非 DRAFT 返回
 * `schema_immutable`，错误原文通过 Alert 展示，不在前端复制该规则。
 */
export default function SchemaDefinitionDrawer({
  open,
  mode,
  schema,
  onClose,
  onSubmit,
}: SchemaDefinitionDrawerProps) {
  const [form] = Form.useForm<SchemaFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    form.setFieldsValue(initialValues(mode, schema));
  }, [form, mode, open, schema]);

  const handleSubmit = async () => {
    let values: SchemaFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const labels = formValuesToLabels(values.labels);
      const base = {
        name: values.name.trim(),
        description: values.description?.trim() ? values.description.trim() : null,
        annotation_levels: values.annotation_levels,
        labels,
        compatibility: values.compatibility,
      };
      const payload: SchemaDefinitionSubmitPayload =
        mode === "create"
          ? {
              mode,
              input: { ...base, code: values.code.trim() },
            }
          : { mode, input: base };
      await onSubmit(payload);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "保存 Schema 失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={mode === "create" ? "新建标注 Schema" : "编辑 Schema 定义"}
      width={760}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
            保存
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        <Typography.Paragraph type="secondary">
          Schema 保存后为 DRAFT；发布需经提交审核与发布动作。已发布版本不可原地修改。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" initialValues={initialValues(mode, schema)}>
          <Form.Item
            name="code"
            label="编码"
            rules={[{ required: true, message: "请填写编码" }]}
          >
            <Input maxLength={64} disabled={mode === "edit"} placeholder="例如 kitchen-action" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请填写名称" }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
          <Form.Item
            name="annotation_levels"
            label="标注层级"
            rules={[{ required: true, message: "至少选择一个层级" }]}
          >
            <Select mode="multiple" options={levelOptions} />
          </Form.Item>
          <Form.Item
            name="compatibility"
            label="兼容性"
            rules={[{ required: true, message: "请选择兼容性" }]}
          >
            <Select options={compatibilityOptions} />
          </Form.Item>
          <Typography.Text strong>标签定义</Typography.Text>
          <LabelDefinitionEditor />
        </Form>
      </Space>
    </Drawer>
  );
}
