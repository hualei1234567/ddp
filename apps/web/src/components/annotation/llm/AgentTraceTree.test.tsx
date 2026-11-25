import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentTraceItemPayload } from "../../../types/llmAnnotation";
import AgentTraceTree from "./AgentTraceTree";

const PAYLOAD: AgentTraceItemPayload = {
  steps: [
    { action: "点击图标", observation: "窗口出现", tool: "mouse", result: "ok" },
    { action: "调用不存在的工具", observation: null, tool: null, result: null },
  ],
  task_goal: "整理桌面文件",
  success: false,
};

describe("AgentTraceTree 轨迹展示", () => {
  it("渲染步骤、工具标签与四分类标签（含 reward 映射）", () => {
    const html = renderToStaticMarkup(<AgentTraceTree payload={PAYLOAD} />);
    expect(html).toContain("共 2 步");
    expect(html).toContain("任务目标：整理桌面文件");
    expect(html).toContain("点击图标");
    expect(html).toContain("mouse");
    expect(html).toContain("观察：窗口出现");
    for (const [label, reward] of [
      ["CORRECT", "+1"],
      ["REDUNDANT", "-0.5"],
      ["HALLUCINATED_TOOL", "-1"],
      ["FATAL", "-2"],
    ] as const) {
      expect(html).toContain(label);
      expect(html).toContain(reward);
    }
  });

  it("success 展示成功/失败，缺失时不渲染结果行", () => {
    const successHtml = renderToStaticMarkup(
      <AgentTraceTree payload={{ ...PAYLOAD, success: true }} />,
    );
    expect(successHtml).toContain("成功");
    const nullHtml = renderToStaticMarkup(
      <AgentTraceTree payload={{ ...PAYLOAD, success: null }} />,
    );
    expect(nullHtml).not.toContain("执行结果");
  });

  it("无 task_goal 时不渲染目标行", () => {
    const html = renderToStaticMarkup(
      <AgentTraceTree payload={{ ...PAYLOAD, task_goal: null }} />,
    );
    expect(html).not.toContain("任务目标");
  });
});
