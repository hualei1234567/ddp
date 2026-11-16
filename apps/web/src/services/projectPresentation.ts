/** 项目与成员的角色/状态展示文案。 */

import type { Project, ProjectRole, ProjectStatus } from "../types/project";

export const PROJECT_ROLE_LABELS: Record<ProjectRole, string> = {
  viewer: "只读",
  editor: "编辑",
  owner: "负责人",
};

export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  ACTIVE: "活跃",
  ARCHIVED: "已归档",
};

/**
 * 是否展示"设置/成员管理"入口。
 * 这只是按钮显隐提示：服务端仍会对每个写请求做权限校验。
 */
export function canManageProject(project: Project): boolean {
  return project.allowed_actions.includes("manage_members");
}

export function isArchived(project: Project): boolean {
  return project.status === "ARCHIVED";
}
