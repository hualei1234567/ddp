import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/supplier/StatusTag";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { supplierApi, type BatchCreateInput } from "../../services/suppliers";
import { presentBatchStatus } from "../../services/supplierPresentation";
import { batchDetailPath } from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import { BATCH_STATUS_VALUES, type DeliveryBatch, type Supplier } from "../../types/supplier";

function CreateBatchModal({
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
  const [form] = Form.useForm<BatchCreateInput>();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    if (!open) {
      return;
    }
    const controller = new AbortController();
    supplierApi
      .listSuppliers(projectId, { status: "ACTIVE", signal: controller.signal })
      .then((page) => setSuppliers(page.items))
      .catch(() => setSuppliers([]));
    return () => controller.abort();
  }, [open, projectId]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await supplierApi.createBatch(projectId, values);
      messageApi.success("到货批次已登记（ANNOUNCED）");
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
        title="登记到货批次"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="supplier_id"
            label="供应商"
            rules={[{ required: true, message: "请选择已激活的供应商" }]}
          >
            <Select
              placeholder={suppliers.length === 0 ? "暂无已激活供应商" : "选择供应商"}
              options={suppliers.map((item) => ({
                value: item.supplier_id,
                label: `${item.name}（${item.code}）`,
              }))}
              notFoundContent="暂无已激活供应商，请先激活供应商档案"
            />
          </Form.Item>
          <Form.Item
            name="batch_code"
            label="批次编码"
            rules={[{ required: true, max: 64, message: "请输入批次编码" }]}
          >
            <Input placeholder="20261020_01" />
          </Form.Item>
          <Form.Item name="title" label="批次说明">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="planned_segment_count" label="计划片段数" initialValue={0}>
            <InputNumber min={0} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="planned_arrival_date" label="计划到货时间">
            <Input placeholder="2026-11-20T00:00:00Z" />
          </Form.Item>
          <Form.Item name="storage_prefix" label="对象存储目录">
            <Input placeholder="oss://supplier/20261020_01/" maxLength={1024} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function BatchListContent({
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
      supplierApi.listBatches(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.batch_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<DeliveryBatch> = [
    {
      title: "批次",
      key: "code",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={batchDetailPath(item.batch_id)}>{item.batch_code}</Link>
          <Typography.Text type="secondary">{item.title ?? "—"}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => <StatusTag value={value} presentation={presentBatchStatus} />,
    },
    {
      title: "计划 / 实到",
      key: "counts",
      width: 120,
      render: (_, item) => `${item.planned_segment_count} / ${item.received_segment_count}`,
    },
    {
      title: "计划到货",
      dataIndex: "planned_arrival_date",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "终审时间",
      dataIndex: "settled_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="到货验收"
      description="供应商到货批次与片段验收；终审结果由片段统计自动判定，不接受人工指定。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={BATCH_STATUS_VALUES.map((value) => ({
              value,
              label: presentBatchStatus(value).label,
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
            登记批次
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
      <Table<DeliveryBatch>
        rowKey="batch_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无到货批次" }}
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
      <CreateBatchModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function BatchListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="到货验收" description="到货批次必须按项目隔离展示。">
        <ProjectScopeRequired scope="到货批次" />
      </PageContainer>
    );
  }
  return (
    <BatchListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
