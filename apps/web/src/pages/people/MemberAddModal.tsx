import { Alert, Form, Input, Modal, Select, message } from "antd";
import { useState } from "react";

import { projectManagementApi } from "../../services/projectManagement";
import { PROJECT_ROLE_LABELS } from "../../services/projectPresentation";
import { PROJECT_ROLES, type ProjectRole } from "../../types/project";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface MemberFormValues {
  user_id: string;
  role: ProjectRole;
}

interface MemberAddModalProps {
  open: boolean;
  projectId: string;
  onClose: () => void;
  onAdded: () => void;
}

/**
 * 添加项目成员。
 *
 * 本阶段没有"邀请/注册用户"能力：用户目录由运维登记，因此这里要求填写已存在的
 * 用户 ID。填错会得到 404（用户不存在）或 409（已是成员），服务端不会静默创建用户。
 */
export default function MemberAddModal({
  open,
  projectId,
  onClose,
  onAdded,
}: MemberAddModalProps) {
  const [form] = Form.useForm<MemberFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await projectManagementApi.addMember(projectId, {
        user_id: values.user_id.trim().toLowerCase(),
        role: values.role,
      });
      messageApi.success("成员已添加");
      form.resetFields();
      onAdded();
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
        title="添加项目成员"
        open={open}
        okText="添加"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="需要已登记的用户 ID"
          description="平台当前不提供邀请或注册入口；请向平台管理员获取用户 ID。添加成功即授予该项目内的角色权限。"
        />
        <Form
          form={form}
          layout="vertical"
          requiredMark
          initialValues={{ role: "viewer" }}
        >
          <Form.Item
            name="user_id"
            label="用户 ID（UUID）"
            rules={[
              { required: true, message: "请填写用户 ID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input placeholder="00000000-0000-0000-0000-000000000000" />
          </Form.Item>
          <Form.Item name="role" label="项目角色" rules={[{ required: true }]}>
            <Select
              options={PROJECT_ROLES.map((role) => ({
                value: role,
                label: PROJECT_ROLE_LABELS[role],
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
