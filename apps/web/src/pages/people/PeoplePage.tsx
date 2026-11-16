import { Modal, message } from "antd";
import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { ApiError } from "../../services/api";
import { resolvePagePhase } from "../../services/pagePhase";
import { projectManagementApi } from "../../services/projectManagement";
import type { Project, ProjectMember, ProjectRole } from "../../types/project";
import MemberAddModal from "./MemberAddModal";
import PeopleBody from "./PeopleBody";

const MAX_PROJECT_OPTIONS = 100;

interface MemberSectionProps {
  /** 完整项目列表：选择器必须始终可切换，不能因为已选中就只剩一项。 */
  projects: Project[];
  projectsLoading: boolean;
  projectsError: ApiError | null;
  projectsTruncated: boolean;
  project: Project;
  onSelectProject: (projectId: string) => void;
}

function MemberSection({
  projects,
  projectsLoading,
  projectsError,
  projectsTruncated,
  project,
  onSelectProject,
}: MemberSectionProps) {
  const [messageApi, contextHolder] = message.useMessage();
  const [addOpen, setAddOpen] = useState(false);

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      projectManagementApi.listMembers(project.project_id, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [project.project_id],
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
  } = useCursorPage(load, (member) => member.user_id);
  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const changeRole = async (member: ProjectMember, role: ProjectRole) => {
    try {
      await projectManagementApi.changeMemberRole(
        project.project_id,
        member.user_id,
        role,
      );
      messageApi.success(`${member.display_name} 的角色已更新`);
      reload();
    } catch (caught) {
      if (caught instanceof Error) {
        messageApi.error(caught.message);
      }
    }
  };

  const remove = (member: ProjectMember) => {
    Modal.confirm({
      title: `移除成员：${member.display_name}`,
      content:
        "移除后该用户立即失去本项目访问权限；项目必须至少保留一名负责人，移除最后一名负责人会被服务端拒绝。",
      okText: "移除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          await projectManagementApi.removeMember(
            project.project_id,
            member.user_id,
          );
          messageApi.success("成员已移除");
          reload();
        } catch (caught) {
          if (caught instanceof Error) {
            messageApi.error(caught.message);
          }
          // 抛出以保持确认框打开，让用户看到失败原因后决定是否重试。
          throw caught;
        }
      },
    });
  };

  return (
    <>
      {contextHolder}
      <PeopleBody
        projects={projects}
        projectsLoading={projectsLoading}
        projectsError={projectsError}
        projectsTruncated={projectsTruncated}
        selectedProject={project}
        phase={phase}
        error={error}
        members={items}
        hasMore={hasMore}
        loading={loading}
        refreshing={refreshing}
        loadingMore={loadingMore}
        loadMoreError={loadMoreError}
        stale={stale}
        onSelectProject={onSelectProject}
        onAddMember={() => setAddOpen(true)}
        onChangeRole={(member, role) => void changeRole(member, role)}
        onRemove={remove}
        onRetry={reload}
        onRefresh={reload}
        onLoadMore={loadMore}
      />
      <MemberAddModal
        open={addOpen}
        projectId={project.project_id}
        onClose={() => setAddOpen(false)}
        onAdded={reload}
      />
    </>
  );
}

function PeopleContent() {
  const [searchParams, setSearchParams] = useSearchParams();
  const routeProjectId = useProjectId();
  const projectsRequest = useRequest(async (signal) => {
    // 同时取活跃与归档项目：归档项目仍可维护成员，选择器需要能找到它。
    const [active, archived] = await Promise.all([
      projectManagementApi.listProjects({
        status: "ACTIVE",
        limit: MAX_PROJECT_OPTIONS,
        signal,
      }),
      projectManagementApi.listProjects({
        status: "ARCHIVED",
        limit: MAX_PROJECT_OPTIONS,
        signal,
      }),
    ]);
    return {
      projects: [...active.items, ...archived.items],
      truncated: active.next_cursor !== null || archived.next_cursor !== null,
    };
  });
  const projects = projectsRequest.data?.projects ?? [];
  const selectedProject =
    projects.find((project) => project.project_id === routeProjectId) ?? null;

  const selectProject = (projectId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("project_id", projectId);
    setSearchParams(next, { replace: true });
  };

  let content;
  if (projectsRequest.error && !selectedProject) {
    content = (
      <PeopleBody
        projects={[]}
        projectsLoading={false}
        projectsError={projectsRequest.error}
        projectsTruncated={false}
        selectedProject={null}
        phase="ready"
        error={null}
        members={[]}
        hasMore={false}
        loading={false}
        refreshing={false}
        loadingMore={false}
        loadMoreError={null}
        stale={false}
        onSelectProject={selectProject}
        onAddMember={() => undefined}
        onChangeRole={() => undefined}
        onRemove={() => undefined}
        onRetry={projectsRequest.reload}
        onRefresh={projectsRequest.reload}
        onLoadMore={() => undefined}
      />
    );
  } else if (!selectedProject) {
    content = (
      <PeopleBody
        projects={projects}
        projectsLoading={projectsRequest.loading}
        projectsError={projectsRequest.error}
        projectsTruncated={projectsRequest.data?.truncated ?? false}
        selectedProject={null}
        phase="ready"
        error={null}
        members={[]}
        hasMore={false}
        loading={false}
        refreshing={projectsRequest.refreshing}
        loadingMore={false}
        loadMoreError={null}
        stale={false}
        onSelectProject={selectProject}
        onAddMember={() => undefined}
        onChangeRole={() => undefined}
        onRemove={() => undefined}
        onRetry={projectsRequest.reload}
        onRefresh={projectsRequest.reload}
        onLoadMore={() => undefined}
      />
    );
  } else {
    // 换项目时用 key 重新挂载成员列表，避免把两个项目的成员混在一份 items 里。
    content = (
      <MemberSection
        key={selectedProject.project_id}
        projects={projects}
        projectsLoading={projectsRequest.loading}
        projectsError={projectsRequest.error}
        projectsTruncated={projectsRequest.data?.truncated ?? false}
        project={selectedProject}
        onSelectProject={selectProject}
      />
    );
  }

  return <>{content}</>;
}

export default function PeoplePage() {
  return <PeopleContent />;
}
