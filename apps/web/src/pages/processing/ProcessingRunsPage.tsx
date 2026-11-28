import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/ui/StatusTag";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { formatDateTime, formatIdentifier } from "../../services/format";
import {
  formatRunDuration,
  presentRunStatus,
  presentStage,
} from "../../services/processingPresentation";
import { processingRunsApi } from "../../services/processingRuns";
import { processingRunDetailPath } from "../../services/processingUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type { ApiError } from "../../services/api";
import type { ProcessingRun } from "../../types/processing";

/** 与后端 ProcessingRunStatus 枚举一致；未知新值由下拉框「全部状态」兜底。 */
const RUN_STATUS_VALUES = [
  "CREATED",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "RETRYING",
] as const;

export interface ProcessingRunListBodyProps {
  items: ProcessingRun[];
  loading: boolean;
  phase: ReturnType<typeof resolvePagePhase>;
  error: ApiError | null;
  hasMore: boolean;
  loadingMore: boolean;
  stale: boolean;
  onRetry: () => void;
  onLoadMore: () => void;
}

const COLUMNS: ColumnsType<ProcessingRun> = [
  {
    title: "运行",
    dataIndex: "run_id",
    width: 150,
    render: (value: string) => (
      <Link to={processingRunDetailPath(value)}>{formatIdentifier(value)}</Link>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (value: string) => (
      <StatusTag value={value} presentation={presentRunStatus} />
    ),
  },
  {
    title: "流水线版本",
    dataIndex: "pipeline_version",
    width: 120,
    ellipsis: true,
  },
  {
    title: "当前阶段",
    dataIndex: "stage",
    width: 140,
    render: (value: string | null) => presentStage(value),
  },
  {
    title: "尝试 / 重试",
    key: "attempts",
    width: 110,
    render: (_, item) => `${item.attempt} / ${item.retry_count}`,
  },
  {
    title: "开始时间",
    dataIndex: "started_at",
    width: 170,
    render: (value: string | null) => formatDateTime(value),
  },
  {
    title: "结束时间",
    dataIndex: "finished_at",
    width: 170,
    render: (value: string | null) => formatDateTime(value),
  },
  {
    title: "耗时",
    key: "duration",
    width: 90,
    render: (_, item) => formatRunDuration(item.started_at, item.finished_at),
  },
  {
    title: "错误摘要",
    key: "error",
    render: (_, item) =>
      item.error_code || item.error_summary ? (
        <Space direction="vertical" size={0}>
          {item.error_code ? (
            <Typography.Text code>{item.error_code}</Typography.Text>
          ) : null}
          {item.error_summary ? (
            <Typography.Text type="secondary" ellipsis>
              {item.error_summary}
            </Typography.Text>
          ) : null}
        </Space>
      ) : (
        "—"
      ),
  },
];

/**
 * 列表展示层（不发请求，便于静态渲染测试）。
 * 状态优先级遵循 UX 规范 §7.2：Forbidden 优先于 Loading 与已有数据。
 */
export function ProcessingRunListBody({
  items,
  loading,
  phase,
  error,
  hasMore,
  loadingMore,
  stale,
  onRetry,
  onLoadMore,
}: ProcessingRunListBodyProps) {
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {stale && phase === "ready" ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示的是上次成功获取的数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Table<ProcessingRun>
        rowKey="run_id"
        size="small"
        loading={phase === "loading" || (loading && phase !== "ready")}
        columns={phase === "forbidden" || phase === "not-found" ? [] : COLUMNS}
        dataSource={phase === "forbidden" || phase === "not-found" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无处理运行" }}
      />
      {error && (phase === "forbidden" || phase === "not-found" || phase === "error") ? (
        <ApiErrorState error={error} onRetry={onRetry} />
      ) : null}
      {hasMore && phase === "ready" ? (
        <Button block loading={loadingMore} onClick={onLoadMore}>
          加载更多
        </Button>
      ) : null}
    </Space>
  );
}

function ProcessingRunListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      processingRunsApi.listRuns(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status: status ?? null,
      }),
    [projectId, status],
  );
  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } = useCursorPage(load, (item) => item.run_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const changeStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("status", value);
    } else {
      params.delete("status");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <PageContainer
      title="处理运行"
      description="查看每个 RawAsset 的处理流水线执行情况；动作按钮只按服务端 allowed_actions 渲染。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={RUN_STATUS_VALUES.map((value) => ({
              value,
              label: presentRunStatus(value).label,
            }))}
            onChange={(value) => changeStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
          刷新
        </Button>
      }
    >
      <ProcessingRunListBody
        items={items}
        loading={loading}
        phase={phase}
        error={error}
        hasMore={hasMore}
        loadingMore={loadingMore}
        stale={stale}
        onRetry={reload}
        onLoadMore={loadMore}
      />
      {loadMoreError ? (
        <Typography.Text type="danger">
          加载更多失败：{loadMoreError.message}
        </Typography.Text>
      ) : null}
    </PageContainer>
  );
}

export default function ProcessingRunsPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="处理运行" description="处理运行必须按项目隔离展示。">
        <ProjectScopeRequired scope="处理运行" />
      </PageContainer>
    );
  }
  const status = searchParams.get("status") ?? undefined;
  return (
    <ProcessingRunListContent
      key={`${projectId}:${status ?? ""}`}
      projectId={projectId}
      status={status}
    />
  );
}
