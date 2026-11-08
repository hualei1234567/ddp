import type {
  EpisodeAction,
  EpisodeActionInput,
  EpisodeQualityStatus,
} from "../types/collection";
import { EPISODE_QUALITY_STATUS_VALUES } from "../types/collection";
import { ApiError } from "./api";

export interface EpisodeActionOption {
  action: EpisodeAction;
  label: string;
  /** 仅做表单提示；动作是否可执行以服务端状态机与权限为准。 */
  hint: string;
}

/**
 * 服务端公开的 Episode 动作（`EpisodeAction` Literal）。
 * 前端不根据 status 推断哪些动作可用，只提供表单入口，由服务端裁决。
 */
export const EPISODE_ACTION_OPTIONS: EpisodeActionOption[] = [
  { action: "ready", label: "标记就绪", hint: "提交 Manifest 与帧数，READY 后数据不可变。" },
  { action: "quality-check", label: "发起质检", hint: "请求服务端进入质量检查。" },
  { action: "quality-result", label: "记录质检结果", hint: "提交 PASS/WARN/FAIL。" },
  { action: "accept", label: "验收通过", hint: "服务端校验状态机后生效。" },
  { action: "reject", label: "驳回", hint: "建议填写原因。" },
  { action: "complete", label: "完成", hint: "服务端校验终态条件。" },
  { action: "invalidate", label: "标记失效", hint: "失效不删除历史，需填写原因。" },
];

export const QUALITY_STATUS_OPTIONS = EPISODE_QUALITY_STATUS_VALUES.filter(
  (value) => value !== "NOT_CHECKED",
).map((value) => ({
  value,
  label:
    value === "PASS" ? "通过 PASS" : value === "WARN" ? "警告 WARN" : "不通过 FAIL",
}));

export interface EpisodeActionFormValues {
  reason: string;
  qualityStatus: EpisodeQualityStatus | "";
  manifestText: string;
  frameCount: string;
}

export const EMPTY_EPISODE_ACTION_FORM: EpisodeActionFormValues = {
  reason: "",
  qualityStatus: "",
  manifestText: "",
  frameCount: "",
};

export interface EpisodeActionBuildResult {
  input: EpisodeActionInput | null;
  error: string | null;
}

export interface EpisodeActionFeedback {
  kind: "success" | "error" | "warning" | "info";
  title: string;
  description: string;
}

function parseManifest(text: string): {
  manifest: Record<string, unknown> | null;
  error: string | null;
} {
  if (text.trim() === "") {
    return { manifest: null, error: "ready 动作必须填写 Manifest JSON。" };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { manifest: null, error: "Manifest 不是合法 JSON。" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { manifest: null, error: "Manifest 必须是 JSON 对象。" };
  }
  return { manifest: parsed as Record<string, unknown>, error: null };
}

function parseFrameCount(value: string): {
  frameCount: number | null;
  error: string | null;
} {
  const trimmed = value.trim();
  if (trimmed === "" || !/^\d+$/.test(trimmed)) {
    return { frameCount: null, error: "帧数必须是非负整数。" };
  }
  const frameCount = Number(trimmed);
  if (!Number.isSafeInteger(frameCount) || frameCount < 0) {
    return { frameCount: null, error: "帧数必须是非负整数。" };
  }
  return { frameCount, error: null };
}

/** 把表单转成服务端 `EpisodeActionRequest`；无效输入不进网络。 */
export function buildEpisodeActionInput(
  action: EpisodeAction,
  values: EpisodeActionFormValues,
): EpisodeActionBuildResult {
  const reason = values.reason.trim() === "" ? null : values.reason.trim();

  if (action === "ready") {
    const manifestResult = parseManifest(values.manifestText);
    if (manifestResult.error || manifestResult.manifest === null) {
      return { input: null, error: manifestResult.error };
    }
    const frameResult = parseFrameCount(values.frameCount);
    if (frameResult.error || frameResult.frameCount === null) {
      return { input: null, error: frameResult.error };
    }
    return {
      input: {
        manifest: manifestResult.manifest,
        frame_count: frameResult.frameCount,
      },
      error: null,
    };
  }

  if (action === "quality-result") {
    if (values.qualityStatus === "") {
      return { input: null, error: "记录质检结果必须选择 PASS/WARN/FAIL。" };
    }
    return {
      input: { reason, quality_status: values.qualityStatus },
      error: null,
    };
  }

  return { input: { reason }, error: null };
}

/** 服务端拒绝动作时给出可操作提示；不把失败当成功。 */
export function describeEpisodeWriteFailure(
  caught: unknown,
  title: string,
): EpisodeActionFeedback {
  if (caught instanceof ApiError) {
    if (caught.isForbidden || caught.isUnauthorized) {
      return {
        kind: "error",
        title: "无权执行该操作",
        description: `权限以服务端为准；请切换有权限的账号后重试。错误码：${caught.code ?? "forbidden"}`,
      };
    }
    if (caught.isNotFound) {
      return {
        kind: "error",
        title: "Episode 不存在或无权操作",
        description: "服务端未找到该 Episode；跨项目资源统一不可见。",
      };
    }
    if (caught.status === 428 || caught.code === "precondition_required") {
      return {
        kind: "warning",
        title: "缺少版本前置条件",
        description: "请刷新详情获取最新 version 后重试，避免覆盖他人修改。",
      };
    }
    if (caught.isConflict) {
      return {
        kind: "warning",
        title: "版本或状态冲突",
        description: `服务端拒绝本次写入；请刷新详情后重试。错误码：${caught.code ?? "conflict"}`,
      };
    }
    if (caught.status === 422) {
      return {
        kind: "error",
        title: "请求未通过服务端校验",
        description: `错误码：${caught.code ?? "validation_failed"}；请根据提示修正输入或联系管理员。`,
      };
    }
    if (caught.kind === "network" || caught.kind === "timeout") {
      return {
        kind: "error",
        title: "网络或服务异常，可重试",
        description: "写操作未确认成功；请刷新详情后确认当前状态，再决定是否重试。",
      };
    }
    return {
      kind: "error",
      title,
      description: `错误码：${caught.code ?? "unknown"}；request_id：${caught.requestId ?? "—"}`,
    };
  }
  return {
    kind: "error",
    title,
    description: "发生未识别错误；请刷新详情后确认状态，不要重复盲写。",
  };
}
