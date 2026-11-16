import { Alert, Form, Input, Modal, Select, message } from "antd";
import { useEffect, useState } from "react";

import { projectManagementApi } from "../../services/projectManagement";
import { PROJECT_STATUS_LABELS } from "../../services/projectPresentation";
import {
  PROJECT_STATUSES,
  type Project,
  type ProjectStatus,
} from "../../types/project";

interface ProjectFormValues {
  code: string;
  name: string;
  description?: string;
  status?: ProjectStatus;
}

interface ProjectFormModalProps {
  open: boolean;
  /** `null` 表示创建；否则编辑该项目。 */
  project: Project | null;
  onClose: () => void;
  /** 创建或修改成功后触发（父组件负责刷新列表）。 */
  onSubmitted: () => void;
}

/**
 * 创建 / 编辑项目。
 *
 * 创建权限由服务端判定（仅平台管理员）；前端没有"当前主体角色"的查询入口，
 * 因此按钮不预设显隐，失败时如实展示服务端错误，而不是猜权限。
 */
export default function ProjectFormModal({
  open,
  project,
  onClose,
  onSubmitted,
}: ProjectFormModalProps) {
  const [form] = Form.useForm<ProjectFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const editing = project !== null;

  useEffect(() => {
    if (!open) {
      return;
    }
    if (project) {
      form.setFieldsValue({
        code: project.code,
        name: project.name,
        description: project.description ?? undefined,
        status: project.status,
      });
    } else {
      form.resetFields();
    }
  }, [form, open, project]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      if (project) {
        await projectManagementApi.patchProject(
          project.project_id,
          {
            name: values.name.trim(),
            description: values.description?.trim()
              ? values.description.trim()
              : null,
            status: values.status,
          },
          { version: project.version },
        );
        messageApi.success("项目已更新");
      } else {
        await projectManagementApi.createProject({
          code: values.code.trim(),
          name: values.name.trim(),
          description: values.description?.trim()
            ? values.description.trim()
            : null,
        });
        messageApi.success("项目已创建，创建者自动成为负责人");
      }
      onSubmitted();
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
        title={editing ? `编辑项目：${project?.name ?? ""}` : "创建项目"}
        open={open}
        okText={editing ? "保存" : "创建"}
        cancelText="取消"
        width={560}
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="项目编码创建后不可修改"
          description="项目是平台数据隔离的边界：所有业务数据都归属且只归属一个项目。归档只把项目移出默认列表，不会删除任何数据。"
        />
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="项目编码"
            rules={[
              { required: true, max: 64 },
              { whitespace: true, message: "编码不能为空白" },
            ]}
            extra={
              editing
                ? "编码是稳定业务键，不支持修改。"
                : "全局唯一，建议使用短横线命名。"
            }
          >
            <Input placeholder="prj-kitchen-2026" disabled={editing} />
          </Form.Item>
          <Form.Item
            name="name"
            label="项目名称"
            rules={[
              { required: true, max: 200 },
              { whitespace: true, message: "名称不能为空白" },
            ]}
          >
            <Input placeholder="厨房场景数据采集" />
          </Form.Item>
          <Form.Item
            name="description"
            label="项目说明"
            rules={[{ max: 1000 }]}
          >
            <Input.TextArea rows={3} maxLength={1000} showCount />
          </Form.Item>
          {editing ? (
            <Form.Item
              name="status"
              label="项目状态"
              rules={[{ required: true }]}
            >
              <Select
                options={PROJECT_STATUSES.map((status) => ({
                  value: status,
                  label: PROJECT_STATUS_LABELS[status],
                }))}
              />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
    </>
  );
}
