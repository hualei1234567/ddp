import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Select, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import SchemaDefinitionDrawer from "../../components/annotation/SchemaDefinitionDrawer";
import type { SchemaDefinitionSubmitPayload } from "../../components/annotation/SchemaDefinitionDrawer";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { annotationApi } from "../../services/annotation";
import type { AnnotationSchemaCreateInput } from "../../services/annotationSchemaApi";
import { presentAnnotationStatus } from "../../services/annotationPresentation";
import {
  buildAnnotationSchemaDetailPath,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  ANNOTATION_SCHEMA_STATUS_VALUES,
  type AnnotationSchema,
} from "../../types/annotation";

function columns(projectId: string): TableColumnsType<AnnotationSchema> {
  return [
    {
      title: "Schema",
      key: "name",
      width: 240,
      render: (_value, schema) => (
        <Space direction="vertical" size={0}>
          <Link to={buildAnnotationSchemaDetailPath(projectId, schema.schema_id)}>
            <Typography.Text strong>{schema.name}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{schema.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "Schema ID",
      dataIndex: "schema_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="Schema ID"
          to={buildAnnotationSchemaDetailPath(projectId, value)}
        />
      ),
    },
    {
      title: "版本",
      dataIndex: "schema_version",
      width: 90,
      render: (value: number) => `v${value}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "标签数",
      dataIndex: "labels",
      width: 90,
      render: (labels: unknown[]) => labels.length,
    },
    {
      title: "兼容性",
      dataIndex: "compatibility",
      width: 180,
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
  ];
}

function SchemaListContent({
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
      annotationApi.listSchemas(projectId, {
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
  } = useCursorPage(load, (schema) => schema.schema_id);
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
        pathname: "/annotation/schemas",
        search: query ? `?${query}` : "",
      },
      { replace: true },
    );
  };

  return (
    <PageContainer
      title="标注 Schema"
      description="查看 Schema 版本、标签定义与快照 Hash；可新建 Schema 并推进发布/废弃。已发布版本不可原地修改。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 180 }}
            options={ANNOTATION_SCHEMA_STATUS_VALUES.map((value) => ({
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
            新建 Schema
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
        <PageState variant="loading" title="正在加载标注 Schema" />
      ) : phase === "error" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : items.length === 0 ? (
        <PageState
          variant="empty"
          title="暂无标注 Schema"
          description="当前项目或筛选条件下没有 Schema。"
        />
      ) : (
        <Space direction="vertical" size={12} className="ddp-stack">
          {stale ? (
            <PageState
              compact
              variant="stale"
              description="刷新失败，展示上次成功获取的 Schema 列表。"
              onRetry={reload}
            />
          ) : null}
          <Table<AnnotationSchema>
            rowKey="schema_id"
            columns={columns(projectId)}
            dataSource={items}
            pagination={false}
            scroll={{ x: 1100 }}
          />
          <LoadMoreBar
            loadedCount={items.length}
            unit="个 Schema"
            hasMore={hasMore}
            loadingMore={loadingMore}
            disabled={loading || refreshing}
            error={loadMoreError}
            onLoadMore={loadMore}
            block={screens.md === false}
          />
        </Space>
      )}
      <SchemaDefinitionDrawer
        open={createOpen}
        mode="create"
        onClose={() => setCreateOpen(false)}
        onSubmit={async ({ input }: SchemaDefinitionSubmitPayload) => {
          const created = await annotationApi.createSchema(
            projectId,
            input as AnnotationSchemaCreateInput,
          );
          setCreateOpen(false);
          navigate(buildAnnotationSchemaDetailPath(projectId, created.schema_id));
        }}
      />
    </PageContainer>
  );
}

export default function AnnotationSchemaListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="标注 Schema" description="Schema 必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注 Schema 列表" />
      </PageContainer>
    );
  }
  return (
    <SchemaListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
