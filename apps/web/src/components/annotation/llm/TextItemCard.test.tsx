import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { TextItemPayload } from "../../../types/llmAnnotation";
import TextItemCard from "./TextItemCard";

describe("TextItemCard 文本展示", () => {
  it("展示正文、来源与语言标签", () => {
    const payload: TextItemPayload = {
      text: "第一行\n第二行",
      source_ref: "doc-42",
      language: "zh",
      metadata: { page: 7 },
    };
    const html = renderToStaticMarkup(<TextItemCard payload={payload} />);
    expect(html).toContain("文本对象（TEXT）");
    expect(html).toContain("第一行");
    expect(html).toContain("doc-42");
    expect(html).toContain("zh");
    expect(html).toContain("page");
  });

  it("来源/语言缺失时展示占位，不伪造数据", () => {
    const payload: TextItemPayload = {
      text: "只有正文",
      source_ref: null,
      language: null,
      metadata: null,
    };
    const html = renderToStaticMarkup(<TextItemCard payload={payload} />);
    expect(html).toContain("只有正文");
    expect(html).toContain("—");
  });
});
