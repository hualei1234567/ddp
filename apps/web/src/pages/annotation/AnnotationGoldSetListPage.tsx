import { ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Select, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
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
import { presentAnnotationStatus } from "../../services/annotationPresentation";
import { buildAnnotationGoldSetDetailPath } from "../../services/annotationUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import { GOLD_SET_STATUS_VALUES, type GoldSet } from "../../types/annotation";

function columns(projectId: string): TableColumnsType<GoldSet> {
  return [
    {
      title: "黄金集",
      key: "code",
      width: 240,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <Link to={buildAnnotationGoldSetDetailPath(projectId, item.gold_set_id)}>
            <Typography.Text strong>{item.name ?? item.code}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "黄金集 ID",
      dataIndex: "gold_set_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="黄金集 ID"
          to={buildAnnotationGoldSetDetailPath(projectId, value)}
        />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "Schema",
      key: "schema",
      width: 140,
      render: (_value, item) => `v${item.schema_version}`,
    },
    {
      title: "条目 / 盲测",
      key: "items",
      width: 140,
      render: (_value, item) => `${item.items.length} / ${item.blind ? "是" : "否"}`,
    },
    {
      title: "曝光策略",
      key: "exposure",
      width: 200,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <span>{item.exposure_policy}</span>
          <Typography.Text type="secondary">
            每人上限 {item.max_exposure_per_annotator} · {item.rotation_policy}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "答案",
      key: "answers",
      width: 130,
      render: () => <Tag color="orange">默认脱敏</Tag>,
    },
  ];
}

function GoldSetListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams] = useSearchParams();
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      annotationApi.listGoldSets(projectId, {
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
  } = useCursorPage(load, (item) => item.gold_set_id);
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
        pathname: "/annotation/gold-sets",
        search: query ? `?${query}` : "",
      },
      { replace: true },
    );
  };

  return (
    <PageContainer
      title="黄金集"
      description="列表始终使用脱敏视图，不包含 expected_labels。期望答案只能在详情页显式请求，并由服务端做权限与审计。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 180 }}
            options={GOLD_SET_STATUS_VALUES.map((value) => ({
              value,
              label: presentAnnotationStatus(value).label,
            }))}
            onChange={(value) => updateStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
          刷新
        </Button>
      }
    >
      {phase === "forbidden" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : phase === "loading" ? (
        <PageState variant="loading" title="正在加载黄金集" />
      ) : phase === "error" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : items.length === 0 ? (
        <PageState
          variant="empty"
          title="暂无黄金集"
          description="当前项目或筛选条件下没有黄金集。"
        />
      ) : (
        <Space direction="vertical" size={12} className="ddp-stack">
          {stale ? (
            <PageState
              compact
              variant="stale"
              description="刷新失败，展示上次成功获取的黄金集列表。"
              onRetry={reload}
            />
          ) : null}
          <Table<GoldSet>
            rowKey="gold_set_id"
            columns={columns(projectId)}
            dataSource={items}
            pagination={false}
            scroll={{ x: 1200 }}
          />
          <LoadMoreBar
            loadedCount={items.length}
            unit="个黄金集"
            hasMore={hasMore}
            loadingMore={loadingMore}
            disabled={loading || refreshing}
            error={loadMoreError}
            onLoadMore={loadMore}
            block={screens.md === false}
          />
        </Space>
      )}
    </PageContainer>
  );
}

export default function AnnotationGoldSetListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="黄金集" description="黄金集必须按项目隔离展示。">
        <ProjectScopeRequired scope="黄金集列表" />
      </PageContainer>
    );
  }
  return (
    <GoldSetListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
