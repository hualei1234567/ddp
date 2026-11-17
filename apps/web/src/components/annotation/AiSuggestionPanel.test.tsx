import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AiSuggestion } from "../../services/annotationAiParsers";
import AiSuggestionPanel from "./AiSuggestionPanel";

const suggestion: AiSuggestion = {
  suggestion_id: "11111111-1111-7111-8111-111111111111",
  project_id: "22222222-2222-7222-8222-222222222222",
  annotation_id: "33333333-3333-7333-8333-333333333333",
  label_key: "action",
  suggested_value: { action: "pick" },
  model_id: "vla-annotator",
  model_version: "v3",
  prompt_version: "p7",
  confidence: 0.82,
  generated_at: "2026-11-17T00:00:00Z",
  human_confirmed: false,
  human_value: null,
  decided_by: null,
  decided_at: null,
  version: 1,
  created_at: null,
  updated_at: null,
  created_by: null,
  updated_by: null,
};

function renderPanel(
  suggestions: AiSuggestion[],
  agreement: Parameters<typeof AiSuggestionPanel>[0]["agreement"],
): string {
  return renderToStaticMarkup(
    <AiSuggestionPanel
      suggestions={suggestions}
      agreement={agreement}
      busySuggestionId={null}
      onAdopt={() => Promise.resolve()}
      onRevise={() => Promise.resolve()}
      onReload={() => undefined}
    />,
  );
}

describe("AI 建议面板", () => {
  it("展示建议、模型版本与采纳/修订入口", () => {
    const html = renderPanel([suggestion], { items: [] });

    expect(html).toContain("action");
    expect(html).toContain("vla-annotator");
    expect(html).toContain("v3");
    // AntD 在两个中文字符的按钮文本间插入空格（"采 纳"），断言前先去空白。
    expect(html.replace(/\s+/g, "")).toContain("采纳");
    expect(html.replace(/\s+/g, "")).toContain("修订");
    expect(html).toContain("82.0%");
  });

  it("没有建议时展示空态，不伪造数据", () => {
    const html = renderPanel([], { items: [] });
    expect(html.replace(/\s+/g, "")).toContain("暂无AI建议");
  });

  it("采纳率为 null 时明确写「无已决定样本」而不是 0%", () => {
    const html = renderPanel([suggestion], {
      items: [
        {
          model_id: "vla-annotator",
          model_version: "v3",
          total: 1,
          accepted: 0,
          revised: 0,
          pending: 1,
          decided: 0,
          acceptance_rate: null,
        },
      ],
    });

    expect(html).toContain("无已决定样本");
    expect(html).not.toContain("0.0%");
  });
});
