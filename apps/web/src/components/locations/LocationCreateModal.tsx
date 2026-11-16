import { Form, Input, Modal, Select, Typography, message } from "antd";
import { useState } from "react";

import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { presentLocationKind } from "../../services/locationPresentation";
import { locationApi } from "../../services/locations";
import {
  LOCATION_KIND_VALUES,
  type LocationKind,
} from "../../types/location";

interface LocationCreateValues {
  code: string;
  name: string;
  kind: LocationKind;
  address?: string;
  notes?: string;
}

interface LocationCreateModalProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

/** 创建场地：`kind` 缺省 `SITE`（与契约定的一致，由服务端兜底）。 */
export default function LocationCreateModal({
  projectId,
  open,
  onClose,
  onCreated,
}: LocationCreateModalProps) {
  const [form] = Form.useForm<LocationCreateValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const online = useOnlineStatus();

  const submit = async () => {
    let values: LocationCreateValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    try {
      setSubmitting(true);
      await locationApi.create(projectId, {
        code: values.code.trim(),
        name: values.name.trim(),
        kind: values.kind ?? "SITE",
        address: values.address?.trim() ? values.address.trim() : null,
        notes: values.notes?.trim() ? values.notes.trim() : null,
      });
      messageApi.success("场地已创建");
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "创建失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="新建场地"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !online }}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        {!online ? (
          <Typography.Paragraph type="warning">
            当前处于离线状态，无法提交创建请求。
          </Typography.Paragraph>
        ) : null}
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="编码"
            rules={[
              { required: true, message: "请输入编码" },
              { max: 64, message: "编码最多 64 字符" },
            ]}
          >
            <Input placeholder="项目内唯一；创建后不可修改" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: "请输入名称" },
              { max: 200, message: "名称最多 200 字符" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="kind"
            label="类型"
            initialValue="SITE"
            rules={[{ required: true, message: "请选择类型" }]}
          >
            <Select
              options={LOCATION_KIND_VALUES.map((value) => ({
                value,
                label: presentLocationKind(value).label,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="address"
            label="地址"
            rules={[{ max: 500, message: "地址最多 500 字符" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="notes"
            label="备注"
            rules={[{ max: 2000, message: "备注最多 2000 字符" }]}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
