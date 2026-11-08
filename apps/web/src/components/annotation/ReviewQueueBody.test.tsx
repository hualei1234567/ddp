import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  annotationReviewFixture,
  annotationSubmissionFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/annotationFixtures";
import type { ReviewQueueResult } from "../../services/annotationQueues";
import ReviewQueueBody from "./ReviewQueueBody";

function render(
  data: ReviewQueueResult | null,
  overrides: Partial<Parameters<typeof ReviewQueueBody>[0]> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ReviewQueueBody
        projectId={PROJECT_ID}
        phase="ready"
        data={data}
        error={null}
        stale={false}
        partialErrors={[]}
        onRetry={() => undefined}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

const queue: ReviewQueueResult = {
  items: [
    {
      submission: annotationSubmissionFixture,
      reviews: [annotationReviewFixture],
    },
  ],
  partialErrors: [],
};

describe("复核队列状态", () => {
  it("展示提交与复核轮次、状态和入口", () => {
    const html = render(queue);
    expect(html).toContain("复核中");
    expect(html).toContain("第 1 轮");
    expect(html).toContain("查看 / 复核");
    expect(html).toContain("复核人 ID");
  });

  it("403 时只显示 Forbidden，不泄漏提交内容", () => {
    const html = render(null, {
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权访问",
        requestId: "req-403",
      }),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain(annotationSubmissionFixture.submission_id);
  });

  it("Empty 与 Partial 状态明确", () => {
    expect(render({ items: [], partialErrors: [] })).toContain("暂无待复核提交");
    const partial = render(queue, {
      partialErrors: [
        {
          scope: "提交 A",
          error: new ApiError({
            kind: "http",
            status: 500,
            code: "internal_error",
            message: "失败",
          }),
        },
      ],
    });
    expect(partial).toContain("部分提交的复核记录加载失败");
    expect(partial).toContain("internal_error");
  });

  it("Stale 状态提示上次成功数据", () => {
    const html = render(queue, { stale: true });
    expect(html).toContain("数据可能已过期");
  });
});
