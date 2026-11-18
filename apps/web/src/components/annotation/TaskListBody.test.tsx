import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  annotationTaskFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/annotationFixtures";
import type { AnnotationTask } from "../../types/annotation";
import TaskListBody from "./TaskListBody";
import type { TaskListBodyProps } from "./TaskListBody";

const baseProps: TaskListBodyProps = {
  projectId: PROJECT_ID,
  items: [annotationTaskFixture],
  loading: false,
  refreshing: false,
  error: null,
  stale: false,
  isMobile: false,
  hasMore: false,
  loadingMore: false,
  loadMoreError: null,
  onRetry: () => undefined,
  onLoadMore: () => undefined,
};

function renderList(
  items: AnnotationTask[],
  overrides: Partial<TaskListBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <TaskListBody {...baseProps} items={items} {...overrides} />
    </MemoryRouter>,
  );
}

describe("标注任务列表 SLA 标记", () => {
  it("逾期任务渲染红色标记与超时时长，并汇总逾期任务数", () => {
    const overdue: AnnotationTask = {
      ...annotationTaskFixture,
      due_at: "2000-01-01T00:00:00Z",
      is_overdue: true,
    };
    const html = renderList([overdue]);

    expect(html).toContain("已逾期");
    expect(html).toContain("已超时");
    expect(html).toContain("逾期任务数");
    expect(html).toContain("red");
  });

  it("未逾期任务展示剩余时长，不渲染逾期标记", () => {
    const future: AnnotationTask = {
      ...annotationTaskFixture,
      due_at: "2999-01-01T00:00:00Z",
      is_overdue: false,
    };
    const html = renderList([future]);

    expect(html).toContain("剩余");
    expect(html).not.toContain("已逾期");
    expect(html).toContain("逾期任务数");
  });

  it("移动端卡片同样渲染逾期标记", () => {
    const overdue: AnnotationTask = {
      ...annotationTaskFixture,
      due_at: "2000-01-01T00:00:00Z",
      is_overdue: true,
    };
    const html = renderList([overdue], { isMobile: true });

    expect(html).toContain("已逾期");
    expect(html).toContain("已超时");
  });
});
