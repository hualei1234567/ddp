import { PlusOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal, Select, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";

import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { datasetApi, type GrantCreateInput } from "../../services/datasets";
import { presentGrantStatus } from "../../services/datasetPresentation";
import { GRANT_SCOPE_VALUES, type DatasetGrant } from "../../types/dataset";
import ActionButtons from "./ActionButtons";
import ApiErrorState from "../ui/ApiErrorState";
import StatusTag from "./StatusTag";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function DatasetGrantsSection({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listGrants(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload, stale } = useCursorPage(load, (item) => item.grant_id);
  const [form] = Form.useForm<GrantCreateInput & { expires_hours: number }>();
  const [open, setOpen] = useState(false);
  const [messageApi, holder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const expiresAt = new Date(
        Date.now() + values.expires_hours * 3600 * 1000,
      ).toISOString();
      await datasetApi.createGrant(projectId, datasetId, {
        grantee_project_id: values.grantee_project_id,
        grantee_team: values.grantee_team ?? null,
        scope: values.scope,
        reason: values.reason,
        expires_at: expiresAt,
        dataset_version_id: values.dataset_version_id || null,
      });
      messageApi.success("授权申请已创建（PENDING_APPROVAL）");
      form.resetFields();
      setOpen(false);
      reload();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  const columns: ColumnsType<DatasetGrant> = [
    {
      title: "被授权项目 / 团队",
      key: "grantee",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{item.grantee_team ?? "—"}</Typography.Text>
          <Typography.Text type="secondary">{item.grantee_project_id}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "范围",
      dataIndex: "scope",
      width: 160,
      render: (value: string[]) => value.join(" / "),
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentGrantStatus} />
      ),
    },
    { title: "到期", dataIndex: "expires_at", width: 190 },
    {
      title: "原因",
      dataIndex: "reason",
      ellipsis: true,
    },
    {
      title: "动作",
      key: "actions",
      width: 220,
      render: (_, item) => (
        <ActionButtons
          actions={item.allowed_actions}
          onAction={async (action, payload) => {
            await datasetApi.actGrant(projectId, datasetId, item.grant_id, action, {
              version: item.version,
              reason: payload.reason,
            });
            reload();
          }}
        />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <Typography.Text type="secondary">
        默认禁止跨项目访问；授权需 PO 审批，到期或撤销后立即失效。
      </Typography.Text>
      <Space>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建授权
        </Button>
        <Button size="small" onClick={reload}>
          刷新
        </Button>
      </Space>
      {stale ? <Typography.Text type="warning">刷新失败，展示上次成功数据。</Typography.Text> : null}
      <Table<DatasetGrant>
        rowKey="grant_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无授权" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建跨项目授权"
        open={open}
        okText="提交申请"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="grantee_project_id"
            label="被授权项目 ID"
            rules={[
              { required: true, message: "请输入项目 UUID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="grantee_team" label="被授权团队（可选）">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="scope"
            label="授权范围"
            initialValue={["VIEW"]}
            rules={[{ required: true, message: "至少选择一个范围" }]}
          >
            <Select mode="multiple" options={GRANT_SCOPE_VALUES.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item name="dataset_version_id" label="限定版本 ID（可选）">
            <Input placeholder="留空表示数据集级授权" />
          </Form.Item>
          <Form.Item
            name="expires_hours"
            label="有效小时数"
            initialValue={24}
            rules={[{ required: true }]}
          >
            <Input type="number" min={1} />
          </Form.Item>
          <Form.Item
            name="reason"
            label="申请原因"
            rules={[{ required: true, max: 2000, message: "请填写原因" }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
