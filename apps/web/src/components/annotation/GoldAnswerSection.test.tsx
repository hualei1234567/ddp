import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import { goldSetFixture } from "../../services/__fixtures__/annotationFixtures";
import GoldAnswerSection from "./GoldAnswerSection";

function render(
  overrides: Partial<Parameters<typeof GoldAnswerSection>[0]> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <GoldAnswerSection
        answers={null}
        error={null}
        loading={false}
        onLoad={() => undefined}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

describe("黄金集答案访问区", () => {
  it("默认不展示期望答案", () => {
    const html = render();
    expect(html).toContain("显式查看期望答案");
    expect(html).toContain("默认脱敏");
    expect(html).not.toContain("PICK");
  });

  it("403 gold_set_access_denied 显示 Forbidden 与错误码，不泄漏答案", () => {
    const html = render({
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "gold_set_access_denied",
        message: "无权查看黄金集期望答案",
        requestId: "req-gold",
      }),
    });
    expect(html).toContain("黄金集期望答案不可见");
    expect(html).toContain("gold_set_access_denied");
    expect(html).toContain("req-gold");
    expect(html).not.toContain("PICK");
  });

  it("授权成功后才展示 expected_labels", () => {
    const html = render({
      answers: {
        ...goldSetFixture,
        items: [
          {
            ...goldSetFixture.items[0]!,
            answers_redacted: false,
            expected_labels: [{ label_key: "action", value: "PICK" }],
          },
        ],
      },
    });
    expect(html).toContain("已授权答案视图");
    expect(html).toContain("PICK");
  });

  it("非 403 错误展示普通 Error 与重试", () => {
    const html = render({
      error: new ApiError({
        kind: "http",
        status: 500,
        code: "internal_error",
        message: "内部错误",
      }),
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
  });
});
