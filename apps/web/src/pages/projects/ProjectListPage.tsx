import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase, type PagePhase } from "../../services/pagePhase";
import { projectManagementApi } from "../../services/projectManagement";
import {
  canManageProject,
  PROJECT_ROLE_LABELS,
  PROJECT_STATUS_LABELS,
  isArchived,
} from "../../services/projectPresentation";
import {
  PROJECT_STATUSES,
  type Project,
  type ProjectStatus,
} from "../../types/project";
import ProjectFormModal from "./ProjectFormModal";

export interface ProjectListBodyProps {
  phase: PagePhase;
  error: ApiError | null;
  items: Project[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  stale: boolean;
  statusFilter: ProjectStatus;
  onStatusFilterChange: (status: ProjectStatus) => void;
  onCreate: () => void;
  onEnter: (project: Project) => void;
  onEdit: (project: Project) => void;
  onRetry: () => void;
  onRefresh: () => void;
  onLoadMore: () => void;
}

function statusTag(project: Project) {
  return (
    <Space size={4} wrap>
      <Tag color={isArchived(project) ? "default" : "green"}>
        {PROJECT_STATUS_LABELS[project.status]}
      </Tag>
      {project.membership_role === null ? (
        <Tag color="blue">平台管理员</Tag>
      ) : (
        <Tag>{PROJECT_ROLE_LABELS[project.membership_role]}</Tag>
      )}
    </Space>
  );
}

/** 列表主体（纯展示）：状态、文案与按钮行为都由 props 决定，便于状态测试。 */
export function ProjectListBody({
  phase,
  error,
  items,
  hasMore,
  loading,
  refreshing,
  loadingMore,
  loadMoreError,
  stale,
  statusFilter,
  onStatusFilterChange,
  onCreate,
  onEnter,
  onEdit,
  onRetry,
  onRefresh,
  onLoadMore,
}: ProjectListBodyProps) {
  const columns = useMemo<ColumnsType<Project>>(
    () => [
      {
        title: "项目",
        key: "name",
        render: (_, project) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{project.name}</Typography.Text>
            <Typography.Text type="secondary" code>
              {project.code}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: "状态 / 我的角色",
        key: "status",
        width: 180,
        render: (_, project) => statusTag(project),
      },
      {
        title: "说明",
        dataIndex: "description",
        render: (value: string | null) => (
          <Typography.Text type="secondary">
            {value ?? "未填写说明"}
          </Typography.Text>
        ),
      },
      {
        title: "更新时间",
        dataIndex: "updated_at",
        width: 170,
        render: (value: string | null) => formatDateTime(value),
      },
      {
        title: "操作",
        key: "actions",
        width: 200,
        render: (_, project) => (
          <Space wrap>
            <Button
              size="small"
              type="primary"
              onClick={() => onEnter(project)}
            >
              进入项目
            </Button>
            {canManageProject(project) ? (
              <Button size="small" onClick={() => onEdit(project)}>
                设置
              </Button>
            ) : null}
          </Space>
        ),
      },
    ],
    [onEdit, onEnter],
  );

  let content;
  if (phase === "forbidden") {
    content = <PageState variant="forbidden" />;
  } else if (phase === "loading") {
    content = <PageState variant="loading" />;
  } else if (phase === "error" && error) {
    content = <ApiErrorState error={error} onRetry={onRetry} />;
  } else if (items.length === 0) {
    content = (
      <PageState
        variant="empty"
        title={
          statusFilter === "ARCHIVED" ? "没有已归档的项目" : "还没有可见的项目"
        }
        description={
          statusFilter === "ARCHIVED"
            ? "归档只把项目移出默认列表，不会删除数据。"
            : "项目只能由平台管理员创建，平台不会自动生成或伪造默认项目。请联系管理员创建，或在部署环境配置 VITE_PROJECT_ID。"
        }
        action={
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            创建项目
          </Button>
        }
      />
    );
  } else {
    content = (
      <Table<Project>
        rowKey="project_id"
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1100 }}
      />
    );
  }

  return (
    <PageContainer
      title="项目管理"
      description="查看与维护项目。项目是平台数据隔离的边界：所有业务数据都归属且只归属一个项目，权限以服务端校验为准。"
      extra={
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            创建项目
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={onRefresh}
          >
            刷新
          </Button>
        </Space>
      }
      filters={
        <Select<ProjectStatus>
          value={statusFilter}
          onChange={onStatusFilterChange}
          style={{ width: 160 }}
          options={PROJECT_STATUSES.map((status) => ({
            value: status,
            label: PROJECT_STATUS_LABELS[status],
          }))}
          aria-label="项目状态筛选"
        />
      }
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="不提供删除项目"
        description="项目被全部业务数据以级联外键引用，删除等于一次性销毁采集、Episode、标注、数据集与审计记录且不可恢复。下线请使用「设置 → 已归档」。"
      />
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的项目列表。"
          onRetry={onRetry}
        />
      ) : null}
      {content}
      {phase === "ready" && items.length > 0 ? (
        <LoadMoreBar
          loadedCount={items.length}
          unit="个项目"
          hasMore={hasMore}
          loadingMore={loadingMore}
          disabled={loading || refreshing}
          error={loadMoreError}
          onLoadMore={onLoadMore}
        />
      ) : null}
    </PageContainer>
  );
}

interface ProjectListContentProps {
  statusFilter: ProjectStatus;
  onStatusFilterChange: (status: ProjectStatus) => void;
}

function ProjectListContent({
  statusFilter,
  onStatusFilterChange,
}: ProjectListContentProps) {
  const navigate = useNavigate();
  const [formOpen, setFormOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Project | null>(null);

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      projectManagementApi.listProjects({
        status: statusFilter,
        cursor: options.cursor,
        signal: options.signal,
      }),
    [statusFilter],
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
  } = useCursorPage(load, (project) => project.project_id);
  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const openCreate = () => {
    setEditTarget(null);
    setFormOpen(true);
  };
  const openEdit = (project: Project) => {
    setEditTarget(project);
    setFormOpen(true);
  };

  return (
    <>
      <ProjectListBody
        phase={phase}
        error={error}
        items={items}
        hasMore={hasMore}
        loading={loading}
        refreshing={refreshing}
        loadingMore={loadingMore}
        loadMoreError={loadMoreError}
        stale={stale}
        statusFilter={statusFilter}
        onStatusFilterChange={onStatusFilterChange}
        onCreate={openCreate}
        onEnter={(project) =>
          navigate(`/devices?project_id=${project.project_id}`)
        }
        onEdit={openEdit}
        onRetry={reload}
        onRefresh={reload}
        onLoadMore={loadMore}
      />
      <ProjectFormModal
        open={formOpen}
        project={editTarget}
        onClose={() => setFormOpen(false)}
        onSubmitted={reload}
      />
    </>
  );
}

/**
 * 切换状态筛选时用 ``key`` 重新挂载列表：``useCursorPage`` 只对挂载期内的
 * 同一数据源分页，换数据源必须重新从第一页开始，否则会把归档项目与活跃项目
 * 混在同一份 items 里。
 */
export default function ProjectListPage() {
  const [statusFilter, setStatusFilter] = useState<ProjectStatus>("ACTIVE");
  return (
    <ProjectListContent
      key={statusFilter}
      statusFilter={statusFilter}
      onStatusFilterChange={setStatusFilter}
    />
  );
}
