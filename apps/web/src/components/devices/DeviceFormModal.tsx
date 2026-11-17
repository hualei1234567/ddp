import { Form, Input, Modal, Select, Space, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  DEVICE_TYPE_VALUES,
  type Device,
} from "../../types/device";
import { getDeviceType } from "../../services/devicePresentation";
import {
  devicesApi,
  parseJsonObjectText,
  type DeviceCreateInput,
  type DevicePatchInput,
  type DeviceRecord,
} from "../../services/devices";
import { locationApi } from "../../services/locations";
import AsyncEntitySelect, { type EntityOption } from "./AsyncEntitySelect";

const DEVICE_TYPE_OPTIONS = DEVICE_TYPE_VALUES.map((value) => ({
  value,
  label: getDeviceType(value).label,
}));

interface DeviceFormValues {
  code: string;
  name: string;
  device_type: string;
  robot_id?: string | null;
  vendor?: string;
  model?: string;
  serial_number?: string;
  firmware_version?: string;
  hardware_version?: string;
  location_id?: string | null;
  /** JSON 对象文本；留空表示不设置。 */
  capability_profile?: string;
}

interface DeviceFormModalProps {
  projectId: string;
  mode: "create" | "edit";
  /** 编辑模式必填；创建模式忽略。 */
  device?: DeviceRecord | null;
  open: boolean;
  onClose: () => void;
  onSaved: (device: DeviceRecord) => void;
}

function toOptional(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

function initialValues(device: Device | null | undefined): DeviceFormValues {
  return {
    code: device?.code ?? "",
    name: device?.name ?? "",
    device_type: device?.device_type ?? "OTHER",
    robot_id: device?.robot_id ?? null,
    vendor: device?.vendor ?? "",
    model: device?.model ?? "",
    serial_number: device?.serial_number ?? "",
    firmware_version: device?.firmware_version ?? "",
    hardware_version: device?.hardware_version ?? "",
    location_id: device?.location_id ?? null,
    capability_profile:
      device && Object.keys(device.capability_profile ?? {}).length > 0
        ? JSON.stringify(device.capability_profile, null, 2)
        : "",
  };
}

/**
 * 新建设备 / 编辑设备档案。
 *
 * 关联的机器人与场地用下拉选择（真实列表接口），不手填 UUID；
 * `capability_profile` 是契约声明的自由对象，用 JSON 文本录入并校验为对象。
 * 编辑走 PATCH + If-Match，由服务端拒绝版本冲突。
 */
export default function DeviceFormModal({
  projectId,
  mode,
  device,
  open,
  onClose,
  onSaved,
}: DeviceFormModalProps) {
  const [form] = Form.useForm<DeviceFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  // 详情数据异步到达后再打开弹窗：每次打开都按当前数据重置表单，
  // 避免 initialValues 只生效于首次挂载导致编辑框为空。
  useEffect(() => {
    if (open) {
      form.setFieldsValue(initialValues(device));
    }
  }, [open, device, form]);

  const loadRobots = useMemo(
    () => async (signal: AbortSignal): Promise<EntityOption[]> => {
      const page = await devicesApi.listRobots(projectId, { signal });
      return page.items.map((robot) => ({
        value: robot.robot_id,
        label: robot.name,
        hint: robot.code,
      }));
    },
    [projectId],
  );

  const loadLocations = useMemo(
    () => async (signal: AbortSignal): Promise<EntityOption[]> => {
      const page = await locationApi.list(projectId, { signal });
      return page.items.map((location) => ({
        value: location.location_id,
        label: location.name,
        hint: `${location.code} · ${location.kind}`,
      }));
    },
    [projectId],
  );

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const capability = parseJsonObjectText(
        values.capability_profile ?? "",
        "能力描述",
      );
      const base = {
        code: values.code.trim(),
        name: values.name.trim(),
        device_type: values.device_type,
        robot_id: values.robot_id ?? null,
        vendor: toOptional(values.vendor),
        model: toOptional(values.model),
        serial_number: toOptional(values.serial_number),
        firmware_version: toOptional(values.firmware_version),
        hardware_version: toOptional(values.hardware_version),
        location_id: values.location_id ?? null,
      };
      setSubmitting(true);
      let saved: DeviceRecord;
      if (mode === "edit") {
        if (!device) {
          throw new Error("编辑模式缺少设备数据");
        }
        const patch: DevicePatchInput = { ...base };
        if (capability !== null) {
          patch.capability_profile = capability;
        }
        saved = await devicesApi.updateDevice(
          projectId,
          device.device_id,
          patch,
          { version: device.version },
        );
      } else {
        const input: DeviceCreateInput = { ...base };
        if (capability !== null) {
          input.capability_profile = capability;
        }
        saved = await devicesApi.createDevice(projectId, input);
      }
      messageApi.success(mode === "edit" ? "设备档案已更新" : "设备已创建");
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
        title={mode === "edit" ? "编辑设备档案" : "新建设备"}
        open={open}
        width={720}
        okText={mode === "edit" ? "保存" : "创建设备"}
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void submit()}
        onCancel={onClose}
        afterClose={() => form.resetFields()}
      >
        <Form
          form={form}
          layout="vertical"
          requiredMark
          initialValues={initialValues(device)}
        >
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="code"
              label="设备编码"
              rules={[{ required: true, max: 64 }]}
              style={{ flex: 1 }}
            >
              <Input placeholder="DEV-0001" />
            </Form.Item>
            <Form.Item
              name="name"
              label="设备名称"
              rules={[{ required: true, max: 200 }]}
              style={{ flex: 2 }}
            >
              <Input placeholder="主控计算单元" />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item
              name="device_type"
              label="设备类型"
              rules={[{ required: true }]}
              style={{ width: 200 }}
            >
              <Select options={DEVICE_TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item name="robot_id" label="挂载机器人" style={{ flex: 1 }}>
              <AsyncEntitySelect
                entityLabel="机器人"
                placeholder="选择机器人（可留空）"
                loadOptions={loadRobots}
              />
            </Form.Item>
          </Space>
          <Form.Item name="location_id" label="位置">
            <AsyncEntitySelect
              entityLabel="场地"
              placeholder="选择基地 / 房间 / 实验室（可留空）"
              loadOptions={loadLocations}
            />
          </Form.Item>
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
            <Form.Item name="firmware_version" label="固件版本" style={{ flex: 1 }}>
              <Input maxLength={64} />
            </Form.Item>
            <Form.Item name="hardware_version" label="硬件版本" style={{ flex: 1 }}>
              <Input maxLength={64} />
            </Form.Item>
          </Space>
          <Form.Item
            name="capability_profile"
            label="能力描述（JSON 对象，可选）"
            extra="能力字段由后续契约冻结；这里只校验是合法 JSON 对象，不解释键的语义。"
          >
            <Input.TextArea rows={4} placeholder='{"cameras": 2, "gripper": "two_finger"}' />
          </Form.Item>
          <Typography.Text type="secondary">
            状态、版本与审计字段由服务端维护；状态动作在设备详情页执行。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  );
}
