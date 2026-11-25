import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PreferencePairItemPayload } from "../../../types/llmAnnotation";
import PreferencePairPanel from "./PreferencePairPanel";

const PAYLOAD: PreferencePairItemPayload = {
  prompt: "总结这段话",
  chosen: "回答 A 内容",
  rejected: "回答 B 内容",
  criteria: ["helpfulness", "harmlessness"],
  annotator_rationale: null,
};

function renderPanel(
  overrides: {
    payload?: PreferencePairItemPayload;
    value?: Parameters<typeof PreferencePairPanel>[0]["value"];
  } = {},
): string {
  return renderToStaticMarkup(
    <PreferencePairPanel
      payload={overrides.payload ?? PAYLOAD}
      value={overrides.value ?? null}
    />,
  );
}

describe("PreferencePairPanel 偏好对比展示", () => {
  it("A/B 双栏对比 + criteria + winner 三个选项", () => {
    const html = renderPanel();
    expect(html).toContain("回答 A（chosen）");
    expect(html).toContain("回答 B（rejected）");
    expect(html).toContain("回答 A 内容");
    expect(html).toContain("回答 B 内容");
    expect(html).toContain("helpfulness、harmlessness");
    for (const option of ["A", "B", "TIE"]) {
      expect(html).toContain(option);
    }
  });

  it("受控值高亮当前 winner", () => {
    const html = renderPanel({
      value: { winner: "B", confidence: 0.9, rationale: "B 更完整" },
    });
    expect(html).toContain("0.9");
    expect(html).toContain("B 更完整");
  });

  it("criteria 未声明时如实展示占位", () => {
    const html = renderPanel({
      payload: { ...PAYLOAD, criteria: [] },
    });
    expect(html).toContain("未声明");
  });
});
