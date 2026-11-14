import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Typography,
  message,
} from "antd";
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
import { supplierApi, type SegmentCreateInput } from "../../services/suppliers";
import {
  presentBatchStatus,
  presentDecision,
  presentSegmentStatus,
} from "../../services/supplierPresentation";
import { batchListPath, segmentPath, supplierDetailPath } from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DeliveryBatch, InboundSegment } from "../../types/supplier";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function AddSegmentModal({
  projectId,
  batchId,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  batchId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<SegmentCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await supplierApi.addSegment(projectId, batchId, {
        segment_code: values.segment_code,
        raw_asset_id: values.raw_asset_id || null,
        duration_ns: values.duration_ns ?? null,
        task_name: values.task_name ?? null,
        diagnostics: {},
      });
      messageApi.success("片段已登记（REGISTERED）");
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
        title="登记到货片段"
        open={open}
        okText="登记"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="segment_code"
            label="片段编码"
            rules={[{ required: true, max: 64, message: "请输入片段编码" }]}
          >
            <Input placeholder="seg-0001" />
          </Form.Item>
          <Form.Item
            name="raw_asset_id"
            label="RawAsset ID（可选，终审前必须补齐）"
            rules={[
              {
                pattern: UUID_PATTERN,
                message: "必须是标准 UUID",
              },
            ]}
          >
            <Input placeholder="关联既有数据接入链路的 RawAsset" />
          </Form.Item>
          <Form.Item name="duration_ns" label="时长（纳秒）">
            <InputNumber min={0} style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="task_name" label="任务名称">
            <Input maxLength={200} placeholder="采集青椒" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function SegmentsSection({
  projectId,
  batch,
  onChanged,
}: {
  projectId: string;
  batch: DeliveryBatch;
  onChanged: () => void;
}) {
  const [addOpen, setAddOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const load = useCallback(
    (options: { cursor?: string; signal: AbortSignal }) =>
      supplierApi.listSegments(projectId, batch.batch_id, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, batch.batch_id],
  );
  const { items, loading, error, reload, hasMore, loadingMore, loadMore } = useCursorPage(
    load,
    (item) => item.segment_id,
  );

  const act = async (segment: InboundSegment, action: string) => {
    try {
      setBusy(segment.segment_id);
      await supplierApi.actSegment(projectId, batch.batch_id, segment.segment_id, action, {
        version: segment.version,
      });
      messageApi.success("片段状态已更新");
      reload();
      onChanged();
    } catch (caught) {
      messageApi.error(caught instanceof Error ? caught.message : "动作执行失败");
    } finally {
      setBusy(null);
    }
  };

  const columns: ColumnsType<InboundSegment> = [
    {
      title: "片段",
      key: "code",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={segmentPath(batch.batch_id, item.segment_id)}>{item.segment_code}</Link>
          <Typography.Text type="secondary">{item.task_name ?? "—"}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => <StatusTag value={value} presentation={presentSegmentStatus} />,
    },
    {
      title: "裁决",
      dataIndex: "decision",
      width: 100,
      render: (value: string | null) =>
        value ? <StatusTag value={value} presentation={presentDecision} /> : "—",
    },
    {
      title: "RawAsset",
      dataIndex: "raw_asset_id",
      width: 200,
      ellipsis: true,
      render: (value: string | null) => value ?? "未关联",
    },
    {
      title: "动作",
      key: "actions",
      width: 240,
      render: (_, item) => (
        <Space wrap>
          {item.allowed_actions.map((action) => (
            <Button
              key={action}
              size="small"
              loading={busy === item.segment_id}
              onClick={() => void act(item, action)}
            >
              {action === "queue-review" ? "进入审看" : "补件重提"}
            </Button>
          ))}
          {item.allowed_actions.length === 0 ? (
            <Typography.Text type="secondary">无可用动作</Typography.Text>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <>
      {contextHolder}
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          到货片段
        </Typography.Title>
        <Space>
          <Button
            size="small"
            icon={<PlusOutlined />}
            disabled={batch.status !== "SCREENING" && batch.status !== "ARRIVED"}
            onClick={() => setAddOpen(true)}
          >
            登记片段
          </Button>
          <Button size="small" loading={loading} onClick={reload}>
            刷新
          </Button>
        </Space>
      </Space>
      {error ? <ApiErrorState error={error} onRetry={reload} compact /> : null}
      <Table<InboundSegment>
        rowKey="segment_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无片段" }}
      />
      {hasMore ? (
        <Button block style={{ marginTop: 8 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      <AddSegmentModal
        projectId={projectId}
        batchId={batch.batch_id}
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={() => {
          reload();
          onChanged();
        }}
      />
    </>
  );
}

function BatchDetailContent({
  projectId,
  batchId,
}: {
  projectId: string;
  batchId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => supplierApi.getBatch(projectId, batchId, { signal }),
    [projectId, batchId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  const act = async (action: string, reason: string | null) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await supplierApi.actBatch(projectId, batchId, action, {
        version: data.version,
        reason,
      });
      refreshAll();
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载到货批次" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const batch: DeliveryBatch = data;
    const canFinalize = batch.status === "SCREENING";
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
            { key: "code", label: "批次编码", children: batch.batch_code },
            {
              key: "status",
              label: "状态",
              children: <StatusTag value={batch.status} presentation={presentBatchStatus} />,
            },
            {
              key: "supplier",
              label: "供应商",
              children: (
                <Link to={supplierDetailPath(batch.supplier_id)}>
                  {batch.supplier_id}
                </Link>
              ),
            },
            {
              key: "counts",
              label: "计划 / 实到片段",
              children: `${batch.planned_segment_count} / ${batch.received_segment_count}`,
            },
            {
              key: "planned",
              label: "计划到货",
              children: formatDateTime(batch.planned_arrival_date),
            },
            { key: "arrived", label: "实际到货", children: formatDateTime(batch.arrived_at) },
            { key: "settled", label: "终审时间", children: formatDateTime(batch.settled_at) },
            { key: "rework", label: "返工次数", children: batch.rework_count },
            { key: "storage", label: "存储目录", span: 2, children: batch.storage_prefix ?? "—" },
            { key: "notes", label: "备注", span: 2, children: batch.notes ?? "—" },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        {canFinalize ? (
          <Space direction="vertical" size={4}>
            <Button
              type="primary"
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await supplierApi.finalizeBatch(projectId, batchId, {
                    version: batch.version,
                  });
                  refreshAll();
                } catch (caught) {
                  // 门禁失败原因由服务端返回，原样展示。
                  const text = caught instanceof Error ? caught.message : "终审失败";
                  Modal.error({ title: "终审未通过", content: text });
                } finally {
                  setBusy(false);
                }
              }}
            >
              提交终审
            </Button>
            <Typography.Text type="secondary">
              终审结果由片段统计决定；存在未裁决或待补件片段、或已通过片段未关联
              RawAsset 时会被拒绝。
            </Typography.Text>
          </Space>
        ) : null}
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            批次动作
          </Typography.Title>
          <ActionBar
            actions={batch.allowed_actions}
            busy={busy}
            skipActions={["finalize"]}
            onAction={act}
          />
        </Space>
        <Divider style={{ margin: "8px 0" }} />
        <SegmentsSection
          key={`${batch.batch_id}:${refreshToken}`}
          projectId={projectId}
          batch={batch}
          onChanged={refreshAll}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="到货批次详情"
      description="逐条审看片段并裁决；每次裁决都会写入只追加的验收核验账本。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(batchListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refreshAll}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function BatchDetailPage() {
  const projectId = useProjectId();
  const { batchId } = useParams<{ batchId: string }>();
  if (!projectId || !batchId) {
    return (
      <PageContainer title="到货批次详情" description="到货批次必须按项目隔离展示。">
        <ProjectScopeRequired scope="到货批次详情" />
      </PageContainer>
    );
  }
  return (
    <BatchDetailContent key={`${projectId}:${batchId}`} projectId={projectId} batchId={batchId} />
  );
}
