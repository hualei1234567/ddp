import type { StatusPresentationLike } from "../components/ui/StatusTag";

/** 审计结果展示：与后端 CHECK 约束的两个取值一一对应。 */
export function presentAuditResult(value: string): StatusPresentationLike {
  switch (value) {
    case "SUCCESS":
      return { label: "成功", color: "green" };
    case "FAILURE":
      return { label: "失败", color: "red" };
    default:
      // 解析器已拒绝未知结果；这里保留原值兜底，不把未知值说成成功。
      return { label: value, color: "default" };
  }
}

/** 操作者：角色 + ID；ID 在窄屏下由表格列做缩略展示。 */
export function formatActor(
  actorRole: string | null,
  actorUserId: string,
): string {
  return actorRole ? `${actorRole} · ${actorUserId}` : actorUserId;
}
