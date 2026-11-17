import { Form, Input, Modal, Space, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  devicesApi,
  parseJsonObjectText,
  type RobotCreateInput,
  type RobotPatchInput,
  type RobotRecord,
} from "../../services/devices";
import { locationApi } from "../../services/locations";
import AsyncEntitySelect, { type EntityOption } from "./AsyncEntitySelect";

interface RobotFormValues {
  code: string;
  name: string;
  robot_type: string;
  vendor?: string;
  model?: string;
  serial_number?: string;
  firmware_version?: string;
  hardware_version?: string;
  location_id?: string | null;
  capability_profile?: string;
}

interface RobotFormModalProps {
  projectId: string;
  mode: "create" | "edit";
  robot?: RobotRecord | null;
  open: boolean;
  onClose: () => void;
  onSaved: (robot: RobotRecord) => void;
}

function optionalText(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

function initialValues(robot: RobotRecord | null | undefined): RobotFormValues {
  return {
    code: robot?.code ?? "",
    name: robot?.name ?? "",
    robot_type: robot?.robot_type ?? "",
    vendor: robot?.vendor ?? "",
    model: robot?.model ?? "",
    serial_number: robot?.serial_number ?? "",
    firmware_version: robot?.firmware_version ?? "",
    hardware_version: robot?.hardware_version ?? "",
    location_id: robot?.location_id ?? null,
    capability_profile:
      robot && Object.keys(robot.capability_profile ?? {}).length > 0
        ? JSON.stringify(robot.capability_profile, null, 2)
        : "",
  };
}

/**
 * 新建 / 编辑机器人档案。
 *
 * `robot_type` 是厂商差异字段（如 HUMANOID / QUADRUPED / ARM），契约未定义封闭枚举，
 * 因此用文本录入并在服务端校验，不在这里硬编码型号。
 */
export default function RobotFormModal({
  projectId,
  mode,
  robot,
  open,
  onClose,
  onSaved,
}: RobotFormModalProps) {
  const [form] = Form.useForm<RobotFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(initialValues(robot));
    }
  }, [open, robot, form]);

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
        robot_type: values.robot_type.trim(),
        vendor: optionalText(values.vendor),
        model: optionalText(values.model),
        serial_number: optionalText(values.serial_number),
        firmware_version: optionalText(values.firmware_version),
        hardware_version: optionalText(values.hardware_version),
        location_id: values.location_id ?? null,
      };
      setSubmitting(true);
      let saved: RobotRecord;
      if (mode === "edit") {
        if (!robot) {
          throw new Error("编辑模式缺少机器人数据");
        }
        const patch: RobotPatchInput = { ...base };
        if (capability !== null) {
          patch.capability_profile = capability;
        }
        saved = await devicesApi.updateRobot(projectId, robot.robot_id, patch, {
          version: robot.version,
        });
      } else {
        const input: RobotCreateInput = { ...base };
        if (capability !== null) {
          input.capability_profile = capability;
        }
        saved = await devicesApi.createRobot(projectId, input);
      }
      messageApi.success(mode === "edit" ? "机器人档案已更新" : "机器人已创建");
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
        title={mode === "edit" ? "编辑机器人档案" : "新建机器人"}
        open={open}
        width={720}
        okText={mode === "edit" ? "保存" : "创建机器人"}
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
              label="机器人编码"
              rules={[{ required: true, max: 64 }]}
              style={{ flex: 1 }}
            >
              <Input placeholder="ROBOT-0001" />
            </Form.Item>
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, max: 200 }]}
              style={{ flex: 2 }}
            >
              <Input placeholder="主机器人" />
            </Form.Item>
          </Space>
          <Form.Item
            name="robot_type"
            label="机器人类型"
            rules={[{ required: true, max: 64 }]}
            extra="例如 HUMANOID / QUADRUPED / ARM；取值由服务端与后续契约冻结，前端不硬编码型号。"
          >
            <Input placeholder="HUMANOID" />
          </Form.Item>
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
            extra="结构由后续契约冻结；这里只校验是合法 JSON 对象。"
          >
            <Input.TextArea rows={4} placeholder='{"dof": 7, "end_effector": "gripper"}' />
          </Form.Item>
          <Typography.Text type="secondary">
            状态、版本与审计字段由服务端维护；生命周期动作在机器人详情页执行。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  );
}
