/** 审计日志响应解析器。 */

import type { CursorPage } from "../types/api";
import { AUDIT_RESULT_VALUES, type AuditLog } from "../types/auditLog";
import {
  asRecord,
  parseCursorPage,
  readEnum,
  readOptionalRecord,
  readOptionalString,
  readString,
  readUuid,
} from "./validation";

export function parseAuditLog(value: unknown, path = "audit_log"): AuditLog {
  const record = asRecord(value, path);
  return {
    audit_id: readUuid(record, "audit_id", path),
    project_id: readUuid(record, "project_id", path),
    actor_user_id: readUuid(record, "actor_user_id", path),
    actor_role: readOptionalString(record, "actor_role", path),
    resource_type: readString(record, "resource_type", path),
    resource_id: readUuid(record, "resource_id", path),
    action: readString(record, "action", path),
    from_status: readOptionalString(record, "from_status", path),
    to_status: readOptionalString(record, "to_status", path),
    reason: readOptionalString(record, "reason", path),
    request_id: readOptionalString(record, "request_id", path),
    // 结果只有 SUCCESS / FAILURE；非法值直接拒绝，不当成「未知结果」透传。
    result: readEnum(record, "result", path, AUDIT_RESULT_VALUES),
    correlation_id: readOptionalString(record, "correlation_id", path),
    details: readOptionalRecord(record, "details", path) ?? {},
    occurred_at: readString(record, "occurred_at", path),
  };
}

export function parseAuditLogPage(value: unknown): CursorPage<AuditLog> {
  return parseCursorPage(value, "audit_logs", parseAuditLog);
}
