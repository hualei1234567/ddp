import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { GapSummary } from "../../types/modelops";
import { DataGapBody } from "./DataGapPage";
import type { DataGapBodyProps } from "./DataGapPage";

/** AntD 会在中文与计数之间插入空白，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function summaryFixture(overrides: Partial<GapSummary> = {}): GapSummary {
  return {
    items: [
      {
        dimension_type: "SCENE",
        dimension_value: "kitchen",
        open_feedback_count: 1,
        current_count: 2,
        target_count: 10,
        suggested_episode_count: 8,
        requirement_ids: [],
        sources: [
          { source: "FEEDBACK", count: 2 },
          { source: "FAILURE_CASE", count: 3 },
        ],
      },
    ],
    freshness: "FRESH",
    unmapped: [],
    truncated_sources: [],
    ...overrides,
  };
}

function renderBody(
  data: GapSummary | null,
  overrides: Partial<DataGapBodyProps> = {},
): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <DataGapBody
          data={data}
          loading={false}
          stale={false}
          error={null}
          onRetry={() => undefined}
          {...overrides}
        />
      </MemoryRouter>,
    ),
  );
}

describe("数据缺口来源证据呈现", () => {
  it("每个缺口维度展示来源徽标与计数", () => {
    const html = renderBody(summaryFixture());

    expect(html).toContain("人工反馈×2");
    expect(html).toContain("评测失败用例×3");
  });

  it("没有来源证据时说明「不代表缺口为 0」，绝不渲染成 0 缺口", () => {
    const data = summaryFixture({
      items: [
        {
          dimension_type: "SCENE",
          dimension_value: "kitchen",
          open_feedback_count: 1,
          current_count: 2,
          target_count: 10,
          suggested_episode_count: 8,
          requirement_ids: [],
          sources: [],
        },
      ],
    });
    const html = renderBody(data);

    expect(html).toContain("本次扫描无来源证据");
    expect(html).toContain("不代表缺口为0");
    expect(html).not.toContain("缺口0");
    expect(html).not.toContain("0缺口");
  });

  it("展示未映射证据的来源、条数与可读原因", () => {
    const data = summaryFixture({
      unmapped: [
        { source: "LOW_SCORE", count: 4, reason: "NO_DIMENSION" },
      ],
    });
    const html = renderBody(data);

    expect(html).toContain("未映射证据");
    expect(html).toContain("低分评测");
    expect(html).toContain("来源本身没有维度语义");
    expect(html).toContain("reason=NO_DIMENSION");
    expect(html).toContain("不会被静默丢弃");
  });

  it("截断来源明确提示「没有出现不代表没有证据」", () => {
    const data = summaryFixture({
      truncated_sources: ["LOW_SCORE", "ANNOTATION_CONFLICT"],
    });
    const html = renderBody(data);

    expect(html).toContain("来源证据可能不完整");
    expect(html).toContain("低分评测、标注分歧");
    expect(html).toContain("不代表没有证据");
  });

  it("未识别的来源与原因按原值展示", () => {
    const data = summaryFixture({
      items: [
        {
          dimension_type: "SCENE",
          dimension_value: "kitchen",
          open_feedback_count: 1,
          current_count: 2,
          target_count: 10,
          suggested_episode_count: 8,
          requirement_ids: [],
          sources: [{ source: "FUTURE_SOURCE", count: 1 }],
        },
      ],
      unmapped: [
        { source: "FUTURE_SOURCE", count: 2, reason: "FUTURE_REASON" },
      ],
    });
    const html = renderBody(data);

    expect(html).toContain("FUTURE_SOURCE");
    expect(html).toContain("FUTURE_REASON");
  });

  it("没有未映射与截断时不出渲染额外区块", () => {
    const html = renderBody(summaryFixture());

    expect(html).not.toContain("未映射证据");
    expect(html).not.toContain("来源证据可能不完整");
  });

  it("来源证据说明不参与覆盖与补采计算", () => {
    const html = renderBody(summaryFixture());

    expect(html).toContain("不参与");
    expect(html).toContain("建议补采计算");
  });
});
