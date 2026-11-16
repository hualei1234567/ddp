import { describe, expect, it } from "vitest";

import { AUDIT_ID, AUDIT_PROJECT_ID, auditFixture } from "./__fixtures__/auditLogFixtures";
import { parseAuditLog, parseAuditLogPage } from "./auditLogParsers";
import { ContractValidationError } from "./validation";

describe("审计日志响应解析", () => {
  it("解析完整条目", () => {
    const parsed = parseAuditLog(auditFixture());

    expect(parsed.audit_id).toBe(AUDIT_ID);
    expect(parsed.project_id).toBe(AUDIT_PROJECT_ID);
    expect(parsed.result).toBe("SUCCESS");
    expect(parsed.details).toEqual({ checksum: "sha256:abc", frames: 120 });
    expect(parsed.occurred_at).toBe("2026-11-17T10:00:00Z");
  });

  it("details 允许为空对象", () => {
    const parsed = parseAuditLog(auditFixture({ details: {} }));
    expect(parsed.details).toEqual({});
  });

  it("拒绝未知结果，不把未知值当成成功", () => {
    expect(() =>
      parseAuditLog(auditFixture({ result: "MAYBE" as never })),
    ).toThrow(ContractValidationError);
  });

  it("拒绝非 UUID 的资源 ID", () => {
    expect(() =>
      parseAuditLog(auditFixture({ resource_id: "not-a-uuid" })),
    ).toThrow(ContractValidationError);
  });

  it("分页解析保留 next_cursor", () => {
    const page = parseAuditLogPage({
      items: [auditFixture()],
      next_cursor: "cursor-1",
    });

    expect(page.items).toHaveLength(1);
    expect(page.next_cursor).toBe("cursor-1");
  });
});
