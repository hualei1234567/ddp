import { ApiError } from "./api";

export interface TaskActionFeedback {
  kind: "success" | "warning" | "error";
  title: string;
  description: string;
}

export function describeTaskActionError(caught: unknown): TaskActionFeedback {
  const error =
    caught instanceof ApiError
      ? caught
      : new ApiError({
          kind: "network",
          message: "动作请求失败，请检查网络或服务状态。",
          cause: caught,
        });
  if (error.isForbidden) {
    return {
      kind: "error",
      title: "无权执行该动作",
      description: `${error.message}；错误码：${error.code ?? "forbidden"}，request_id：${error.requestId ?? "—"}。权限以服务端为准。`,
    };
  }
  if (
    error.isConflict ||
    error.code === "precondition_required" ||
    error.code === "state_transition_invalid"
  ) {
    return {
      kind: "warning",
      title: "任务已被更新或当前状态不允许该动作",
      description: `${error.message}；错误码：${error.code ?? "conflict"}，request_id：${error.requestId ?? "—"}。请刷新后基于最新版本重新操作，页面不会覆盖他人修改；本地检查草稿保留。`,
    };
  }
  return {
    kind: "error",
    title: "动作失败",
    description: `${error.message}；错误码：${error.code ?? "unknown"}，request_id：${error.requestId ?? "—"}。`,
  };
}

export function taskActionSuccess(message: string): TaskActionFeedback {
  return {
    kind: "success",
    title: "动作已提交",
    description: `${message} 正在刷新服务端最新状态与可执行动作。`,
  };
}
