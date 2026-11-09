import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/modelops/ActionBar";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { modelOpsApi, type ModelVersionCreateInput } from "../../services/modelops";
import {
  presentAction,
  presentModelStatus,
  presentTaskType,
  presentVersionStatus,
} from "../../services/modelopsPresentation";
import { modelListPath } from "../../services/modelopsUrl";
import { formatDateTime, formatIdentifier } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { Model, ModelVersion } from "../../types/modelops";

const ARTIFACT_FORMATS = ["ONNX", "TORCHSCRIPT", "SAFETENSORS", "TENSORRT", "OTHER"];

function CreateVersionModal({
  projectId,
  modelId,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  modelId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<ModelVersionCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.createVersion(projectId, modelId, {
        ...values,
        dataset_version_ids: values.dataset_version_ids ?? [],
        metrics_summary: values.metrics_summary ?? {},
      });
      messageApi.success("模型版本已创建（DRAFT），请先在服务端校验产物再发布。");
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
        title="新建模型版本"
        open={open}
        width={640}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Typography.Title level={5}>产物</Typography.Title>
          <Form.Item
            name={["artifact", "object_key"]}
            label="对象存储键"
            rules={[{ required: true, max: 1024, message: "请输入产物对象键" }]}
          >
            <Input placeholder="models/act-pick/v1/model.onnx" />
          </Form.Item>
          <Form.Item
            name={["artifact", "sha256"]}
            label="SHA256"
            rules={[
              { required: true, message: "请输入 sha256:<64 位十六进制>" },
              {
                pattern: /^sha256:[0-9a-f]{64}$/,
                message: "格式必须为 sha256:<64 位小写十六进制>",
              },
            ]}
          >
            <Input placeholder="sha256:…" />
          </Form.Item>
          <Space style={{ display: "flex" }} align="start">
            <Form.Item
              name={["artifact", "size_bytes"]}
              label="大小（字节）"
              rules={[{ required: true, message: "请输入产物大小" }]}
            >
              <InputNumber min={1} style={{ width: 180 }} />
            </Form.Item>
            <Form.Item
              name={["artifact", "format"]}
              label="格式"
              initialValue="ONNX"
              rules={[{ required: true }]}
            >
              <Select
                style={{ width: 180 }}
                options={ARTIFACT_FORMATS.map((value) => ({ value, label: value }))}
              />
            </Form.Item>
          </Space>
          <Typography.Title level={5}>运行环境</Typography.Title>
          <Space style={{ display: "flex" }} align="start">
            <Form.Item
              name={["runtime", "framework"]}
              label="框架"
              initialValue="pytorch"
              rules={[{ required: true }]}
            >
              <Input style={{ width: 180 }} />
            </Form.Item>
            <Form.Item
              name={["runtime", "framework_version"]}
              label="框架版本"
              rules={[{ required: true, message: "请输入框架版本" }]}
            >
              <Input style={{ width: 180 }} placeholder="2.4.0" />
            </Form.Item>
          </Space>
          <Space style={{ display: "flex" }} align="start">
            <Form.Item name={["runtime", "python_version"]} label="Python 版本">
              <Input style={{ width: 180 }} placeholder="3.11" />
            </Form.Item>
            <Form.Item name={["runtime", "cuda_version"]} label="CUDA 版本">
              <Input style={{ width: 180 }} placeholder="12.1" />
            </Form.Item>
          </Space>
          <Form.Item name={["runtime", "image_ref"]} label="镜像引用">
            <Input placeholder="registry/ddp-train:1.0.0（可选）" />
          </Form.Item>
          <Form.Item name="release_notes" label="版本说明">
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function VersionsSection({
  projectId,
  modelId,
  refreshToken,
  onChanged,
}: {
  projectId: string;
  modelId: string;
  refreshToken: number;
  onChanged: () => void;
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const load = useCallback(
    (options: { cursor?: string; signal: AbortSignal }) =>
      modelOpsApi.listVersions(projectId, modelId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, modelId],
  );
  const { items, loading, refreshing, error, reload, hasMore, loadingMore, loadMore } =
    useCursorPage(load, (item) => item.model_version_id);

  // 父级动作或新建版本成功后，强制重新拉取第一页。
  useEffect(() => {
    if (refreshToken > 0) {
      reload();
    }
  }, [refreshToken, reload]);

  const act = async (version: ModelVersion, action: string) => {
    try {
      setBusy(version.model_version_id);
      await modelOpsApi.actVersion(projectId, modelId, version.model_version_id, action, {
        version: version.version,
      });
      messageApi.success("模型版本动作已提交");
      reload();
      onChanged();
    } catch (caught) {
      messageApi.error(caught instanceof Error ? caught.message : "动作执行失败");
    } finally {
      setBusy(null);
    }
  };

  const columns: ColumnsType<ModelVersion> = [
    {
      title: "版本",
      dataIndex: "version_no",
      width: 80,
      render: (value: number) => `v${value}`,
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentVersionStatus} />
      ),
    },
    {
      title: "产物",
      key: "artifact",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{item.artifact.format}</Typography.Text>
          <Typography.Text type="secondary" copyable={{ text: item.artifact.sha256 }}>
            {formatIdentifier(item.artifact.sha256, 16, 8)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "校验 / 发布时间",
      key: "time",
      width: 200,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary">
            校验：{formatDateTime(item.validated_at)}
          </Typography.Text>
          <Typography.Text type="secondary">
            发布：{formatDateTime(item.released_at)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "动作",
      key: "actions",
      width: 280,
      render: (_, item) => (
        <Space wrap>
          {item.allowed_actions.map((action) => (
            <Button
              key={action}
              size="small"
              loading={busy === item.model_version_id}
              onClick={() => void act(item, action)}
            >
              {presentAction(action)}
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
          版本
        </Typography.Title>
        <Space>
          <Button size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建版本
          </Button>
          <Button size="small" loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      </Space>
      {error ? <ApiErrorState error={error} onRetry={reload} compact /> : null}
      <Table<ModelVersion>
        rowKey="model_version_id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
        locale={{ emptyText: "暂无模型版本" }}
      />
      {hasMore ? (
        <Button block style={{ marginTop: 8 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      <CreateVersionModal
        projectId={projectId}
        modelId={modelId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          reload();
          onChanged();
        }}
      />
    </>
  );
}

function ModelDetailContent({ projectId, modelId }: { projectId: string; modelId: string }) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => modelOpsApi.getModel(projectId, modelId, { signal }),
    [projectId, modelId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, payload: { reason: string | null }) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await modelOpsApi.actModel(projectId, modelId, action, {
        version: data.version,
        reason: payload.reason,
      });
      reload();
      setRefreshToken((value) => value + 1);
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载模型" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const model: Model = data;
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
            { key: "code", label: "编码", children: model.code },
            {
              key: "status",
              label: "状态",
              children: <StatusTag value={model.status} presentation={presentModelStatus} />,
            },
            {
              key: "task_type",
              label: "任务类型",
              children: presentTaskType(model.task_type),
            },
            { key: "framework", label: "框架", children: model.framework },
            {
              key: "owner",
              label: "负责人",
              children: <Typography.Text copyable>{model.owner_id}</Typography.Text>,
            },
            {
              key: "current",
              label: "当前版本",
              children: model.current_version_id ? (
                <Typography.Text copyable>
                  {formatIdentifier(model.current_version_id)}
                </Typography.Text>
              ) : (
                "—"
              ),
            },
            {
              key: "tags",
              label: "标签",
              children:
                model.tags.length > 0
                  ? model.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
                  : "—",
            },
            { key: "updated", label: "更新时间", children: formatDateTime(model.updated_at) },
            {
              key: "description",
              label: "描述",
              span: 2,
              children: model.description ?? "—",
            },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            模型动作
          </Typography.Title>
          <ActionBar actions={model.allowed_actions} busy={busy} onAction={act} />
        </Space>
        <Divider style={{ margin: "8px 0" }} />
        <VersionsSection
          projectId={projectId}
          modelId={modelId}
          refreshToken={refreshToken}
          onChanged={reload}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="模型详情"
      description="版本产物以对象存储引用 + SHA256 标识；发布后不可原地修改。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(modelListPath())}>
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

export default function ModelDetailPage() {
  const projectId = useProjectId();
  const { modelId } = useParams<{ modelId: string }>();
  if (!projectId || !modelId) {
    return (
      <PageContainer title="模型详情" description="模型必须按项目隔离展示。">
        <ProjectScopeRequired scope="模型详情" />
      </PageContainer>
    );
  }
  return <ModelDetailContent key={`${projectId}:${modelId}`} projectId={projectId} modelId={modelId} />;
}
