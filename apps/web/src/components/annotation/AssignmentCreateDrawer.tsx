import { Alert, Button, DatePicker, Drawer, Form, Input, InputNumber, Select, Space, Typography } from "antd";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";

import type { AnnotationAssignmentCreateInput } from "../../services/annotationTaskApi";
import type { AnnotationBatch } from "../../types/annotation";
import { ASSIGNMENT_ROLE_VALUES } from "../../types/annotation";

interface AssignmentFormValues {
  batch_id: string;
  assignee_id: string;
  assignment_role: string;
  planned_item_count: number;
  planned_minutes: number;
  lease_expires_at?: Dayjs | null;
}

interface AssignmentCreateDrawerProps {
  open: boolean;
  batches: AnnotationBatch[];
  onClose: () => void;
  onSubmit: (input: AnnotationAssignmentCreateInput) => Promise<void>;
}

const roleOptions = ASSIGNMENT_ROLE_VALUES.map((value) => ({ value, label: value }));

/** 新建人员分配；`batch_id` 必须来自当前任务的批次列表。 */
export default function AssignmentCreateDrawer({
  open,
  batches,
  onClose,
  onSubmit,
}: AssignmentCreateDrawerProps) {
  const [form] = Form.useForm<AssignmentFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    form.resetFields();
    form.setFieldsValue({
      batch_id: batches[0]?.batch_id,
      assignee_id: "",
      assignment_role: "ANNOTATOR",
      planned_item_count: 0,
      planned_minutes: 0,
      lease_expires_at: null,
    });
  }, [batches, form, open]);

  const handleSubmit = async () => {
    if (batches.length === 0) {
      setError("任务尚未创建批次，请先新建批次再指派人员。");
      return;
    }
    let values: AssignmentFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        batch_id: values.batch_id,
        assignee_id: values.assignee_id.trim(),
        assignment_role: values.assignment_role,
        planned_item_count: values.planned_item_count,
        planned_minutes: values.planned_minutes,
        lease_expires_at: values.lease_expires_at
          ? values.lease_expires_at.toISOString()
          : null,
      });
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "新建分配失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="新建分配"
      width={560}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={submitting}
            disabled={batches.length === 0}
            onClick={() => void handleSubmit()}
          >
            创建
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        {batches.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="任务尚未创建批次，请先新建批次再指派人员。"
          />
        ) : null}
        <Typography.Paragraph type="secondary">
          容量上限由服务端策略校验；超限时服务端拒绝并返回原因。
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item
            name="batch_id"
            label="批次"
            rules={[{ required: true, message: "请选择批次" }]}
          >
            <Select
              options={batches.map((batch) => ({
                value: batch.batch_id,
                label: `#${batch.sequence_no} ${batch.code} · ${batch.status}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="assignee_id"
            label="人员 ID"
            rules={[{ required: true, message: "请填写人员 UUID" }]}
          >
            <Input placeholder="项目成员 UUID" />
          </Form.Item>
          <Form.Item name="assignment_role" label="角色">
            <Select options={roleOptions} />
          </Form.Item>
          <Form.Item name="planned_item_count" label="计划项数">
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="planned_minutes" label="计划分钟">
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="lease_expires_at" label="租约到期（可选）">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Space>
    </Drawer>
  );
}
