import { Form, Input, Modal, Select, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { ApiError } from "../../services/api";
import { presentLocationKind } from "../../services/locationPresentation";
import { locationApi } from "../../services/locations";
import { LOCATION_KIND_VALUES, type Location, type LocationKind } from "../../types/location";

interface LocationEditValues {
  name: string;
  kind: LocationKind;
  address?: string;
  notes?: string;
}

interface LocationEditModalProps {
  projectId: string;
  location: Location;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * 编辑场地：`code` 不可改（被设备与任务引用的业务标识）。
 * 显式清空 `address` / `notes` 会提交 `null`。
 */
export default function LocationEditModal({
  projectId,
  location,
  open,
  onClose,
  onSaved,
}: LocationEditModalProps) {
  const [form] = Form.useForm<LocationEditValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    if (!open) {
      return;
    }
    form.setFieldsValue({
      name: location.name,
      kind: location.kind,
      address: location.address ?? "",
      notes: location.notes ?? "",
    });
  }, [open, location, form]);

  const submit = async () => {
    let values: LocationEditValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    try {
      setSubmitting(true);
      await locationApi.update(
        projectId,
        location.location_id,
        {
          name: values.name.trim(),
          kind: values.kind,
          address: values.address?.trim() ? values.address.trim() : null,
          notes: values.notes?.trim() ? values.notes.trim() : null,
        },
        { version: location.version },
      );
      messageApi.success("场地已保存");
      onSaved();
      onClose();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("场地已被他人修改，请刷新后重试。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "保存失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="编辑场地"
        open={open}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Typography.Paragraph type="secondary">
          编码不可修改（它是被设备与采集任务引用的业务标识）。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item label="编码">
            <Input value={location.code} disabled />
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
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
