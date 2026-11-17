import {
  Alert,
  DatePicker,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import type { Dayjs } from "dayjs";
import dayjs from "dayjs";
import { useEffect, useState } from "react";

import {
  CALIBRATION_TYPE_VALUES,
} from "../../types/device";
import {
  devicesApi,
  parseJsonObjectText,
  type CalibrationCreateInput,
} from "../../services/devices";

const CALIBRATION_TYPE_OPTIONS = CALIBRATION_TYPE_VALUES.map((value) => ({
  value,
  label: value,
}));

const UUID_PATTERN =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

interface CalibrationFormValues {
  profile_id: string;
  calibration_type: string;
  method: string;
  valid_from: Dayjs;
  valid_to?: Dayjs | null;
  parent_frame?: string;
  child_frame?: string;
  parameters?: string;
  source?: string;
  uncertainty?: string;
}

interface CalibrationCreateModalProps {
  projectId: string;
  deviceId: string;
  /** 设备已绑定的标定配置；用于预填，不代替服务端校验。 */
  calibrationProfileId?: string | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

function optionalText(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

/**
 * 登记一条设备标定记录（DRAFT）。
 *
 * `profile_id` 是标定配置版本行：设备若已绑定 `calibration_profile_id` 就预填，
 * 否则需粘贴该项目已登记配置的 UUID（服务端没有配置列表端点，前端不伪造下拉）。
 * `parameters` 由 Canonical 标定 Schema 约束结构，这里只校验是 JSON 对象。
 */
export default function CalibrationCreateModal({
  projectId,
  deviceId,
  calibrationProfileId = null,
  open,
  onClose,
  onSaved,
}: CalibrationCreateModalProps) {
  const [form] = Form.useForm<CalibrationFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        profile_id: calibrationProfileId ?? "",
        calibration_type: "INTRINSICS",
        method: "",
        valid_from: dayjs(),
        valid_to: null,
        parent_frame: "",
        child_frame: "",
        parameters: "",
        source: "",
        uncertainty: "",
      });
    }
  }, [open, calibrationProfileId, form]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      if (!values.valid_from) {
        throw new Error("请填写生效时间");
      }
      const input: CalibrationCreateInput = {
        profile_id: values.profile_id.trim(),
        target_type: "DEVICE",
        target_id: deviceId,
        calibration_type: values.calibration_type,
        method: values.method.trim(),
        parameters: parseJsonObjectText(values.parameters ?? "", "标定参数") ?? {},
        valid_from: values.valid_from.toISOString(),
        valid_to: values.valid_to ? values.valid_to.toISOString() : null,
        parent_frame: optionalText(values.parent_frame),
        child_frame: optionalText(values.child_frame),
        source: optionalText(values.source),
        uncertainty: parseJsonObjectText(values.uncertainty ?? "", "不确定度") ?? {},
      };
      setSubmitting(true);
      await devicesApi.createCalibration(projectId, input);
      messageApi.success("标定记录已登记（草稿）");
      form.resetFields();
      onSaved();
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
        title="登记标定记录"
        open={open}
        width={720}
        okText="登记标定"
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void submit()}
        onCancel={onClose}
        afterClose={() => form.resetFields()}
      >
        {calibrationProfileId ? null : (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="该设备未绑定标定配置"
            description="需要粘贴本项目已登记的标定配置 ID（calibration_profile_id）。服务端没有配置列表端点，前端不会伪造下拉选项。"
          />
        )}
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="profile_id"
            label="标定配置 ID"
            rules={[
              { required: true, message: "请填写标定配置 ID" },
              { pattern: UUID_PATTERN, message: "必须是合法 UUID" },
            ]}
            extra="标定配置是不可变版本；改配置请新建配置版本后再引用了新版本。"
          >
            <Input placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          </Form.Item>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="calibration_type"
              label="标定类型"
              rules={[{ required: true }]}
              style={{ flex: 1 }}
            >
              <Select options={CALIBRATION_TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="method"
              label="标定方法"
              rules={[{ required: true, max: 200 }]}
              style={{ flex: 2 }}
            >
              <Input placeholder="例如 charuco-board-8x6" />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="valid_from"
              label="生效时间"
              rules={[{ required: true }]}
              style={{ flex: 1 }}
            >
              <DatePicker showTime style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="valid_to" label="失效时间（可选）" style={{ flex: 1 }}>
              <DatePicker showTime style={{ width: "100%" }} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item name="parent_frame" label="父坐标系" style={{ flex: 1 }}>
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item name="child_frame" label="子坐标系" style={{ flex: 1 }}>
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item name="source" label="来源" style={{ flex: 1 }}>
              <Input maxLength={120} />
            </Form.Item>
          </Space>
          <Form.Item
            name="parameters"
            label="标定参数（JSON 对象）"
            extra="结构由 Canonical 标定 Schema 约束；留空提交 {}。"
          >
            <Input.TextArea rows={4} placeholder='{"fx": 610.5, "fy": 609.8}' />
          </Form.Item>
          <Form.Item name="uncertainty" label="不确定度（JSON 对象，可选）">
            <Input.TextArea rows={2} placeholder='{"translation_m": 0.002}' />
          </Form.Item>
          <Typography.Text type="secondary">
            标定记录以草稿状态创建；提交、校验、生效等状态动作在标定列表中执行，权限以服务端为准。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  );
}
