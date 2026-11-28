import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/modelops/ActionBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import PageContainer from "../../components/ui/PageContainer";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import {
  presentRunStatus,
  presentStage,
  presentStepStatus,
} from "../../services/processingPresentation";
import { formatDateTime } from "../../services/format";
import { processingRunDetailPath, processingRunListPath } from "../../services/processingUrl";
import { processingRunsApi } from "../../services/processingRuns";
import type { ProcessingRun, ProcessingRunStep } from "../../types/processing";

/** 阶段步骤列。 */
const STEP_COLUMNS: ColumnsType<ProcessingRunStep> = [
  {
    title: "阶段",
    dataIndex: "stage",
    width: 160,
    render: (value: string) => presentStage(value),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (value: string) => (
      <StatusTag value={value} presentation={presentStepStatus} />
    ),
  },
  {
    title: "尝试",
    dataIndex: "attempt",
    width: 70,
  },
  {
    title: "日志摘要 / 错误",
    key: "summary",
    render: (_, item) => (
      <Space direction="vertical" size={0}>
        {item.log_summary ? (
          <Typography.Text type="secondary" ellipsis>
            {item.log_summary}
          </Typography.Text>
        ) : null}
        {item.error_code ? (
          <Typography.Text code>{item.error_code}</Typography.Text>
        ) : null}
        {item.error_summary ? (
          <Typography.Text type="danger" ellipsis>
            {item.error_summary}
          </Typography.Text>
        ) : null}
        {!item.log_summary && !item.error_code && !item.error_summary
          ? "—"
          : null}
      </Space>
    ),
  },
];

export interface ProcessingRunDetailBodyProps {
  data: ProcessingRun | null;
  loading: boolean;
  error: ApiError | null;
  stale: boolean;
  onRetry: () => void;
  onAct: (action: string, payload: { reason: string | null }) => Promise<void>;
  busy: boolean;
}

/**
 * 详情页的**纯展示层**：只吃 props，不发请求。
 *
 * 为什么拆出来：容器把取数与渲染耦合在一起时，测试只能「复刻分支逻辑」——
 * 那测的是测试自己写的副本，组件改了测试照样绿（实测踩到：复刻版只输出
 * message，真实组件输出 message + 错误码 + request_id，断言必然对不上）。
 * 拆出 Body 后测试直接渲染真实组件，展示层契约才真正被钉住。
 */
export function ProcessingRunDetailBody({
  data,
  loading,
  error,
  stale,
  onRetry,
  onAct,
  busy,
}: ProcessingRunDetailBodyProps) {
  if (error?.isForbidden) {
    return <ApiErrorState error={error} onRetry={onRetry} />;
  }
  if (loading && data === null) {
    return <PageState variant="loading" title="正在加载处理运行" />;
  }
  if (data === null) {
    return error ? (
      <ApiErrorState error={error} onRetry={onRetry} />
    ) : (
      <PageState
        variant="empty"
        description="未找到该处理运行（可能已被清理，或不属于当前项目）。"
      />
    );
  }

  const run = data;
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示的是上次成功获取的数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Descriptions
        size="small"
        column={2}
        items={[
          {
            key: "run",
            label: "运行 ID",
            children: <Typography.Text copyable>{run.run_id}</Typography.Text>,
          },
          {
            key: "status",
            label: "状态",
            children: (
              <StatusTag value={run.status} presentation={presentRunStatus} />
            ),
          },
          {
            key: "pipeline",
            label: "流水线版本",
            children: run.pipeline_version,
          },
          {
            key: "stage",
            label: "当前阶段",
            children: presentStage(run.stage),
          },
          {
            key: "attempts",
            label: "尝试 / 重试",
            children: `${run.attempt} / ${run.retry_count}`,
          },
          {
            key: "started",
            label: "开始时间",
            children: formatDateTime(run.started_at),
          },
          {
            key: "finished",
            label: "结束时间",
            children: formatDateTime(run.finished_at),
          },
          {
            key: "input",
            label: "输入引用",
            span: 2,
            children:
              run.input_refs.length > 0 ? (
                run.input_refs.map((item) => (
                  <Typography.Text key={item} code>
                    {item}
                  </Typography.Text>
                ))
              ) : (
                "—"
              ),
          },
          {
            key: "output",
            label: "输出引用",
            span: 2,
            children:
              run.output_refs.length > 0 ? (
                run.output_refs.map((item) => (
                  <Typography.Text key={item} code>
                    {item}
                  </Typography.Text>
                ))
              ) : (
                "—"
              ),
          },
          {
            key: "error",
            label: "错误",
            span: 2,
            children:
              run.error_code || run.error_summary ? (
                <Space direction="vertical" size={0}>
                  {run.error_code ? (
                    <Typography.Text code>{run.error_code}</Typography.Text>
                  ) : null}
                  {run.error_summary ? (
                    <Typography.Text type="danger">{run.error_summary}</Typography.Text>
                  ) : null}
                </Space>
              ) : (
                "—"
              ),
          },
        ]}
      />
      <Typography.Title level={5} style={{ margin: 0 }}>
        阶段步骤
      </Typography.Title>
      <Table<ProcessingRunStep>
        rowKey="step_id"
        size="small"
        columns={STEP_COLUMNS}
        dataSource={run.steps ?? []}
        pagination={false}
        locale={{
          emptyText: "该运行暂无步骤记录（列表接口不内联步骤，完整历史以后端为准）",
        }}
      />
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          运行动作
        </Typography.Title>
        <ActionBar actions={run.allowed_actions} busy={busy} onAction={onAct} />
      </Space>
    </Space>
  );
}

export function RunDetailContent({
  projectId,
  runId,
}: {
  projectId: string;
  runId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => processingRunsApi.getRun(projectId, runId, { signal }),
    [projectId, runId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);

  // cancel 动作的原因由 ActionBar 收集并写入审计；后端动作接口不读 body，
  // 这里只需转发动作名（参数少于回调类型是合法的）。
  const act = async (action: string) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await processingRunsApi.actRun(projectId, runId, action, {
        version: data.version,
      });
      reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageContainer
      title="处理运行详情"
      description={`运行 ${runId}`}
      extra={
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(processingRunListPath())}
          >
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      <ProcessingRunDetailBody
        data={data}
        loading={loading}
        error={error}
        stale={stale}
        onRetry={reload}
        onAct={act}
        busy={busy}
      />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        当前页面：<Link to={processingRunDetailPath(runId)}>{runId}</Link>
      </Typography.Paragraph>
    </PageContainer>
  );
}

export default function ProcessingRunDetailPage() {
  const projectId = useProjectId();
  const { runId } = useParams<{ runId: string }>();
  if (!projectId || !runId) {
    return (
      <PageContainer title="处理运行详情" description="处理运行必须按项目隔离展示。">
        <ProjectScopeRequired scope="处理运行详情" />
      </PageContainer>
    );
  }
  return (
    <RunDetailContent key={`${projectId}:${runId}`} projectId={projectId} runId={runId} />
  );
}
