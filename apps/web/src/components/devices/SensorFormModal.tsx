import { Form, Input, InputNumber, Modal, Select, Space, Typography, message } from "antd";
import { useEffect, useState } from "react";

import {
  FRAME_CONVENTION_VALUES,
  SENSOR_SYNC_MODE_VALUES,
  SENSOR_TYPE_VALUES,
  type Sensor,
} from "../../types/device";
import { getSensorType } from "../../services/devicePresentation";
import {
  devicesApi,
  type SensorCreateInput,
  type SensorPatchInput,
  type SensorRecord,
} from "../../services/devices";

const SENSOR_TYPE_OPTIONS = SENSOR_TYPE_VALUES.map((value) => ({
  value,
  label: getSensorType(value).label,
}));

const FRAME_CONVENTION_OPTIONS = FRAME_CONVENTION_VALUES.map((value) => ({
  value,
  label: value,
}));

const SYNC_MODE_OPTIONS = SENSOR_SYNC_MODE_VALUES.map((value) => ({
  value,
  label: value,
}));

interface SensorFormValues {
  code: string;
  sensor_type: string;
  sample_rate_hz: number;
  frame_id: string;
  frame_convention: string;
  sync_mode: string;
  sync_group?: string;
  resolution_w?: number | null;
  resolution_h?: number | null;
  bit_depth?: number | null;
  channels?: number | null;
  vendor?: string;
  model?: string;
  serial_number?: string;
  mount_position?: string;
  firmware_version?: string;
}

interface SensorFormModalProps {
  projectId: string;
  deviceId: string;
  mode: "create" | "edit";
  sensor?: SensorRecord | null;
  open: boolean;
  onClose: () => void;
  onSaved: (sensor: SensorRecord) => void;
}

function toOptional(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

function initialValues(sensor: Sensor | null | undefined): SensorFormValues {
  return {
    code: sensor?.code ?? "",
    sensor_type: sensor?.sensor_type ?? "RGB",
    sample_rate_hz: sensor?.sample_rate_hz ?? 30,
    frame_id: sensor?.frame_id ?? sensor?.coordinate_frame ?? "",
    frame_convention: sensor?.frame_convention ?? "UNSPECIFIED",
    sync_mode: sensor?.sync_mode ?? "NONE",
    sync_group: sensor?.sync_group ?? "",
    resolution_w: sensor?.resolution_w ?? null,
    resolution_h: sensor?.resolution_h ?? null,
    bit_depth: sensor?.bit_depth ?? null,
    channels: sensor?.channels ?? null,
    vendor: sensor?.vendor ?? "",
    model: sensor?.model ?? "",
    serial_number: sensor?.serial_number ?? "",
    mount_position: sensor?.mount_position ?? "",
    firmware_version: sensor?.firmware_version ?? "",
  };
}

/**
 * 新建 / 编辑传感器档案。
 *
 * 坐标系按 ADR-0019 填 `frame_id`；阶段 1 契约仍要求 `coordinate_frame`，
 * 表单用同一个值下发两处，避免两个字段漂移。`UNSPECIFIED` 是显式"未声明"，
 * 不默认成 ROS 约定。
 */
export default function SensorFormModal({
  projectId,
  deviceId,
  mode,
  sensor,
  open,
  onClose,
  onSaved,
}: SensorFormModalProps) {
  const [form] = Form.useForm<SensorFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initialValues(sensor));
    }
  }, [open, sensor, form]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const frameId = values.frame_id.trim();
      const base = {
        code: values.code.trim(),
        sensor_type: values.sensor_type,
        sample_rate_hz: values.sample_rate_hz,
        frame_id: frameId,
        coordinate_frame: frameId,
        frame_convention: values.frame_convention,
        sync_mode: values.sync_mode,
        sync_group: toOptional(values.sync_group),
        resolution_w: values.resolution_w ?? null,
        resolution_h: values.resolution_h ?? null,
        bit_depth: values.bit_depth ?? null,
        channels: values.channels ?? null,
        vendor: toOptional(values.vendor),
        model: toOptional(values.model),
        serial_number: toOptional(values.serial_number),
        mount_position: toOptional(values.mount_position),
        firmware_version: toOptional(values.firmware_version),
      };
      setSubmitting(true);
      let saved: SensorRecord;
      if (mode === "edit") {
        if (!sensor) {
          throw new Error("编辑模式缺少传感器数据");
        }
        const patch: SensorPatchInput = { ...base };
        saved = await devicesApi.updateSensor(
          projectId,
          sensor.sensor_id,
          patch,
          { version: sensor.version },
        );
      } else {
        const input: SensorCreateInput = { ...base, device_id: deviceId };
        saved = await devicesApi.createSensor(projectId, input);
      }
      messageApi.success(mode === "edit" ? "传感器已更新" : "传感器已创建");
      form.resetFields();
      onSaved(saved);
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
        title={mode === "edit" ? "编辑传感器" : "新建传感器"}
        open={open}
        width={760}
        okText={mode === "edit" ? "保存" : "创建传感器"}
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void submit()}
        onCancel={onClose}
        afterClose={() => form.resetFields()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="code"
              label="编码"
              rules={[{ required: true, max: 64 }]}
              style={{ flex: 1 }}
            >
              <Input placeholder="CAM-FRONT-01" />
            </Form.Item>
            <Form.Item
              name="sensor_type"
              label="类型"
              rules={[{ required: true }]}
              style={{ width: 180 }}
            >
              <Select options={SENSOR_TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item
              name="sample_rate_hz"
              label="采样率"
              rules={[{ required: true, type: "number", min: 0.001 }]}
              style={{ width: 160 }}
            >
              <InputNumber min={0.001} addonAfter="Hz" style={{ width: "100%" }} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="frame_id"
              label="坐标系 frame_id"
              rules={[{ required: true, max: 200 }]}
              extra="例如 camera_front_link；不确定来源时保持 frame_convention=UNSPECIFIED。"
              style={{ flex: 2 }}
            >
              <Input />
            </Form.Item>
            <Form.Item name="frame_convention" label="坐标约定" style={{ flex: 1 }}>
              <Select options={FRAME_CONVENTION_OPTIONS} />
            </Form.Item>
            <Form.Item name="sync_mode" label="同步方式" style={{ flex: 1 }}>
              <Select options={SYNC_MODE_OPTIONS} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item name="resolution_w" label="分辨率宽" style={{ flex: 1 }}>
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="resolution_h" label="分辨率高" style={{ flex: 1 }}>
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="bit_depth" label="位深" style={{ flex: 1 }}>
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item name="channels" label="通道数" style={{ flex: 1 }}>
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item name="vendor" label="厂商" style={{ flex: 1 }}>
              <Input maxLength={120} />
            </Form.Item>
            <Form.Item name="model" label="型号" style={{ flex: 1 }}>
              <Input maxLength={120} />
            </Form.Item>
            <Form.Item name="serial_number" label="序列号" style={{ flex: 1 }}>
              <Input maxLength={120} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item name="sync_group" label="同步组" style={{ flex: 1 }}>
              <Input maxLength={64} />
            </Form.Item>
            <Form.Item name="mount_position" label="安装位置" style={{ flex: 1 }}>
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item name="firmware_version" label="固件版本" style={{ flex: 1 }}>
              <Input maxLength={64} />
            </Form.Item>
          </Space>
          <Typography.Text type="secondary">
            传感器归属当前设备；跨设备的标定引用需在标定记录中登记。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  );
}
