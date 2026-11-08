import { useCallback } from "react";

import { ApiError } from "../services/api";
import { annotationApi } from "../services/annotation";
import {
  loadAnnotationWorkbench,
  type WorkbenchData,
  type WorkbenchPartialError,
} from "../services/annotationWorkbench";
import { collectionsApi } from "../services/collections";
import { useRequest, type RequestState } from "./useRequest";

export interface WorkbenchRequestResult {
  data: WorkbenchData;
  partialErrors: WorkbenchPartialError[];
}

export interface UseAnnotationWorkbenchResult
  extends Omit<RequestState<WorkbenchRequestResult>, "data"> {
  data: WorkbenchRequestResult | null;
  reload: () => void;
}

/**
 * 工作台数据 Hook：必需资源失败进入 Error/Stale；可选资源失败保留主体并
 * 通过 `partialErrors` 展示 Partial。页面不得自行拼接请求。
 */
export function useAnnotationWorkbench(
  projectId: string,
  assignmentId: string,
): UseAnnotationWorkbenchResult {
  const request = useCallback(
    async (signal: AbortSignal): Promise<WorkbenchRequestResult> => {
      const result = await loadAnnotationWorkbench(
        annotationApi,
        collectionsApi,
        projectId,
        assignmentId,
        signal,
      );
      if (result.error !== null) {
        throw result.error;
      }
      if (result.data === null) {
        throw new ApiError({
          kind: "network",
          message: "工作台数据缺失，请重试。",
        });
      }
      return { data: result.data, partialErrors: result.partialErrors };
    },
    [projectId, assignmentId],
  );
  return useRequest(request);
}
