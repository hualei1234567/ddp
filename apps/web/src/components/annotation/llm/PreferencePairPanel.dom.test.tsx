import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PreferencePairItemPayload } from "../../../types/llmAnnotation";
import DialogueTurnList from "./DialogueTurnList";
import PreferencePairPanel from "./PreferencePairPanel";

const PREF_PAYLOAD: PreferencePairItemPayload = {
  prompt: "总结要点",
  chosen: "A 回答全文",
  rejected: "B 回答全文",
  criteria: ["helpfulness"],
  annotator_rationale: null,
};

describe("LLM 展示组件交互（jsdom）", () => {
  it("PreferencePairPanel：选择 winner/TIE 并回传 JSON 约定值", async () => {
    const onChange = vi.fn();
    render(
      <PreferencePairPanel payload={PREF_PAYLOAD} value={null} onChange={onChange} />,
    );

    await userEvent.click(screen.getByTestId("llm-pref-winner-A"));
    expect(onChange).toHaveBeenLastCalledWith({
      winner: "A",
      confidence: null,
      rationale: null,
    });

    await userEvent.click(screen.getByTestId("llm-pref-winner-TIE"));
    expect(onChange).toHaveBeenLastCalledWith({
      winner: "TIE",
      confidence: null,
      rationale: null,
    });
  });

  it("PreferencePairPanel：rationale 输入进入约定值", async () => {
    const onChange = vi.fn();
    render(
      <PreferencePairPanel payload={PREF_PAYLOAD} value={null} onChange={onChange} />,
    );
    await userEvent.click(screen.getByTestId("llm-pref-winner-B"));
    await userEvent.type(screen.getByTestId("llm-pref-rationale"), "B 更准确");
    const calls = onChange.mock.calls as Array<
      [{ winner: string; confidence: number | null; rationale: string | null } | null]
    >;
    const last = calls[calls.length - 1]?.[0];
    expect(last).not.toBeNull();
    expect(last?.winner).toBe("B");
    expect(last?.rationale).toContain("B 更准确");
  });

  it("PreferencePairPanel：winner 未选时回传 null（不算有效结论）", async () => {
    const onChange = vi.fn();
    render(
      <PreferencePairPanel payload={PREF_PAYLOAD} value={null} onChange={onChange} />,
    );
    await userEvent.type(screen.getByTestId("llm-pref-rationale"), "还没选");
    const calls = onChange.mock.calls as Array<[unknown]>;
    expect(calls[calls.length - 1]?.[0]).toBeNull();
  });

  it("DialogueTurnList：长输出默认折叠，点击展开后可见全文", async () => {
    const longContent = "很长的输出".repeat(60);
    render(
      <DialogueTurnList
        turns={[{ role: "assistant", content: longContent, ts: null }]}
      />,
    );
    expect(screen.getByTestId("llm-dialogue-content-0").textContent).not.toContain(
      longContent,
    );
    await userEvent.click(screen.getByTestId("llm-dialogue-toggle-0"));
    expect(screen.getByTestId("llm-dialogue-content-0").textContent).toContain(
      longContent,
    );
    // 再点一次收起
    await userEvent.click(screen.getByTestId("llm-dialogue-toggle-0"));
    expect(
      screen.getByTestId("llm-dialogue-content-0").textContent,
    ).not.toContain(longContent);
  });
});
