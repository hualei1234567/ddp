import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { auditFixture } from "../../services/__fixtures__/auditLogFixtures";
import AuditLogDetailContent from "./AuditLogDetailContent";

function renderDetail(value = auditFixture()): string {
  return renderToStaticMarkup(
    <AuditLogDetailContent item={value} onFocusResource={() => undefined} />,
  );
}

describe("审计详情", () => {
  it("展示留痕的全部关键字段", () => {
    const html = renderDetail();

    expect(html).toContain("RAW_ASSET");
    expect(html).toContain("validate");
    expect(html).toContain("UPLOADED");
    expect(html).toContain("VALID");
    expect(html).toContain("校验通过");
    expect(html).toContain("req-audit-1");
    expect(html).toContain("owner");
  });

  it("details 按原样展示，不解释未知键", () => {
    const html = renderDetail();

    expect(html).toContain("details");
    expect(html).toContain("checksum");
    expect(html).toContain("sha256:abc");
    expect(html).toContain("frames");
  });

  it("只提供「只看该资源」，没有修改或删除入口", () => {
    const html = renderDetail();

    expect(html).toContain("只看该资源");
    expect(html).not.toContain("删除");
    expect(html).not.toContain("修改");
  });

  it("空原因与空关联 ID 显示占位符而不是空白", () => {
    const html = renderDetail(
      auditFixture({ reason: null, correlation_id: null }),
    );

    expect(html).toContain("—");
  });
});
