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
  buildAnnotationIAADetailPath,
  buildAnnotationIAAListPath,
  buildAnnotationTaskDetailPath,
  readTaskIdQuery,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { IAAReport } from "../../types/annotation";

function columns(projectId: string): TableColumnsType<IAAReport> {
  return [
    {
      title: "报告",
      key: "report",
      width: 240,
      render: (_value, report) => (
        <Link to={buildAnnotationIAADetailPath(projectId, report.report_id)}>
          <IdentifierValue value={report.report_id} label="IAA 报告 ID" />
        </Link>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 140,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "样本 / 重叠 / 标注员",
      key: "counts",
      width: 220,
      render: (_value, report) =>
        `${report.sample_item_count} / ${report.overlap_item_count} / ${report.annotator_count}`,
    },
    {
      title: "指标",
      key: "metrics",
      width: 260,
      render: (_value, report) => (
        <Space wrap size={4}>
          {report.metrics.length === 0 ? (
            <Typography.Text type="secondary">暂无指标</Typography.Text>
          ) : (
            report.metrics.slice(0, 3).map((metric) => (
              <Tag key={`${metric.metric}-${metric.label_key}`}>
                {metric.metric}:{" "}
                {metric.value === null ? "N/A" : metric.value.toFixed(4)}
              </Tag>
            ))
          )}
        </Space>
      ),
    },
    {
      title: "计算版本",
      dataIndex: "metric_definitions_version",
      width: 200,
    },
    {
      title: "计算时间",
      dataIndex: "computed_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
  ];
}

function IAAListContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.listIAAReports(projectId, taskId, { signal }),
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
    body = <PageState variant="loading" title="正在加载 IAA 报告" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (data.items.length === 0) {
    body = (
      <PageState
        variant="empty"
        title="暂无 IAA 报告"
        description="任务尚未计算 IAA。样本不足时服务端返回 INSUFFICIENT_DATA，而不是 0。"
      />
    );
  } else {
    body = (
      <Space direction="vertical" size={12} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的 IAA 报告。"
            onRetry={reload}
          />
        ) : null}
        <Table<IAAReport>
          rowKey="report_id"
          columns={columns(projectId)}
          dataSource={data.items}
          pagination={false}
          scroll={{ x: 1200 }}
        />
        <Typography.Text type="secondary">
          IAA 只在同任务、同 Schema 版本、至少两名标注员独立标注的重叠样本上计算；
          INSUFFICIENT_DATA 表示样本不足，值为 N/A。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="IAA 报告"
      description={`任务 ${taskId} · 标注者间一致性只读报告。`}
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationIAAListPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const taskId = readTaskIdQuery(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="IAA 报告" description="IAA 必须按项目隔离展示。">
        <ProjectScopeRequired scope="IAA 报告" />
      </PageContainer>
    );
  }
  if (!taskId) {
    return (
      <PageContainer title="IAA 报告">
        <TaskScopedPrompt
          projectId={projectId}
          scope="IAA 报告"
          onApply={(value) => navigate(buildAnnotationIAAListPath(projectId, value))}
        />
      </PageContainer>
    );
  }
  return (
    <IAAListContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
