import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import type { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  canManageProject,
  isArchived,
  PROJECT_ROLE_LABELS,
  PROJECT_STATUS_LABELS,
} from "../../services/projectPresentation";
import {
  PROJECT_ROLES,
  type Project,
  type ProjectMember,
  type ProjectRole,
} from "../../types/project";

export interface PeopleBodyProps {
  projects: Project[];
  projectsLoading: boolean;
  projectsError: ApiError | null;
  projectsTruncated: boolean;
  selectedProject: Project | null;
  phase: PagePhase;
  error: ApiError | null;
  members: ProjectMember[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  stale: boolean;
  onSelectProject: (projectId: string) => void;
  onAddMember: () => void;
  onChangeRole: (member: ProjectMember, role: ProjectRole) => void;
  onRemove: (member: ProjectMember) => void;
  onRetry: () => void;
  onRefresh: () => void;
  onLoadMore: () => void;
}

/** 人员与团队主体（纯展示）：所有状态由 props 决定，便于逐状态渲染测试。 */
export function PeopleBody({
  projects,
  projectsLoading,
  projectsError,
  projectsTruncated,
  selectedProject,
  phase,
  error,
  members,
  hasMore,
  loading,
  refreshing,
  loadingMore,
  loadMoreError,
  stale,
  onSelectProject,
  onAddMember,
  onChangeRole,
  onRemove,
  onRetry,
  onRefresh,
  onLoadMore,
}: PeopleBodyProps) {
  const canManage = selectedProject ? canManageProject(selectedProject) : false;

  const columns = useMemo<ColumnsType<ProjectMember>>(
    () => [
      {
        title: "成员",
        key: "member",
        render: (_, member) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{member.display_name}</Typography.Text>
            <Typography.Text
              type="secondary"
              code
              copyable={{ text: member.user_id }}
            >
              {member.user_id}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: "项目角色",
        key: "role",
        width: 180,
        render: (_, member) =>
          canManage ? (
            <Select<ProjectRole>
              size="small"
              value={member.role}
              style={{ width: 120 }}
              aria-label={`调整 ${member.display_name} 的角色`}
              options={PROJECT_ROLES.map((role) => ({
                value: role,
                label: PROJECT_ROLE_LABELS[role],
              }))}
              onChange={(role) => onChangeRole(member, role)}
            />
          ) : (
            <Tag>{PROJECT_ROLE_LABELS[member.role]}</Tag>
          ),
      },
      {
        title: "加入时间",
        dataIndex: "created_at",
        width: 170,
        render: (value: string | null) => formatDateTime(value),
      },
      {
        title: "操作",
        key: "actions",
        width: 120,
        render: (_, member) =>
          canManage ? (
            <Button
              size="small"
              danger
              aria-label={`移除 ${member.display_name}`}
              onClick={() => onRemove(member)}
            >
              移除
            </Button>
          ) : (
            <Typography.Text type="secondary">—</Typography.Text>
          ),
      },
    ],
    [canManage, onChangeRole, onRemove],
  );

  let content;
  if (projectsError && !selectedProject) {
    content = <ApiErrorState error={projectsError} onRetry={onRefresh} />;
  } else if (!selectedProject) {
    content = (
      <PageState
        variant="empty"
        title={projectsLoading ? "正在加载项目" : "未选择项目"}
        description={
          projectsLoading
            ? "正在获取可访问的项目列表。"
            : "人员与角色始终属于某个项目。请在上方选择项目，或通过 URL 参数 project_id 指定。"
        }
        action={<Link to="/projects">前往项目管理</Link>}
      />
    );
  } else if (phase === "forbidden") {
    content = <PageState variant="forbidden" />;
  } else if (phase === "loading") {
    content = <PageState variant="loading" />;
  } else if (phase === "error" && error) {
    content = <ApiErrorState error={error} onRetry={onRetry} />;
  } else if (members.length === 0) {
    content = (
      <PageState
        variant="empty"
        title="该项目还没有成员"
        description="项目必须至少保留一名负责人；请添加成员并授予角色，平台不会自动填充成员。"
      />
    );
  } else {
    content = (
      <Table<ProjectMember>
        rowKey="user_id"
        columns={columns}
        dataSource={members}
        pagination={false}
        scroll={{ x: 800 }}
      />
    );
  }

  return (
    <PageContainer
      title="人员与团队"
      description="按项目维护成员与角色（只读 / 编辑 / 负责人）。角色只决定项目内权限，平台级权限由身份系统决定。"
      extra={
        <Space wrap>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={!canManage}
            onClick={onAddMember}
          >
            添加成员
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
        <Space wrap>
          <Select<string>
            style={{ minWidth: 320 }}
            value={selectedProject?.project_id}
            loading={projectsLoading}
            placeholder={projectsLoading ? "正在加载项目" : "选择项目"}
            aria-label="选择项目"
            onChange={onSelectProject}
            options={projects.map((project) => ({
              value: project.project_id,
              label: `${project.name}（${project.code}）${
                isArchived(project) ? " · 已归档" : ""
              }`,
            }))}
          />
          <Link to="/projects">
            <Button type="link">管理项目</Button>
          </Link>
        </Space>
      }
    >
      {projectsTruncated ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="项目列表只展示前 100 个"
          description="可通过 URL 参数 project_id 直接指定未出现在下拉框中的项目。"
        />
      ) : null}
      {selectedProject ? (
        <Space direction="vertical" size={16} className="ddp-stack">
          <Space wrap>
            <Typography.Text strong>{selectedProject.name}</Typography.Text>
            <Tag color={isArchived(selectedProject) ? "default" : "green"}>
              {PROJECT_STATUS_LABELS[selectedProject.status]}
            </Tag>
            <Typography.Text type="secondary" code>
              {selectedProject.code}
            </Typography.Text>
          </Space>
          {isArchived(selectedProject) ? (
            <Alert
              type="warning"
              showIcon
              message="该项目已归档"
              description="归档只把项目移出默认列表，不会删除数据；成员与角色仍可维护，需要恢复请到项目管理修改状态。"
            />
          ) : null}
          {canManage ? (
            <Alert
              type="info"
              showIcon
              message="你可以管理本项目成员"
              description="仅有项目负责人或平台管理员可以添加成员、调整角色与移除成员。"
            />
          ) : (
            <Alert
              type="warning"
              showIcon
              message="当前角色只读"
              description="成员与角色由项目负责人或平台管理员维护；按钮隐藏只是提示，服务端仍会对每个写请求做权限校验。"
            />
          )}
          {stale ? (
            <PageState
              variant="stale"
              compact
              description="最近一次刷新失败，当前展示的是上次成功获取的成员列表。"
              onRetry={onRetry}
            />
          ) : null}
          {content}
          {phase === "ready" && members.length > 0 ? (
            <LoadMoreBar
              loadedCount={members.length}
              unit="名成员"
              hasMore={hasMore}
              loadingMore={loadingMore}
              disabled={loading || refreshing}
              error={loadMoreError}
              onLoadMore={onLoadMore}
            />
          ) : null}
        </Space>
      ) : (
        content
      )}
    </PageContainer>
  );
}

export default PeopleBody;
