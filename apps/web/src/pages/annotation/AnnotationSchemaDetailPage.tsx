import { ArrowLeftOutlined, EditOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/annotation/ActionButtons";
import type { AnnotationActionPayload } from "../../components/annotation/ActionButtons";
import AnnotationFeedbackAlert from "../../components/annotation/AnnotationFeedbackAlert";
import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import SchemaDefinitionDrawer from "../../components/annotation/SchemaDefinitionDrawer";
import type { SchemaDefinitionSubmitPayload } from "../../components/annotation/SchemaDefinitionDrawer";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { resolveAnnotationActions } from "../../services/annotationActions";
import {
  annotationSuccess,
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../../services/annotationPresentation";
import type { AnnotationSchemaPatchInput } from "../../services/annotationSchemaApi";
import { buildAnnotationSchemaListPath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { LabelDefinition } from "../../types/annotation";

function labelColumns(): TableColumnsType<LabelDefinition> {
  return [
    { title: "标签键", dataIndex: "label_key", width: 160 },
    { title: "类型", dataIndex: "label_type", width: 140 },
    {
      title: "必填",
      dataIndex: "required",
      width: 80,
      render: (value: boolean) => (value ? <Tag color="red">必填</Tag> : "否"),
    },
    { title: "层级", dataIndex: "level", width: 130 },
    {
      title: "可见性",
      dataIndex: "visibility",
      width: 140,
      render: (value: string) => (
        <Tag color={value === "ANNOTATOR" ? "green" : "orange"}>{value}</Tag>
      ),
    },
    {
      title: "值定义",
      dataIndex: "value_spec",
      render: (value: Record<string, unknown>) => (
        <Typography.Text code>{JSON.stringify(value)}</Typography.Text>
      ),
    },
    {
      title: "质量规则",
      dataIndex: "quality_rules",
      width: 200,
      render: (value: string[]) => value.join("；") || "—",
    },
  ];
}

function SchemaDetailContent({
  projectId,
  schemaId,
}: {
  projectId: string;
  schemaId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getSchema(projectId, schemaId, { signal }),
    [projectId, schemaId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  const handleAction = async (
    action: string,
    payload: AnnotationActionPayload,
  ) => {
    if (data === null) {
      throw new Error("Schema 尚未加载完成，请刷新后重试。");
    }
    setBusyAction(action);
    setFeedback(null);
    try {
      await annotationApi.actOnSchema(projectId, schemaId, action, {
        version: data.version,
        reason: payload.reason,
      });
      setFeedback(annotationSuccess(`Schema 动作 ${action} 已提交。`));
      reload();
    } catch (caught) {
      setFeedback(describeAnnotationActionError(caught));
      throw caught;
    } finally {
      setBusyAction(null);
    }
  };

  const handleEdit = async ({ input }: SchemaDefinitionSubmitPayload) => {
    if (data === null) {
      throw new Error("Schema 尚未加载完成，请刷新后重试。");
    }
    await annotationApi.updateSchema(
      projectId,
      schemaId,
      input as AnnotationSchemaPatchInput,
      { version: data.version },
    );
    setEditOpen(false);
    setFeedback(annotationSuccess("Schema 定义已保存。"));
    reload();
  };

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate(buildAnnotationSchemaListPath(projectId))}
      >
        返回列表
      </Button>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载 Schema 详情" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的 Schema。"
            onRetry={reload}
          />
        ) : null}
        <AnnotationFeedbackAlert feedback={feedback} />
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>状态动作</Typography.Text>
          <ActionButtons
            kind="schema"
            actions={resolveAnnotationActions("schema", data.status, data.allowed_actions).actions}
            busyAction={busyAction}
            showFallbackNote
            onAction={handleAction}
          />
          {data.status === "DRAFT" ? (
            <Button icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
              编辑定义
            </Button>
          ) : null}
        </Space>
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="Schema ID">
            <IdentifierValue value={data.schema_id} label="Schema ID" />
          </Descriptions.Item>
          <Descriptions.Item label="编码">{data.code}</Descriptions.Item>
          <Descriptions.Item label="名称">{data.name}</Descriptions.Item>
          <Descriptions.Item label="版本">v{data.schema_version}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <AnnotationStatusTag value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="兼容性">{data.compatibility}</Descriptions.Item>
          <Descriptions.Item label="层级">
            {data.annotation_levels.join(" / ")}
          </Descriptions.Item>
          <Descriptions.Item label="Schema Hash">
            <Typography.Text code>{data.schema_sha256}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="发布时间">
            {formatDateTime(data.published_at)}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {formatDateTime(data.updated_at)}
          </Descriptions.Item>
          <Descriptions.Item label="替代版本">
            <IdentifierValue value={data.supersedes_id} label="旧 Schema ID" />
          </Descriptions.Item>
        </Descriptions>
        {data.description ? (
          <Typography.Paragraph type="secondary">{data.description}</Typography.Paragraph>
        ) : null}
        <Typography.Text type="secondary">
          {data.status === "DRAFT"
            ? "DRAFT 可编辑定义；提交审核后只读，发布与废弃由状态动作推进。"
            : data.status === "PUBLISHED"
              ? "Schema 已发布，标签定义只读；变更必须创建新版本。"
              : "当前状态不可编辑；可用动作以服务端裁决为准。"}
        </Typography.Text>
        <Table<LabelDefinition>
          rowKey="label_key"
          size="small"
          columns={labelColumns()}
          dataSource={data.labels}
          pagination={false}
          scroll={{ x: 1100 }}
        />
        <Typography.Text type="secondary">
          GOLD_ONLY / SYSTEM_ONLY 标签属于受限定义；页面只读展示，不包含黄金集期望答案。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? `Schema · ${data.code} v${data.schema_version}` : "标注 Schema 详情"}
      description="查看版本化标签定义与快照 Hash，并推进 Schema 状态动作。"
      extra={extra}
    >
      {body}
      <SchemaDefinitionDrawer
        open={editOpen}
        mode="edit"
        schema={data}
        onClose={() => setEditOpen(false)}
        onSubmit={handleEdit}
      />
    </PageContainer>
  );
}

export default function AnnotationSchemaDetailPage() {
  const projectId = useProjectId();
  const { schemaId } = useParams<{ schemaId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="标注 Schema 详情" description="Schema 必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注 Schema 详情" />
      </PageContainer>
    );
  }
  if (!schemaId) {
    return (
      <PageContainer title="标注 Schema 详情">
        <ProjectScopeRequired scope="标注 Schema 详情" />
      </PageContainer>
    );
  }
  return (
    <SchemaDetailContent
      key={`${projectId}:${schemaId}`}
      projectId={projectId}
      schemaId={schemaId}
    />
  );
}
