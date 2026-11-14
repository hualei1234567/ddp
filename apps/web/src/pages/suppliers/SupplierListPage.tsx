import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/supplier/StatusTag";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { supplierApi, type SupplierCreateInput } from "../../services/suppliers";
import {
  presentCategory,
  presentSupplierStatus,
} from "../../services/supplierPresentation";
import { supplierDetailPath } from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  SUPPLIER_CATEGORY_VALUES,
  SUPPLIER_STATUS_VALUES,
  type Supplier,
} from "../../types/supplier";

function CreateSupplierModal({
  projectId,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<SupplierCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await supplierApi.createSupplier(projectId, values);
      messageApi.success("供应商已登记（DRAFT），需激活后才能承接批次");
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
        title="登记供应商"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="供应商编码"
            rules={[{ required: true, max: 64, message: "请输入 1-64 字符编码" }]}
          >
            <Input placeholder="SUP-001" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, max: 200, message: "请输入名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="category" label="类型" initialValue="DATA_COLLECTION">
            <Select
              options={SUPPLIER_CATEGORY_VALUES.map((value) => ({
                value,
                label: presentCategory(value),
              }))}
            />
          </Form.Item>
          <Form.Item name="contact_name" label="联系人">
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="contact_email" label="联系邮箱">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="external_ref" label="外部系统标识">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function SupplierListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      supplierApi.listSuppliers(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.supplier_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<Supplier> = [
    {
      title: "名称 / 编码",
      key: "name",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={supplierDetailPath(item.supplier_id)}>{item.name}</Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentSupplierStatus} />
      ),
    },
    {
      title: "类型",
      dataIndex: "category",
      width: 120,
      render: (value: string) => presentCategory(value),
    },
    { title: "联系人", dataIndex: "contact_name", width: 120, render: (v: string | null) => v ?? "—" },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 190,
      render: (value: string) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="供应商管理"
      description="供应商档案、合作状态与到货验收入口；状态与动作以服务端 allowed_actions 为准。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={SUPPLIER_STATUS_VALUES.map((value) => ({
              value,
              label: presentSupplierStatus(value).label,
            }))}
            onChange={(value) => {
              const params = new URLSearchParams(searchParams);
              if (value) {
                params.set("status", value);
              } else {
                params.delete("status");
              }
              setSearchParams(params, { replace: true });
            }}
          />
        </Space>
      }
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            登记供应商
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<Supplier>
        rowKey="supplier_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无供应商" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {hasMore ? (
        <Button block style={{ marginTop: 12 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <Typography.Text type="danger">加载更多失败：{loadMoreError.message}</Typography.Text>
      ) : null}
      <CreateSupplierModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function SupplierListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="供应商管理" description="供应商必须按项目隔离展示。">
        <ProjectScopeRequired scope="供应商列表" />
      </PageContainer>
    );
  }
  return (
    <SupplierListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
