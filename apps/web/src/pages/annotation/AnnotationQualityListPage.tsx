import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import TaskScopedPrompt from "../../components/annotation/TaskScopedPrompt";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import {
  buildAnnotationQualityDetailPath,
  buildAnnotationQualityListPath,
  buildAnnotationSubmissionPath,
  buildAnnotationTaskDetailPath,
  readTaskIdQuery,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { QualitySample } from "../../types/annotation";

function columns(projectId: string): TableColumnsType<QualitySample> {
  return [
    {
      title: "抽检",
      key: "sample",
      width: 230,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <Link to={buildAnnotationQualityDetailPath(projectId, item.quality_sample_id)}>
            <IdentifierValue value={item.quality_sample_id} label="抽检 ID" />
          </Link>
          <Typography.Text type="secondary">{item.sample_type}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "结论",
      dataIndex: "outcome",
      width: 110,
      render: (value: string | null) => value ?? "Pending",
    },
    {
      title: "提交",
      dataIndex: "submission_id",
      width: 210,
      render: (value: string) => (
        <Link to={buildAnnotationSubmissionPath(projectId, value)}>
          <IdentifierValue value={value} label="提交 ID" />
        </Link>
      ),
    },
    {
      title: "标注人 / 复核人",
      key: "people",
      width: 220,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <IdentifierValue value={item.annotator_id} label="标注人 ID" />
          <Typography.Text type="secondary">
            复核：{item.reviewer_id ?? "未指派"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "策略 / 理由",
      key: "policy",
      width: 260,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <Tag>{item.sampling_policy_version}</Tag>
          <Typography.Text type="secondary">{item.sample_reason}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "截止时间",
      dataIndex: "due_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
  ];
}

function QualityListContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.listQualitySamples(projectId, taskId, { signal }),
    [projectId, taskId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const extra = (
    <Space wrap>
      <Link to={buildAnnotationTaskDetailPath(projectId, taskId)}>
        <Button icon={<ArrowLeftOutlined />}>返回任务</Button>
      </Link>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载质检抽检" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (data.items.length === 0) {
    body = (
      <PageState
        variant="empty"
        title="暂无质检抽检"
        description="该任务尚未产生抽检样本；缺失结论不会默认通过。"
      />
    );
  } else {
    body = (
      <Space direction="vertical" size={12} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的抽检列表。"
            onRetry={reload}
          />
        ) : null}
        <Table<QualitySample>
          rowKey="quality_sample_id"
          columns={columns(projectId)}
          dataSource={data.items}
          pagination={false}
          scroll={{ x: 1500 }}
        />
        <Typography.Text type="secondary">
          抽检只读；标注员不得自选或复核抽检样本，结论与策略版本以服务端记录为准。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="质检抽检"
      description={`任务 ${taskId} · 只读查看抽样策略、复核人与结论。`}
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationQualityListPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const taskId = readTaskIdQuery(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="质检抽检" description="抽检必须按项目隔离展示。">
        <ProjectScopeRequired scope="质检抽检" />
      </PageContainer>
    );
  }
  if (!taskId) {
    return (
      <PageContainer title="质检抽检">
        <TaskScopedPrompt
          projectId={projectId}
          scope="质检抽检"
          onApply={(value) =>
            navigate(buildAnnotationQualityListPath(projectId, value))
          }
        />
      </PageContainer>
    );
  }
  return (
    <QualityListContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
