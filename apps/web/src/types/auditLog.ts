/**
 * 审计日志领域类型。
 *
 * 契约：后端 `GET /projects/{project_id}/audit-logs` 与
 * `GET /projects/{project_id}/audit-logs/{resource_type}/{resource_id}`。
 * `packages/contracts` 尚未声明这两个路径（按任务要求不改契约），
 * 缺口已在交付报告登记。
 *
 * 前端只读：审计日志没有创建/修改/删除入口，页面也不得画这些按钮——
 * 审计可追溯的前提是记录不可篡改。
 */

/** 与后端 `AuditResult` / 表 CHECK 约束 `ck_audit_logs_result_allowed` 同源。 */
export const AUDIT_RESULT_VALUES = ["SUCCESS", "FAILURE"] as const;

export type AuditResult = (typeof AUDIT_RESULT_VALUES)[number];

export interface AuditLog {
  audit_id: string;
  project_id: string;
  actor_user_id: string;
  actor_role: string | null;
  resource_type: string;
  resource_id: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  reason: string | null;
  request_id: string | null;
  result: AuditResult;
  correlation_id: string | null;
  details: Record<string, unknown>;
  occurred_at: string;
}

/**
 * 过滤条件。
 *
 * - `resource_type` / `action` 不做前端枚举：取值由各业务用例写入，
 *   历史数据大小写不统一，前端硬编码下拉会与真实数据脱节；
 * - 时间区间为闭区间，必须带时区（服务端拒绝 naive 时间，直接 422）。
 */
export interface AuditLogListFilters {
  resource_type?: string;
  resource_id?: string;
  action?: string;
  actor_user_id?: string;
  result?: AuditResult;
  occurred_from?: string;
  occurred_to?: string;
}
