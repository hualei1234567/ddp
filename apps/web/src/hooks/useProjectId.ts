import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import {
  readStoredProjectId,
  resolveProjectId,
  storeProjectId,
} from "../services/project";

/**
 * 当前项目上下文。来源优先级与校验规则见 `services/project.ts`；
 * 页面拿到的只是待服务端校验的路径参数，不是前端权限依据。
 */
export function useProjectId(): string | null {
  const [searchParams] = useSearchParams();
  const queryProjectId = searchParams.get("project_id");

  const projectId = useMemo(
    () =>
      resolveProjectId({
        query: queryProjectId,
        stored: readStoredProjectId(),
        env: import.meta.env.VITE_PROJECT_ID,
      }),
    [queryProjectId],
  );

  useEffect(() => {
    if (projectId) {
      storeProjectId(projectId);
    }
  }, [projectId]);

  return projectId;
}
