import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { goldSetFixture } from "../../services/__fixtures__/annotationFixtures";
import GoldEvaluationPanel from "./GoldEvaluationPanel";

function renderPanel(
  overrides: Partial<Parameters<typeof GoldEvaluationPanel>[0]> = {},
): string {
  return renderToStaticMarkup(
    <GoldEvaluationPanel
      goldSet={goldSetFixture}
      evaluation={null}
      injection={null}
      error={null}
      onEvaluate={() => Promise.resolve()}
      onInject={() => Promise.resolve()}
      {...overrides}
    />,
  );
}

describe("黄金集评估与注入面板", () => {
  it("提供评估与注入两个真实动作入口", () => {
    const html = renderPanel();
    expect(html).toContain("执行评估");
    expect(html).toContain("执行注入");
    expect(html).toContain("用黄金集评估提交");
    expect(html).toContain("注入黄金项");
  });

  it("准确率为 null 时不渲染成 0%", () => {
    const html = renderPanel({
      evaluation: {
        evaluation_id: "11111111-1111-7111-8111-111111111111",
        project_id: "22222222-2222-7222-8222-222222222222",
        gold_set_id: goldSetFixture.gold_set_id,
        task_id: "33333333-3333-7333-8333-333333333333",
        submission_id: "44444444-4444-7444-8444-444444444444",
        evaluated_count: 0,
        passed_count: 0,
        accuracy: null,
        status: "INSUFFICIENT_DATA",
        sample_size: 0,
        results: [],
        computed_at: "2026-11-17T00:00:00Z",
      },
    });

    expect(html).toContain("无可评估项（不是 0%）");
    expect(html).not.toContain("0.0%");
  });

  it("后端错误原样展示", () => {
    const html = renderPanel({ error: "黄金集期望答案不完整" });
    expect(html).toContain("黄金集期望答案不完整");
  });
});
