import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Select, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import SampleSetCreateDrawer from "../../components/annotation/SampleSetCreateDrawer";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { annotationApi } from "../../services/annotation";
import type { AnnotationSampleSetCreateInput } from "../../services/annotationSchemaApi";
import { presentAnnotationStatus } from "../../services/annotationPresentation";
import { buildAnnotationSampleSetDetailPath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  SAMPLE_SET_STATUS_VALUES,
  type AnnotationSampleSet,
} from "../../types/annotation";

function columns(projectId: string): TableColumnsType<AnnotationSampleSet> {
  return [
    {
      title: "样本集",
      key: "name",
      width: 220,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <Link to={buildAnnotationSampleSetDetailPath(projectId, item.sample_set_id)}>
            <Typography.Text strong>{item.name ?? item.code}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "样本集 ID",
      dataIndex: "sample_set_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="样本集 ID"
          to={buildAnnotationSampleSetDetailPath(projectId, value)}
        />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    { title: "策略", dataIndex: "strategy", width: 120 },
    {
      title: "候选 / 入选 / 排除 / 去重",
      key: "counts",
      width: 230,
      render: (_value, item) =>
        `${item.candidate_count} / ${item.selected_count} / ${item.excluded_count} / ${item.deduplicated_count}`,
    },
    {
      title: "快照",
      key: "snapshot",
      width: 220,
      render: (_value, item) =>
        item.snapshot_at ? (
          <Space direction="vertical" size={0}>
            <span>{formatDateTime(item.snapshot_at)}</span>
            <Typography.Text type="secondary" code>
              {item.snapshot_sha256?.slice(0, 18) ?? "—"}…
            </Typography.Text>
          </Space>
        ) : (
          "未快照"
        ),
    },
    {
      title: "数据来源",
      dataIndex: "data_source",
      width: 110,
    },
  ];
}

function SampleSetListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      annotationApi.listSampleSets(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
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
  } = useCursorPage(load, (item) => item.sample_set_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const updateStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("status", value);
    } else {
      params.delete("status");
    }
    const query = params.toString();
    navigate(
      {
        pathname: "/annotation/sample-sets",
        search: query ? `?${query}` : "",
      },
      { replace: true },
    );
  };

  return (
    <PageContainer
      title="标注样本集"
      description="只读查看样本选择结果、去重/分层统计与快照 Hash；样本集不是阶段 4 的 DatasetVersion。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 180 }}
            options={SAMPLE_SET_STATUS_VALUES.map((value) => ({
              value,
              label: presentAnnotationStatus(value).label,
            }))}
            onChange={(value) => updateStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            新建样本集
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {phase === "forbidden" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : phase === "loading" ? (
        <PageState variant="loading" title="正在加载标注样本集" />
      ) : phase === "error" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : items.length === 0 ? (
        <PageState
          variant="empty"
          title="暂无标注样本集"
          description="当前项目或筛选条件下没有样本集。"
        />
      ) : (
        <Space direction="vertical" size={12} className="ddp-stack">
          {stale ? (
            <PageState
              compact
              variant="stale"
              description="刷新失败，展示上次成功获取的样本集列表。"
              onRetry={reload}
            />
          ) : null}
          <Table<AnnotationSampleSet>
            rowKey="sample_set_id"
            columns={columns(projectId)}
            dataSource={items}
            pagination={false}
            scroll={{ x: 1300 }}
          />
          <LoadMoreBar
            loadedCount={items.length}
            unit="个样本集"
            hasMore={hasMore}
            loadingMore={loadingMore}
            disabled={loading || refreshing}
            error={loadMoreError}
            onLoadMore={loadMore}
            block={screens.md === false}
          />
        </Space>
      )}
      <SampleSetCreateDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={async (input: AnnotationSampleSetCreateInput) => {
          const created = await annotationApi.createSampleSet(projectId, input);
          setCreateOpen(false);
          navigate(
            buildAnnotationSampleSetDetailPath(projectId, created.sample_set_id),
          );
        }}
      />
    </PageContainer>
  );
}

export default function AnnotationSampleSetListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="标注样本集" description="样本集必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注样本集列表" />
      </PageContainer>
    );
  }
  return (
    <SampleSetListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
