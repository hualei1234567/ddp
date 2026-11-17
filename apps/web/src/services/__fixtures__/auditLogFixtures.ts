/** 审计日志测试数据（字段与后端 `AuditLogOut` 一一对应）。 */

import type { AuditLog } from "../../types/auditLog";

export const AUDIT_PROJECT_ID = "77777777-7777-4777-8777-777777777777";
export const OTHER_AUDIT_PROJECT_ID = "66666666-6666-4666-8666-666666666666";
export const AUDIT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const AUDIT_RESOURCE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const AUDIT_ACTOR_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

export function auditFixture(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    audit_id: AUDIT_ID,
    project_id: AUDIT_PROJECT_ID,
    actor_user_id: AUDIT_ACTOR_ID,
    actor_role: "owner",
    resource_type: "RAW_ASSET",
    resource_id: AUDIT_RESOURCE_ID,
    action: "validate",
    from_status: "UPLOADED",
    to_status: "VALID",
    reason: "校验通过",
    request_id: "req-audit-1",
    result: "SUCCESS",
    correlation_id: null,
    details: { checksum: "sha256:abc", frames: 120 },
    occurred_at: "2026-11-17T10:00:00Z",
    ...overrides,
  };
}
