import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
  Modal,
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
import { trainingApi, type ExternalReferenceCreateInput } from "../../services/training";
import {
  presentDeliveryStatus,
  presentExternalStatus,
} from "../../services/datasetPresentation";
import { trainingRequestDetailPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type { ExternalTrainingReference, TrainingDelivery } from "../../types/dataset";

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
    { title: "外部 Run", dataIndex: "external_run_id", ellipsis: true },
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
        </Form>
        <Typography.Text type="secondary">
          真实训练平台未接入时使用 [MOCK] Adapter；DDP 只保存外部引用与状态摘要。
        </Typography.Text>
      </Modal>
    </Space>
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
