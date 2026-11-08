import type { RawAssetStatus } from "../types/collection";
import { ApiError } from "./api";

export interface DownloadFeedback {
  kind: "error" | "warning" | "info";
  title: string;
  description: string;
}

/** 服务端下载用例只允许 VALID/ARCHIVED；其余状态按钮保持禁用并说明原因。 */
export function isDownloadableStatus(status: RawAssetStatus): boolean {
  return status === "VALID" || status === "ARCHIVED";
}

/** 签名链接有效期判断；留 1 秒余量，避免边界上打开已过期 URL。 */
export function isDownloadLinkExpired(
  expiresAt: number,
  now: number = Date.now(),
): boolean {
  return !Number.isFinite(expiresAt) || expiresAt <= now + 1000;
}

/** URL 获取错误按 403/404/409/410/422/网络分类，不默认成功。 */
export function describeDownloadFailure(caught: unknown): DownloadFeedback {
  if (caught instanceof ApiError) {
    if (caught.isForbidden || caught.isUnauthorized) {
      return {
        kind: "error",
        title: "无权获取下载地址",
        description: `权限以服务端为准；请切换有下载权限的账号后重试。错误码：${caught.code ?? "forbidden"}`,
      };
    }
    if (caught.isNotFound) {
      return {
        kind: "error",
        title: "资产不存在或无权查看",
        description: "服务端未找到该资产；跨项目资源统一不可见，不泄漏资源存在性。",
      };
    }
    if (caught.isConflict) {
      return {
        kind: "warning",
        title: "资产状态或版本冲突",
        description: `资产可能已被其他操作变更；请刷新详情后再试。错误码：${caught.code ?? "conflict"}`,
      };
    }
    if (caught.isGone || caught.code === "asset_tombstoned") {
      return {
        kind: "error",
        title: "资产已墓碑化，不可下载",
        description: "墓碑资产只保留元数据与血缘；下载被服务端拒绝。",
      };
    }
    if (caught.code === "asset_not_usable") {
      return {
        kind: "warning",
        title: "资产当前不可下载",
        description:
          "资产可能正在校验、被隔离或已失效；请等待服务端校验通过或联系数据管理员。",
      };
    }
    if (
      caught.kind === "network" ||
      caught.kind === "timeout" ||
      caught.status === 429 ||
      (caught.status ?? 0) >= 500
    ) {
      return {
        kind: "error",
        title: "下载地址获取失败，可重试",
        description: `网络或服务暂时异常。错误码：${caught.code ?? "network"}`,
      };
    }
    if (caught.kind === "invalid_response") {
      return {
        kind: "error",
        title: "下载地址响应不符合契约",
        description: "服务端返回的数据未能通过前端校验，已阻止打开；请联系支持。",
      };
    }
    return {
      kind: "error",
      title: "未能获取下载地址",
      description: `错误码：${caught.code ?? "unknown"}；request_id：${caught.requestId ?? "—"}`,
    };
  }

  return {
    kind: "error",
    title: "未能获取下载地址",
    description: "发生未识别错误，请刷新后重试；不要重复盲点。",
  };
}

export function describeExpiredDownloadLink(): DownloadFeedback {
  return {
    kind: "warning",
    title: "下载链接已过期",
    description: "短期签名地址已过期，未打开；请重新点击下载以获取新地址。",
  };
}
