import { Alert, Button, DatePicker, Drawer, Form, Input, InputNumber, Select, Space, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";

import type { AnnotationBatchCreateInput } from "../../services/annotationTaskApi";

interface BatchFormValues {
  planned_item_count: number;
  code?: string | null;
  name?: string | null;
  sequence_no?: number | null;
  stratum?: string | null;
  due_at?: Dayjs | null;
  item_refs?: string[];
}

interface BatchCreateDrawerProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (input: AnnotationBatchCreateInput) => Promise<void>;
}

function toIso(value: Dayjs | null | undefined): string | null {
  return value ? value.toISOString() : null;
}

function cleaned(values: string[] | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter((value) => value !== "");
}

/** 任务拆分批次：只提交计划项数等契约字段，批次编号由服务端生成。 */
export default function BatchCreateDrawer({
  open,
  onClose,
  onSubmit,
}: BatchCreateDrawerProps) {
  const [form] = Form.useForm<BatchFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    form.resetFields();
    form.setFieldsValue({
      planned_item_count: 0,
      code: null,
      name: null,
      sequence_no: null,
      stratum: null,
      due_at: null,
      item_refs: [],
    });
  }, [form, open]);

  const handleSubmit = async () => {
    let values: BatchFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const input: AnnotationBatchCreateInput = {
        planned_item_count: values.planned_item_count,
        code: values.code?.trim() ? values.code.trim() : null,
        name: values.name?.trim() ? values.name.trim() : null,
        sequence_no: values.sequence_no ?? null,
        stratum: values.stratum?.trim() ? values.stratum.trim() : null,
        item_refs: cleaned(values.item_refs),
        due_at: toIso(values.due_at),
      };
      await onSubmit(input);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "新建批次失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="新建批次"
      width={560}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
            创建
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        <Typography.Paragraph type="secondary">
          批次是任务内的计划单元；创建后由批次动作推进就绪、指派与开始。
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item
            name="planned_item_count"
            label="计划项数"
            rules={[{ required: true, message: "请填写计划项数" }]}
          >
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="code" label="编码（可选）">
            <Input maxLength={64} placeholder="留空由服务端生成" />
          </Form.Item>
          <Form.Item name="name" label="名称（可选）">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="sequence_no" label="序号（可选）">
            <InputNumber min={1} precision={0} style={{ width: "100%" }} placeholder="留空由服务端生成" />
          </Form.Item>
          <Form.Item name="stratum" label="分层（可选）">
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="due_at" label="截止时间（可选）">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="item_refs"
            label="条目引用（可选）"
            tooltip="输入条目 UUID 后回车；留空表示由后续分配决定"
          >
            <Select mode="tags" open={false} tokenSeparators={[",", " "]} placeholder="回车添加条目 ID" />
          </Form.Item>
        </Form>
      </Space>
    </Drawer>
  );
}
