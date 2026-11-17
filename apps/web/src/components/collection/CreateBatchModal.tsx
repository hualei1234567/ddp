import { Alert, DatePicker, Form, Input, InputNumber, Modal, message } from "antd";
import type { Dayjs } from "dayjs";
import { useState } from "react";

import AsyncEntitySelect, {
  type EntityOption,
} from "../devices/AsyncEntitySelect";
import { collectionsApi } from "../../services/collections";
import { projectManagementApi } from "../../services/projectManagement";

interface BatchFormValues {
  expected_count: number;
  code?: string;
  sequence_no?: number | null;
  assigned_to?: string | null;
  scheduled_start?: Dayjs | null;
  scheduled_end?: Dayjs | null;
}

interface CreateBatchModalProps {
  projectId: string;
  taskId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function toIso(value: Dayjs | null | undefined): string | null {
  return value ? value.toISOString() : null;
}

/**
 * 新建采集批次。
 *
 * `code` / `sequence_no` 留空时由服务端生成（项目内 code 唯一，序号在任务内递增）；
 * 执行人从项目成员下拉选择，不手填 UUID。批次预计数量之和不得超过任务目标，
 * 该规则由服务端校验，前端只把服务端错误原样展示。
 */
export default function CreateBatchModal({
  projectId,
  taskId,
  open,
  onClose,
  onCreated,
}: CreateBatchModalProps) {
  const [form] = Form.useForm<BatchFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const loadMembers = async (signal: AbortSignal): Promise<EntityOption[]> => {
    const page = await projectManagementApi.listMembers(projectId, { signal });
    return page.items.map((member) => ({
      value: member.user_id,
      label: member.display_name,
      hint: member.role,
    }));
  };

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await collectionsApi.createBatch(projectId, taskId, {
        expected_count: values.expected_count,
        code: values.code?.trim() || null,
        sequence_no: values.sequence_no ?? null,
        assigned_to: values.assigned_to ?? null,
        scheduled_start: toIso(values.scheduled_start),
        scheduled_end: toIso(values.scheduled_end),
      });
      messageApi.success("采集批次已创建");
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      if (caught instanceof Error) {
        messageApi.error(caught.message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="新建采集批次"
        open={open}
        width={640}
        okText="创建批次"
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void submit()}
        onCancel={onClose}
        afterClose={() => form.resetFields()}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="批次预计数量之和不超任务目标"
          description="编码与序号可留空由服务端生成；批次创建后默认处于计划状态，后续动作以服务端 allowed_actions 为准。"
        />
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="expected_count"
            label="预计条数"
            rules={[
              { required: true },
              {
                type: "integer",
                min: 0,
                message: "预计条数必须是 ≥0 的整数",
              },
            ]}
          >
            <InputNumber min={0} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="code" label="批次编码" rules={[{ max: 64 }]}>
            <Input placeholder="留空由服务端生成，例如 B-1a2b3c4d-0001" />
          </Form.Item>
          <Form.Item
            name="sequence_no"
            label="序号"
            rules={[
              {
                type: "integer",
                min: 1,
                message: "序号必须是 ≥1 的整数",
              },
            ]}
          >
            <InputNumber min={1} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="assigned_to" label="执行人">
            <AsyncEntitySelect
              entityLabel="项目成员"
              placeholder="选择项目成员"
              loadOptions={loadMembers}
            />
          </Form.Item>
          <Form.Item name="scheduled_start" label="计划开始">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="scheduled_end" label="计划结束">
            <DatePicker showTime style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
