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
  Tabs,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/dataset/ActionButtons";
import RequirementSnapshotFields from "../../components/dataset/RequirementSnapshotFields";
import {
  snapshotFromValues,
  type SnapshotFormValues,
} from "../../services/requirementSnapshot";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { trainingApi } from "../../services/training";
import {
  presentDeliveryStatus,
  presentPriority,
  presentRevisionStatus,
  presentTrainingRequestStatus,
} from "../../services/datasetPresentation";
import {
  trainingDeliveryDetailPath,
  trainingRequestListPath,
} from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  TrainingDelivery,
  TrainingRequest,
  TrainingRequestRevision,
} from "../../types/dataset";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function RevisionsPanel({
  projectId,
  request,
  onChanged,
}: {
  projectId: string;
  request: TrainingRequest;
  onChanged: () => void;
}) {
  const requestId = request.training_request_id;
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      trainingApi.listRevisions(projectId, requestId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, requestId],
  );
  const { items, loading, error, reload } = useCursorPage(
    load,
    (item) => item.revision_id,
  );
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<SnapshotFormValues & { change_reason: string }>();
  const [messageApi, holder] = message.useMessage();
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await trainingApi.createRevision(projectId, requestId, {
        snapshot: snapshotFromValues(values),
        change_reason: values.change_reason,
      });
      messageApi.success("修订已创建（DRAFT）");
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

  const act = async (revision: TrainingRequestRevision, action: string, reason: string | null) => {
    await trainingApi.actRevision(projectId, requestId, revision.revision_id, action, {
      version: revision.version,
      reason,
    });
    reload();
    onChanged();
  };

  const columns: ColumnsType<TrainingRequestRevision> = [
    {
      title: "修订",
      key: "revision_no",
      width: 90,
      render: (_, item) => `r${item.revision_no}`,
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, item) => <StatusTag value={item.status} presentation={presentRevisionStatus} />,
    },
    { title: "变更原因", dataIndex: "change_reason", ellipsis: true },
    { title: "创建时间", dataIndex: "created_at", width: 190 },
    {
      title: "动作",
      key: "actions",
      width: 220,
      render: (_, item) => {
        return (
          <ActionButtons
            actions={item.allowed_actions}
            onAction={async (action, payload) =>
              act(item, action, payload.reason ?? null)
            }
          />
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <Space>
        <Button size="small" type="primary" onClick={() => setOpen(true)}>
          新建修订
        </Button>
        <Button size="small" onClick={reload}>
          刷新
        </Button>
      </Space>
      <Table<TrainingRequestRevision>
        rowKey="revision_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无修订" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建训练需求修订"
        open={open}
        width={760}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <RequirementSnapshotFields />
          <Form.Item
            name="change_reason"
            label="变更原因"
            rules={[{ required: true, max: 2000 }]}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function DeliveriesPanel({
  projectId,
  request,
  onChanged,
}: {
  projectId: string;
  request: TrainingRequest;
  onChanged: () => void;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      trainingApi.listDeliveries(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        training_request_id: request.training_request_id,
      }),
    [projectId, request.training_request_id],
  );
  const { items, loading, error, reload } = useCursorPage(
    load,
    (item) => item.training_delivery_id,
  );
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{
    training_request_revision_id: string;
    dataset_version_id: string;
    export_job_id: string;
    recipient_id: string;
    recipient_team?: string;
    delivery_note_no: string;
  }>();
  const [messageApi, holder] = message.useMessage();
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await trainingApi.createDelivery(projectId, {
        training_request_id: request.training_request_id,
        training_request_revision_id: values.training_request_revision_id,
        dataset_version_id: values.dataset_version_id,
        export_job_id: values.export_job_id,
        recipient_id: values.recipient_id,
        recipient_team: values.recipient_team ?? null,
        delivery_note_no: values.delivery_note_no,
      });
      messageApi.success("训练交付已创建（DRAFT）");
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

  const columns: ColumnsType<TrainingDelivery> = [
    {
      title: "交付单号",
      key: "note",
      render: (_, item) => (
        <Link to={trainingDeliveryDetailPath(item.training_delivery_id)}>
          {item.delivery_note_no}
        </Link>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, item) => <StatusTag value={item.status} presentation={presentDeliveryStatus} />,
    },
    { title: "条目", dataIndex: "item_count", width: 90 },
    { title: "接收团队", dataIndex: "recipient_team", width: 140 },
    { title: "创建时间", dataIndex: "created_at", width: 190 },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <Space>
        <Button
          size="small"
          type="primary"
          onClick={() => setOpen(true)}
          disabled={request.status === "DRAFT" || request.status === "CANCELLED"}
        >
          新建交付
        </Button>
        <Button size="small" onClick={reload}>
          刷新
        </Button>
      </Space>
      <Table<TrainingDelivery>
        rowKey="training_delivery_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无交付" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建训练交付"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="training_request_revision_id"
            label="需求修订 ID"
            rules={[
              { required: true, message: "请输入修订 UUID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="dataset_version_id"
            label="数据集版本 ID"
            rules={[{ required: true }, { pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="export_job_id"
            label="导出任务 ID"
            rules={[{ required: true }, { pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="recipient_id"
            label="接收人用户 ID"
            rules={[{ required: true }, { pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="recipient_team" label="接收团队">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="delivery_note_no" label="交付单号" rules={[{ required: true, max: 64 }]}>
            <Input placeholder="DN-2026-001" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function TrainingRequestDetailContent({
  projectId,
  requestId,
}: {
  projectId: string;
  requestId: string;
}) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => trainingApi.getRequest(projectId, requestId, { signal }),
    [projectId, requestId],
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
    body = <PageState variant="loading" title="正在加载训练需求" description="读取需求、修订与交付。" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const request: TrainingRequest = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState compact variant="stale" description="刷新失败，展示上次成功数据。" onRetry={reload} />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "code", label: "编码", children: request.code },
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={request.status} presentation={presentTrainingRequestStatus} />
              ),
            },
            {
              key: "priority",
              label: "优先级",
              children: <StatusTag value={request.priority} presentation={presentPriority} />,
            },
            { key: "owner", label: "负责人", children: request.owner_id },
            {
              key: "revision",
              label: "当前修订",
              children: request.current_revision_no ? `r${request.current_revision_no}` : "—",
            },
            { key: "blocked", label: "阻塞原因", children: request.blocked_reason ?? "—" },
          ]}
        />
        <Space wrap>
          <Typography.Text strong>需求动作</Typography.Text>
          <ActionButtons
            actions={request.allowed_actions}
            onAction={async (action, payload) => {
              await trainingApi.actRequest(projectId, requestId, action, {
                version: request.version,
                reason: payload.reason,
              });
              refreshAll();
            }}
          />
        </Space>
        <Divider />
        <Tabs
          items={[
            {
              key: "revisions",
              label: "修订",
              children: (
                <RevisionsPanel
                  key={`revisions:${refreshToken}`}
                  projectId={projectId}
                  request={request}
                  onChanged={refreshAll}
                />
              ),
            },
            {
              key: "deliveries",
              label: "交付",
              children: (
                <DeliveriesPanel
                  key={`deliveries:${refreshToken}`}
                  projectId={projectId}
                  request={request}
                  onChanged={refreshAll}
                />
              ),
            },
          ]}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? data.title : "训练需求"}
      description={data ? `${data.code} · ${data.training_request_id}` : "查看需求、修订与交付。"}
      extra={
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(trainingRequestListPath())}>
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

export default function TrainingRequestDetailPage() {
  const projectId = useProjectId();
  const { requestId } = useParams<{ requestId: string }>();
  if (!projectId || !requestId) {
    return (
      <PageContainer title="训练需求" description="训练需求必须按项目隔离展示。">
        <ProjectScopeRequired scope="训练需求详情" />
      </PageContainer>
    );
  }
  return (
    <TrainingRequestDetailContent
      key={`${projectId}:${requestId}`}
      projectId={projectId}
      requestId={requestId}
    />
  );
}
