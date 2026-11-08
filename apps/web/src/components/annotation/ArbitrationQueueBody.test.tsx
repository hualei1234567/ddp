import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  annotationArbitrationFixture,
  annotationSubmissionFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/annotationFixtures";
import type { ArbitrationQueueResult } from "../../services/annotationQueues";
import ArbitrationQueueBody from "./ArbitrationQueueBody";

function render(
  data: ArbitrationQueueResult | null,
  overrides: Partial<Parameters<typeof ArbitrationQueueBody>[0]> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ArbitrationQueueBody
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

const queue: ArbitrationQueueResult = {
  items: [
    {
      submission: annotationSubmissionFixture,
      arbitrations: [annotationArbitrationFixture],
    },
  ],
  partialErrors: [],
};

describe("仲裁队列状态", () => {
  it("展示仲裁状态、触发原因与投票进度", () => {
    const html = render(queue);
    expect(html).toContain("仲裁进行中");
    expect(html).toContain("REVIEWER_DISAGREEMENT");
    expect(html).toContain("投票 0/1");
    expect(html).toContain("查看 / 仲裁");
  });

  it("403 时只显示 Forbidden，不泄漏提交内容", () => {
    const html = render(null, {
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权访问",
      }),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain(annotationSubmissionFixture.submission_id);
  });

  it("Empty 提示无仲裁对象", () => {
    expect(render({ items: [], partialErrors: [] })).toContain("暂无仲裁对象");
  });
});
