import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Descriptions,
  Divider,
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
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/dataset/ActionButtons";
import CountsGrid from "../../components/dataset/CountsGrid";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import { trainingApi, type ExternalReferenceCreateInput } from "../../services/training";
import {
  presentDeliveryStatus,
  presentEvidenceRunKind,
  presentEvidenceStatus,
  presentExecutionMode,
  presentExternalStatus,
} from "../../services/datasetPresentation";
import { trainingRequestDetailPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  ExternalTrainingReference,
  TrainingDelivery,
  TrainingRunEvidence,
} from "../../types/dataset";

/** MOCK / DRY_RUN / UNKNOWN 都不是真实训练结果，界面必须显式提示。 */
const NON_REAL_EXECUTION_MODES = new Set(["MOCK", "DRY_RUN", "UNKNOWN"]);

function ExternalReferencesPanel({
  projectId,
  delivery,
  onChanged,
}: {
  projectId: string;
  delivery: TrainingDelivery;
  onChanged: () => void;
}) {
  const deliveryId = delivery.training_delivery_id;
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      trainingApi.listExternalReferences(projectId, deliveryId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, deliveryId],
  );
  const { items, loading, error, reload } = useCursorPage(
    load,
    (item) => item.external_ref_id,
  );
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<ExternalReferenceCreateInput>();
  const [messageApi, holder] = message.useMessage();
  const [submitting, setSubmitting] = useState(false);
  const canSubmit = delivery.status === "DELIVERED" || delivery.status === "ACCEPTED";

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await trainingApi.createExternalReference(projectId, deliveryId, {
        ...values,
        config: {},
      });
      messageApi.success("外部训练引用已提交");
      form.resetFields();
      setOpen(false);
      reload();
      onChanged();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ColumnsType<ExternalTrainingReference> = [
    { title: "Adapter", key: "adapter", width: 180, render: (_, item) => `${item.adapter_id} ${item.adapter_version}` },
    {
      title: "执行方式",
      key: "execution_mode",
      width: 190,
      // 执行方式与"外部 Run 状态"是两回事：SUCCEEDED 的 MOCK 运行仍然是模拟。
      // 因此这里单独成列并直接给出"非真实训练结果"警示，不能只藏在详情角落。
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <StatusTag value={item.execution_mode} presentation={presentExecutionMode} />
          {NON_REAL_EXECUTION_MODES.has(item.execution_mode) ? (
            <Typography.Text type="warning">非真实训练结果</Typography.Text>
          ) : null}
        </Space>
      ),
    },
    { title: "外部 Run", dataIndex: "external_run_id", ellipsis: true },
    {
      title: "训练产物",
      key: "artifacts",
      width: 240,
      // 产物地址由回调落库。放在这里是为了让"训练完成 → 登记模型版本"
      // 在界面上接得上：用户能直接复制 object_key，而不必去翻适配器。
      render: (_, item) => {
        const model = item.artifacts?.model as { object_key?: unknown } | undefined;
        const key = typeof model?.object_key === "string" ? model.object_key : null;
        return key ? (
          <Typography.Text copyable={{ text: key }} ellipsis style={{ maxWidth: 220 }}>
            {key}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        );
      },
    },
    {
      title: "状态",
      key: "status",
      width: 130,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <StatusTag value={item.external_status} presentation={presentExternalStatus} />
          {item.status_stale ? <Typography.Text type="warning">状态可能陈旧</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: "动作",
      key: "actions",
      width: 200,
      render: (_, item) => (
        <ActionButtons
          actions={item.allowed_actions}
          onAction={async (action) => {
            await trainingApi.actExternalReference(projectId, item.external_ref_id, action, {
              version: item.version,
            });
            reload();
            onChanged();
          }}
        />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <Space>
        <Button size="small" type="primary" disabled={!canSubmit} onClick={() => setOpen(true)}>
          提交外部训练
        </Button>
        <Button size="small" onClick={reload}>
          刷新
        </Button>
      </Space>
      {!canSubmit ? (
        <Typography.Text type="secondary">
          交付处于 {presentDeliveryStatus(delivery.status).label} 状态，暂不能提交外部训练。
        </Typography.Text>
      ) : null}
      <Table<ExternalTrainingReference>
        rowKey="external_ref_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无外部训练引用" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="提交外部训练引用"
        open={open}
        okText="提交"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="adapter_id" label="Adapter ID" initialValue="mock-training" rules={[{ required: true, max: 100 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="adapter_version" label="Adapter 版本" initialValue="1.0.0" rules={[{ required: true, max: 100 }]}>
            <Input />
          </Form.Item>
          <Form.Item name="correlation_id" label="关联 ID" rules={[{ required: true, max: 128 }]}>
            <Input placeholder="delivery-2026-001" />
          </Form.Item>
          <Form.Item
            name="execution_mode"
            label="执行方式"
            tooltip="留空时由 Adapter 部署配置决定；两者都没有时服务端拒绝提交。"
          >
            <Select
              allowClear
              placeholder="由 Adapter 配置决定；未配置时必须选择"
              options={[
                { value: "LOCAL", label: "LOCAL 本地真实执行" },
                { value: "MOCK", label: "MOCK 模拟训练" },
                { value: "DRY_RUN", label: "DRY_RUN 空跑（不训练）" },
                { value: "REMOTE", label: "REMOTE 远端真实训练" },
              ]}
            />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary">
          执行方式必须与 Adapter 实际运行环境一致；MOCK / DRY_RUN 的结果不得当作真实训练结果。
          留空且 Adapter 未配置执行方式时，服务端会拒绝提交而不是默认 REMOTE。
        </Typography.Text>
      </Modal>
    </Space>
  );
}

function extractModelSha256(evidence: TrainingRunEvidence): string | null {
  if (evidence.checkpoint_sha256) {
    return evidence.checkpoint_sha256;
  }
  const summarySha = evidence.metrics_summary?.model_safetensors_sha256;
  return typeof summarySha === "string" && summarySha.trim() ? summarySha : null;
}

export interface TrainingEvidenceTableProps {
  items: TrainingRunEvidence[];
  loading: boolean;
  error: ApiError | null;
  onReload: () => void;
}

export function TrainingEvidenceTable({
  items,
  loading,
  error,
  onReload,
}: TrainingEvidenceTableProps) {
  const columns: ColumnsType<TrainingRunEvidence> = [
    {
      title: "证据类型",
      key: "run_kind",
      width: 140,
      render: (_, item) => (
        <StatusTag value={item.run_kind} presentation={presentEvidenceRunKind} />
      ),
    },
    {
      title: "训练步数",
      key: "steps",
      width: 160,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{item.steps} steps</Typography.Text>
          <Typography.Text type="secondary">
            {item.device} · batch={item.batch_size} · seed={item.seed}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "model_safetensors_sha256",
      key: "model_safetensors_sha256",
      width: 280,
      render: (_, item) => {
        const sha = extractModelSha256(item);
        return sha ? (
          <Typography.Text copyable={{ text: sha }} ellipsis style={{ maxWidth: 260 }}>
            {sha}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        );
      },
    },
    {
      title: "状态",
      key: "status",
      width: 140,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <StatusTag value={item.status} presentation={presentEvidenceStatus} />
          <Typography.Text type="secondary">exit_code={item.exit_code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "落库时间",
      dataIndex: "created_at",
      width: 200,
      ellipsis: true,
    },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Space>
        <Typography.Text strong>已落库训练证据</Typography.Text>
        <Button size="small" onClick={onReload}>
          刷新证据
        </Button>
      </Space>
      <Table<TrainingRunEvidence>
        rowKey="evidence_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无已落库的训练过程证据" }}
      />
      {error ? <ApiErrorState error={error} onRetry={onReload} /> : null}
    </Space>
  );
}

function TrainingEvidencePanel({
  projectId,
  deliveryId,
}: {
  projectId: string;
  deliveryId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) => trainingApi.listDeliveryEvidence(projectId, deliveryId, { signal }),
    [projectId, deliveryId],
  );
  const { data, error, loading, reload } = useRequest(load);
  return (
    <TrainingEvidenceTable
      items={data?.items ?? []}
      loading={loading}
      error={error}
      onReload={reload}
    />
  );
}

function DeliveryDetailContent({
  projectId,
  deliveryId,
}: {
  projectId: string;
  deliveryId: string;
}) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => trainingApi.getDelivery(projectId, deliveryId, { signal }),
    [projectId, deliveryId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载训练交付" description="读取交付与外部引用。" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const delivery = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState compact variant="stale" description="刷新失败，展示上次成功数据。" onRetry={reload} />
        ) : null}
        {NON_REAL_EXECUTION_MODES.has(delivery.execution_mode) ? (
          <Alert
            showIcon
            type={delivery.execution_mode === "UNKNOWN" ? "error" : "warning"}
            message={
              delivery.execution_mode === "UNKNOWN"
                ? "执行方式未知：不得视为真实训练结果"
                : `${presentExecutionMode(delivery.execution_mode).label}：结果不得作为真实训练结果使用`
            }
            description={
              delivery.execution_mode === "UNKNOWN"
                ? "尚未提交外部训练时为正常状态。若下方已有训练引用，则该记录产生于执行方式落地之前（迁移前存量），无法回溯判定本地、模拟还是远端。"
                : "执行方式由训练提交时的 Adapter 配置或请求声明确定，逐条外部训练引用的执行方式见下方列表。"
            }
          />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "note", label: "交付单号", children: delivery.delivery_note_no },
            {
              key: "status",
              label: "状态",
              children: <StatusTag value={delivery.status} presentation={presentDeliveryStatus} />,
            },
            {
              key: "execution_mode",
              label: "执行方式",
              children: (
                <StatusTag
                  value={delivery.execution_mode}
                  presentation={presentExecutionMode}
                />
              ),
            },
            { key: "recipient", label: "接收人", children: delivery.recipient_id },
            { key: "team", label: "接收团队", children: delivery.recipient_team ?? "—" },
            { key: "version", label: "数据集版本", children: delivery.dataset_version_id },
            { key: "export", label: "导出任务", children: delivery.export_job_id },
            { key: "delivered", label: "交付时间", children: delivery.delivered_at ?? "—" },
            { key: "accepted", label: "验收时间", children: delivery.accepted_at ?? "—" },
          ]}
        />
        <CountsGrid
          items={[
            { label: "条目", value: delivery.item_count },
            { label: "训练", value: delivery.split_counts.train ?? 0 },
            { label: "验证", value: delivery.split_counts.validation ?? 0 },
            { label: "测试", value: delivery.split_counts.test ?? 0 },
          ]}
        />
        {delivery.rejection_reason ? (
          <Typography.Text type="danger">驳回原因：{delivery.rejection_reason}</Typography.Text>
        ) : null}
        <Space wrap>
          <Typography.Text strong>交付动作</Typography.Text>
          <ActionButtons
            actions={delivery.allowed_actions}
            onAction={async (action, payload) => {
              await trainingApi.actDelivery(projectId, deliveryId, action, {
                version: delivery.version,
                reason: payload.reason,
                acceptance_opinion: payload.acceptance_opinion,
                rework_ref: payload.rework_ref,
              });
              refreshAll();
            }}
          />
        </Space>
        <Divider />
        <ExternalReferencesPanel
          key={`${deliveryId}:${refreshToken}`}
          projectId={projectId}
          delivery={delivery}
          onChanged={refreshAll}
        />
        <Divider />
        <TrainingEvidencePanel
          key={`evidence:${deliveryId}:${refreshToken}`}
          projectId={projectId}
          deliveryId={deliveryId}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? `训练交付 ${data.delivery_note_no}` : "训练交付"}
      description={data ? data.training_delivery_id : "查看交付状态、验收与外部训练引用。"}
      extra={
        <Space wrap>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() =>
              data
                ? navigate(trainingRequestDetailPath(data.training_request_id))
                : navigate(-1)
            }
          >
            返回需求
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

export default function TrainingDeliveryDetailPage() {
  const projectId = useProjectId();
  const { deliveryId } = useParams<{ deliveryId: string }>();
  if (!projectId || !deliveryId) {
    return (
      <PageContainer title="训练交付" description="训练交付必须按项目隔离展示。">
        <ProjectScopeRequired scope="训练交付详情" />
      </PageContainer>
    );
  }
  return (
    <DeliveryDetailContent
      key={`${projectId}:${deliveryId}`}
      projectId={projectId}
      deliveryId={deliveryId}
    />
  );
}
