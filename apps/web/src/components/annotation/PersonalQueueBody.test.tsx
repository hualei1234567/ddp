// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { AnnotationAssignment } from "../../types/annotation";
import { ApiError } from "../../services/api";
import type { PersonalQueue, QueueEntry } from "../../services/personalQueue";
import PersonalQueueBody from "./PersonalQueueBody";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

function entry(overrides: {
  status?: string;
  allowedActions?: readonly string[];
}): QueueEntry {
  const assignment = {
    assignment_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    project_id: PROJECT_ID,
    task_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    batch_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    assignee_id: "11111111-1111-1111-1111-111111111111",
    status: overrides.status ?? "ASSIGNED",
    assignment_role: "ANNOTATOR",
    planned_item_count: 10,
    claimed_item_count: 0,
    submitted_item_count: 0,
    approved_item_count: 0,
    planned_minutes: 30,
    actual_minutes: null,
    lease_expires_at: null,
    claimed_at: null,
    submitted_at: null,
    completed_at: null,
    revoked_reason: null,
    status_reason: null,
    version: 3,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
  } as AnnotationAssignment;
  return {
    projectId: PROJECT_ID,
    projectCode: "prj-a",
    projectName: "项目 A",
    assignment,
    allowedActions: overrides.allowedActions ?? [],
  };
}

function queue(overrides: Partial<PersonalQueue> = {}): PersonalQueue {
  return {
    open: [],
    submitted: [],
    failures: [],
    truncated: false,
    scannedProjects: 1,
    ...overrides,
  };
}

const noop = () => {};

function renderBody(props: Partial<Parameters<typeof PersonalQueueBody>[0]>) {
  return render(
    <MemoryRouter>
      <PersonalQueueBody
        phase="ready"
        data={queue()}
        error={null}
        stale={false}
        feedback={null}
        busyAssignmentId={null}
        onRetry={noop}
        onClaim={noop}
        buildWorkbenchPath={(projectId, assignmentId) =>
          `/annotation/workbench/${assignmentId}?project_id=${projectId}`
        }
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("PersonalQueueBody", () => {
  it("空队列显示 Empty，并说明已扫描项目", () => {
    renderBody({});
    expect(screen.getByText(/当前没有待办分配/)).toBeInTheDocument();
    expect(screen.queryByText(/待处理（/)).not.toBeInTheDocument();
  });

  it("有 allowed_actions=claim 时渲染领取按钮并回调", async () => {
    const onClaim = vi.fn();
    renderBody({
      data: queue({ open: [entry({ allowedActions: ["claim"] })] }),
      onClaim,
    });

    const button = screen.getByRole("button", { name: /领\s*取/ });
    await userEvent.click(button);
    expect(onClaim).toHaveBeenCalledTimes(1);
  });

  it("服务端未授权任何动作时不渲染按钮，并给出明确说明", () => {
    renderBody({
      data: queue({ open: [entry({ allowedActions: [] })] }),
    });

    expect(
      screen.queryByRole("button", { name: /领\s*取/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/服务端未返回可执行动作/)).toBeInTheDocument();
  });

  it("工作台链接带项目作用域，不靠前端推断", () => {
    renderBody({
      data: queue({ open: [entry({ allowedActions: ["claim"] })] }),
    });

    expect(screen.getByRole("link", { name: "打开工作台" })).toHaveAttribute(
      "href",
      expect.stringContaining(`project_id=${PROJECT_ID}`),
    );
  });

  it("部分项目失败时报 Partial，并逐条列出作用域", () => {
    renderBody({
      data: queue({
        failures: [
          {
            scope: "project:prj-b",
            error: new ApiError({
              kind: "http",
              status: 403,
              code: "forbidden",
              message: "无权访问",
            }),
          },
        ],
      }),
    });

    expect(screen.getByText(/1 个作用域读取失败/)).toBeInTheDocument();
    expect(screen.getByText(/project:prj-b（forbidden）/)).toBeInTheDocument();
  });

  it("队列截断时明确告知不完整，而不是当成全部", () => {
    renderBody({ data: queue({ truncated: true }) });

    expect(screen.getByText(/队列已截断/)).toBeInTheDocument();
  });

  it("加载失败显示错误态与重试入口", async () => {
    const onRetry = vi.fn();
    renderBody({
      phase: "error",
      data: null,
      error: new ApiError({ kind: "network", message: "网络不可用" }),
      onRetry,
    });

    expect(screen.getByText(/网络不可用/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("身份不可用（forbidden）时不渲染队列内容", () => {
    renderBody({
      phase: "forbidden",
      data: null,
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权访问",
      }),
    });

    expect(screen.queryByText(/当前没有待办分配/)).not.toBeInTheDocument();
  });
});
