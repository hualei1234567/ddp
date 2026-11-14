import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Divider, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/supplier/ActionBar";
import StatusTag from "../../components/supplier/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { supplierApi } from "../../services/suppliers";
import {
  metricsSummary,
  presentBatchStatus,
  presentCategory,
  presentSupplierStatus,
} from "../../services/supplierPresentation";
import {
  batchDetailPath,
  supplierListPath,
} from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DeliveryBatch, Supplier } from "../../types/supplier";

function BatchesSection({
  projectId,
  supplierId,
}: {
  projectId: string;
  supplierId: string;
}) {
  const load = useCallback(
    (options: { cursor?: string; signal: AbortSignal }) =>
      supplierApi.listBatches(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        supplier_id: supplierId,
      }),
    [projectId, supplierId],
  );
  const { items, loading, error, reload, hasMore, loadingMore, loadMore } = useCursorPage(
    load,
    (item) => item.batch_id,
  );

  const columns: ColumnsType<DeliveryBatch> = [
    {
      title: "批次",
      key: "code",
      render: (_, item) => (
        <Link to={batchDetailPath(item.batch_id)}>{item.batch_code}</Link>
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
      width: 130,
      render: (_, item) => `${item.planned_segment_count} / ${item.received_segment_count}`,
    },
    { title: "返工次数", dataIndex: "rework_count", width: 100 },
    {
      title: "到货 / 终审",
      key: "times",
      width: 200,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">到货：{formatDateTime(item.arrived_at)}</Typography.Text>
          <Typography.Text type="secondary">终审：{formatDateTime(item.settled_at)}</Typography.Text>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          到货批次
        </Typography.Title>
        <Button size="small" loading={loading} onClick={reload}>
          刷新
        </Button>
      </Space>
      {error ? <ApiErrorState error={error} onRetry={reload} compact /> : null}
      <Table<DeliveryBatch>
        rowKey="batch_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无到货批次" }}
      />
      {hasMore ? (
        <Button block style={{ marginTop: 8 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
    </>
  );
}

function SupplierDetailContent({
  projectId,
  supplierId,
}: {
  projectId: string;
  supplierId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => supplierApi.getSupplier(projectId, supplierId, { signal }),
    [projectId, supplierId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const metricsLoad = useCallback(
    (signal: AbortSignal) => supplierApi.getMetrics(projectId, supplierId, { signal }),
    [projectId, supplierId],
  );
  const metrics = useRequest(metricsLoad);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, reason: string | null) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await supplierApi.actSupplier(projectId, supplierId, action, {
        version: data.version,
        reason,
      });
      reload();
      metrics.reload();
      setRefreshToken((value) => value + 1);
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载供应商" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const supplier: Supplier = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "code", label: "编码", children: supplier.code },
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={supplier.status} presentation={presentSupplierStatus} />
              ),
            },
            { key: "category", label: "类型", children: presentCategory(supplier.category) },
            { key: "contact", label: "联系人", children: supplier.contact_name ?? "—" },
            { key: "email", label: "邮箱", children: supplier.contact_email ?? "—" },
            { key: "external", label: "外部标识", children: supplier.external_ref ?? "—" },
            {
              key: "updated",
              label: "更新时间",
              children: formatDateTime(supplier.updated_at),
            },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          备注：{supplier.notes ?? "—"}
        </Typography.Paragraph>
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          供应商绩效
        </Typography.Title>
        <Typography.Text type="secondary">
          缺失数据统一显示「暂无」；0 与 N/A 严格区分，不把未知计入分母。
        </Typography.Text>
        {metrics.error ? (
          <ApiErrorState error={metrics.error} onRetry={metrics.reload} compact />
        ) : null}
        {metrics.data ? (
          <Descriptions
            size="small"
            column={4}
            items={[
              ...metricsSummary(metrics.data).map((item) => ({
                key: item.label,
                label: item.label,
                children: item.value,
              })),
              {
                key: "decided_segments",
                label: "已裁决片段",
                children: String(metrics.data.decided_segments),
              },
            ]}
          />
        ) : null}
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            供应商动作
          </Typography.Title>
          <ActionBar actions={supplier.allowed_actions} busy={busy} onAction={act} />
          <Typography.Text type="secondary">
            终止合作不可回退，且要求项目负责人权限。
          </Typography.Text>
        </Space>
        <Divider style={{ margin: "8px 0" }} />
        <BatchesSection
          key={`${supplierId}:${refreshToken}`}
          projectId={projectId}
          supplierId={supplierId}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="供应商详情"
      description="供应商档案、绩效与到货批次；绩效只统计已终审批次。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(supplierListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function SupplierDetailPage() {
  const projectId = useProjectId();
  const { supplierId } = useParams<{ supplierId: string }>();
  if (!projectId || !supplierId) {
    return (
      <PageContainer title="供应商详情" description="供应商必须按项目隔离展示。">
        <ProjectScopeRequired scope="供应商详情" />
      </PageContainer>
    );
  }
  return (
    <SupplierDetailContent
      key={`${projectId}:${supplierId}`}
      projectId={projectId}
      supplierId={supplierId}
    />
  );
}
