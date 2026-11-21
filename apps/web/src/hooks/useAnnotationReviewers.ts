/**
 * 复核 / 仲裁的候选人来源：当前认证主体 + 项目成员。
 *
 * 为什么放在一起取：复核人默认是"当前用户自己"（复核人去队列里领取），
 * 双人复核或改派时才需要选其他成员。两者任一失败都只降级成手填 UUID，
 * 不能因此让整个提交详情页不可用；降级状态要如实告诉用户。
 */

import { useCallback } from "react";

import { fetchAllCursorPages } from "../services/cursorPage";
import { identityApi } from "../services/identity";
import { projectManagementApi } from "../services/projectManagement";
import type { ProjectMember } from "../types/project";
import { useRequest } from "./useRequest";

export interface AnnotationReviewers {
  /** 服务端认证的当前用户；失败或无身份时为 null（不猜测）。 */
  currentUserId: string | null;
  members: ProjectMember[];
  loading: boolean;
  /** 身份或成员列表读取失败：界面退回手填 UUID 并显式提示。 */
  degraded: boolean;
  reload: () => void;
}

interface ReviewerBundle {
  currentUserId: string;
  members: ProjectMember[];
}

export function useAnnotationReviewers(projectId: string): AnnotationReviewers {
  const load = useCallback(
    async (signal: AbortSignal): Promise<ReviewerBundle> => {
      const [me, members] = await Promise.all([
        identityApi.getCurrentUser({ signal }),
        fetchAllCursorPages<ProjectMember>(
          ({ cursor, limit }) =>
            projectManagementApi.listMembers(projectId, {
              cursor,
              limit,
              signal,
            }),
          (member) => member.user_id,
          { maxPages: 5 },
        ),
      ]);
      return { currentUserId: me.user_id, members };
    },
    [projectId],
  );
  const request = useRequest(load);
  return {
    currentUserId: request.data?.currentUserId ?? null,
    members: request.data?.members ?? [],
    loading: request.loading,
    degraded: request.error !== null,
    reload: request.reload,
  };
}
