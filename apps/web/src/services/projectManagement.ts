/**
 * 项目管理与人员/团队 API。
 *
 * 与 `services/project.ts` 的区别：后者只处理"当前项目上下文"（URL/会话缓存），
 * 本文件负责真正的 `/api/v1/projects` 读写。二者命名相近但职责不同，
 * 不要合并——上下文解析是纯函数，这里全部是网络调用与结构校验。
 */

import type { CursorPage } from "../types/api";
import {
  PROJECT_ROLES,
  PROJECT_STATUSES,
  type Project,
  type ProjectMember,
  type ProjectRole,
  type ProjectStatus,
} from "../types/project";
import { ApiClient, apiClient } from "./api";
import { formatIfMatch } from "./idempotency";
import { assertPageScope } from "./projectScope";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readEnum,
  readOptionalString,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

const PROJECTS_PATH = "/api/v1/projects";
const MAX_PAGE_LIMIT = 100;

export interface ProjectCreateInput {
  code: string;
  name: string;
  description?: string | null;
}

export interface ProjectPatchInput {
  name?: string;
  description?: string | null;
  status?: ProjectStatus;
}

export interface ProjectListOptions {
  status?: ProjectStatus;
  cursor?: string;
  limit?: number;
  signal?: AbortSignal;
}

export interface MemberListOptions {
  cursor?: string;
  limit?: number;
  signal?: AbortSignal;
}

function readOptionalRole(
  record: Record<string, unknown>,
  key: string,
  path: string,
): ProjectRole | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readEnum(record, key, path, PROJECT_ROLES);
}

function readAllowedActions(
  record: Record<string, unknown>,
  path: string,
): string[] {
  const raw = record.allowed_actions;
  if (!Array.isArray(raw) || raw.some((item) => typeof item !== "string")) {
    throw new ContractValidationError(
      `${path}.allowed_actions 必须是字符串数组`,
    );
  }
  return raw as string[];
}

export function parseProject(value: unknown, path: string): Project {
  const record = asRecord(value, path);
  return {
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    description: readOptionalString(
      record,
      "description",
      `${path}.description`,
    ),
    status: readEnum(record, "status", `${path}.status`, PROJECT_STATUSES),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    membership_role: readOptionalRole(
      record,
      "membership_role",
      `${path}.membership_role`,
    ),
    allowed_actions: readAllowedActions(record, path),
  };
}

export function parseProjectMember(
  value: unknown,
  path: string,
): ProjectMember {
  const record = asRecord(value, path);
  return {
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    user_id: readUuid(record, "user_id", `${path}.user_id`),
    display_name: readString(record, "display_name", `${path}.display_name`),
    role: readEnum(record, "role", `${path}.role`, PROJECT_ROLES),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
  };
}

export function parseProjectResponse(value: unknown): Project {
  return parseProject(value, "项目");
}

export function parseProjectMemberResponse(value: unknown): ProjectMember {
  return parseProjectMember(value, "项目成员");
}

export function parseProjectPage(value: unknown): CursorPage<Project> {
  return parseCursorPage(value, "项目列表", parseProject);
}

export function parseProjectMemberPage(
  value: unknown,
  projectId: string,
): CursorPage<ProjectMember> {
  const page = parseCursorPage(value, "项目成员列表", parseProjectMember);
  // 服务端已按路径作用域过滤；这里再校验一次，防止响应串项目。
  return assertPageScope(page, projectId, "项目成员列表");
}

function projectPath(projectId: string): string {
  return `${PROJECTS_PATH}/${encodeURIComponent(projectId.trim())}`;
}

function memberPath(projectId: string, userId: string): string {
  return `${projectPath(projectId)}/members/${encodeURIComponent(userId.trim())}`;
}

/**
 * 工厂形态便于测试用 ``ApiClient({ fetchImpl })`` 注入假 fetch。
 * 页面只依赖导出的单例 ``projectManagementApi``。
 */
export function createProjectManagementApi(client: ApiClient = apiClient) {
  return {
    listProjects(
      options: ProjectListOptions = {},
    ): Promise<CursorPage<Project>> {
      return client.get(PROJECTS_PATH, {
        signal: options.signal,
        query: {
          status: options.status,
          cursor: options.cursor,
          limit: options.limit ?? MAX_PAGE_LIMIT,
        },
        validate: parseProjectPage,
      });
    },

    createProject(
      input: ProjectCreateInput,
      options: { signal?: AbortSignal } = {},
    ): Promise<Project> {
      return client.request(PROJECTS_PATH, {
        method: "POST",
        body: input,
        signal: options.signal,
        validate: parseProjectResponse,
      });
    },

    getProject(
      projectId: string,
      options: { signal?: AbortSignal } = {},
    ): Promise<Project> {
      return client.get(projectPath(projectId), {
        signal: options.signal,
        validate: parseProjectResponse,
      });
    },

    /** PATCH 必须带 If-Match：版本不符时服务端返回 409，避免静默覆盖。 */
    patchProject(
      projectId: string,
      input: ProjectPatchInput,
      options: { version: number; signal?: AbortSignal },
    ): Promise<Project> {
      return client.request(projectPath(projectId), {
        method: "PATCH",
        body: input,
        headers: { "If-Match": formatIfMatch(options.version) },
        signal: options.signal,
        validate: parseProjectResponse,
      });
    },

    listMembers(
      projectId: string,
      options: MemberListOptions = {},
    ): Promise<CursorPage<ProjectMember>> {
      return client.get(`${projectPath(projectId)}/members`, {
        signal: options.signal,
        query: {
          cursor: options.cursor,
          limit: options.limit ?? MAX_PAGE_LIMIT,
        },
        validate: (value) => parseProjectMemberPage(value, projectId),
      });
    },

    addMember(
      projectId: string,
      input: { user_id: string; role: ProjectRole },
      options: { signal?: AbortSignal } = {},
    ): Promise<ProjectMember> {
      return client.request(`${projectPath(projectId)}/members`, {
        method: "POST",
        body: input,
        signal: options.signal,
        validate: parseProjectMemberResponse,
      });
    },

    changeMemberRole(
      projectId: string,
      userId: string,
      role: ProjectRole,
      options: { signal?: AbortSignal } = {},
    ): Promise<ProjectMember> {
      return client.request(memberPath(projectId, userId), {
        method: "PATCH",
        body: { role },
        signal: options.signal,
        validate: parseProjectMemberResponse,
      });
    },

    removeMember(
      projectId: string,
      userId: string,
      options: { signal?: AbortSignal } = {},
    ): Promise<void> {
      return client.request<void>(memberPath(projectId, userId), {
        method: "DELETE",
        signal: options.signal,
      });
    },
  };
}

export const projectManagementApi = createProjectManagementApi();
