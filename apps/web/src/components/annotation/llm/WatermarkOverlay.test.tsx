import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import WatermarkOverlay from "./WatermarkOverlay";

describe("WatermarkOverlay 水印覆盖层", () => {
  it("渲染操作员 ID 与时间戳，pointer-events:none 且 aria-hidden", () => {
    const html = renderToStaticMarkup(
      <WatermarkOverlay operatorId="op-001" timestamp="2026-11-26T00:00:00Z" />,
    );
    expect(html).toContain("pointer-events:none");
    expect(html).toContain("aria-hidden");
    expect(html).toContain("op-001");
    expect(html).toContain("2026-11-26T00%3A00%3A00Z");
    expect(html).toContain("repeat");
  });

  it("特殊字符操作员 ID 不破坏 data URL（整体 URI 编码）", () => {
    const html = renderToStaticMarkup(
      <WatermarkOverlay
        operatorId={'<user>"#1'}
        timestamp="2026-11-26T00:00:00Z"
      />,
    );
    // 原始尖括号/引号必须被编码后才能进入 style 属性
    expect(html).not.toContain("<svg");
    expect(html).toContain("data:image/svg+xml");
  });
});
