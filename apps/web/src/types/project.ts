/** 项目与项目成员的对外类型（对齐后端 `/api/v1/projects`）。 */

export const PROJECT_STATUSES = ["ACTIVE", "ARCHIVED"] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

export const PROJECT_ROLES = ["viewer", "editor", "owner"] as const;
export type ProjectRole = (typeof PROJECT_ROLES)[number];

export interface Project {
  project_id: string;
  code: string;
  name: string;
  description: string | null;
  status: ProjectStatus;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  /** 当前登录主体在该项目中的角色；平台管理员为 null（权限不依赖成员关系）。 */
  membership_role: ProjectRole | null;
  /**
   * 服务端给出的按钮渲染提示（read / write / delete / validate /
   * manage_members）。前端只据此显隐按钮，不做权威授权。
   */
  allowed_actions: string[];
}

export interface ProjectMember {
  project_id: string;
  user_id: string;
  display_name: string;
  role: ProjectRole;
  created_at: string | null;
  updated_at: string | null;
}
