import { describe, expect, it, vi } from "vitest";

import type { AnnotationAssignment } from "../types/annotation";
import type { Project } from "../types/project";
import { ApiError } from "./api";
import { CursorPageOverflowError } from "./cursorPage";
import { loadPersonalQueue, MAX_SCANNED_PROJECTS } from "./personalQueue";

const USER_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_A = "22222222-2222-2222-2222-222222222222";
const PROJECT_B = "33333333-3333-3333-3333-333333333333";

function project(overrides: Partial<Project> = {}): Project {
  return {
    project_id: PROJECT_A,
    code: "prj-a",
    name: "项目 A",
    description: null,
    status: "ACTIVE",
    membership_role: "editor",
    allowed_actions: ["read", "write"],
    ...overrides,
  } as Project;
}

type AssignmentWithActions = AnnotationAssignment & { allowed_actions?: string[] };

function assignment(
  overrides: Partial<AssignmentWithActions> = {},
): AssignmentWithActions {
  return {
    assignment_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    project_id: PROJECT_A,
    task_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    batch_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    assignee_id: USER_ID,
    status: "ASSIGNED",
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
    version: 1,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
    ...overrides,
  } as AnnotationAssignment;
}

interface Harness {
  deps: Parameters<typeof loadPersonalQueue>[0];
  listAssignments: ReturnType<typeof vi.fn>;
}

function harness(options: {
  projects?: Project[];
  byProject?: Record<string, AssignmentWithActions[] | Error>;
  user?: Error;
}): Harness {
  const listAssignments = vi.fn(async (projectId: string) => {
    const value = options.byProject?.[projectId] ?? [];
    if (value instanceof Error) {
      throw value;
    }
    return { items: value, next_cursor: null };
  });
  return {
    deps: {
      identity: {
        getCurrentUser: vi.fn(async () => {
          if (options.user) {
            throw options.user;
          }
          return { user_id: USER_ID };
        }),
      },
      projects: {
        listProjects: vi.fn(async () => ({
          items: options.projects ?? [project()],
          next_cursor: null,
        })),
      },
      assignments: { listAssignments },
    },
    listAssignments,
  };
}

const signal = new AbortController().signal;

describe("loadPersonalQueue", () => {
  it("按服务端身份过滤分配，并按状态分流", async () => {
    const { deps, listAssignments } = harness({
      byProject: {
        [PROJECT_A]: [
          assignment({ status: "ASSIGNED" }),
          assignment({
            assignment_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab",
            status: "IN_PROGRESS",
          }),
          assignment({
            assignment_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac",
            status: "SUBMITTED",
          }),
          assignment({
            assignment_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaad",
            status: "COMPLETED",
          }),
        ],
      },
    });

    const queue = await loadPersonalQueue(deps, signal);

    expect(listAssignments).toHaveBeenCalledWith(
      PROJECT_A,
      expect.objectContaining({ assigneeId: USER_ID }),
    );
    expect(queue.open.map((entry) => entry.assignment.status)).toEqual([
      "ASSIGNED",
      "IN_PROGRESS",
    ]);
    expect(queue.submitted).toHaveLength(1);
    expect(queue.failures).toEqual([]);
    expect(queue.truncated).toBe(false);
  });

  it("服务端 allowed_actions 原样透传，前端不自造按钮", async () => {
    const { deps } = harness({
      byProject: {
        [PROJECT_A]: [
          assignment({ status: "ASSIGNED", allowed_actions: ["claim", "revoke"] }),
        ],
      },
    });

    const queue = await loadPersonalQueue(deps, signal);

    expect(queue.open[0]?.allowedActions).toEqual(["claim", "revoke"]);
  });

  it("单个项目失败不拖垮其它项目，失败原因逐条保留", async () => {
    const { deps } = harness({
      projects: [project(), project({ project_id: PROJECT_B, code: "prj-b" })],
      byProject: {
        [PROJECT_A]: [assignment()],
        [PROJECT_B]: new ApiError({ kind: "http", status: 403, code: "forbidden", message: "无权访问" }),
      },
    });

    const queue = await loadPersonalQueue(deps, signal);

    expect(queue.open).toHaveLength(1);
    expect(queue.failures).toHaveLength(1);
    expect(queue.failures[0]?.scope).toBe("project:prj-b");
    expect(queue.failures[0]?.error.code ?? queue.failures[0]?.error.kind).toBeTruthy();
  });

  it("身份读取失败直接抛出：没有身份就不猜队列", async () => {
    const { deps, listAssignments } = harness({
      user: new ApiError({ kind: "http", status: 401, code: "unauthorized", message: "缺少访问令牌" }),
    });

    await expect(loadPersonalQueue(deps, signal)).rejects.toThrow("缺少访问令牌");
    expect(listAssignments).not.toHaveBeenCalled();
  });

  it("项目数超过上限时截断并显式标记", async () => {
    const many = Array.from({ length: MAX_SCANNED_PROJECTS + 3 }, (_, index) =>
      project({
        project_id: `00000000-0000-0000-0000-0000000000${String(index).padStart(2, "0")}`,
        code: `prj-${index}`,
      }),
    );
    const { deps, listAssignments } = harness({ projects: many });

    const queue = await loadPersonalQueue(deps, signal);

    expect(listAssignments).toHaveBeenCalledTimes(MAX_SCANNED_PROJECTS);
    expect(queue.truncated).toBe(true);
    expect(queue.scannedProjects).toBe(MAX_SCANNED_PROJECTS);
  });

  it("单项目页数触顶记为截断而不是空队列", async () => {
    const { deps } = harness({});
    deps.assignments.listAssignments = vi.fn(async () => {
      throw new CursorPageOverflowError(5);
    });

    const queue = await loadPersonalQueue(deps, signal);

    expect(queue.truncated).toBe(true);
    expect(queue.failures).toHaveLength(1);
  });

  it("没有任何分配时返回空队列而不是错误", async () => {
    const { deps } = harness({});

    const queue = await loadPersonalQueue(deps, signal);

    expect(queue).toMatchObject({
      open: [],
      submitted: [],
      failures: [],
      truncated: false,
    });
  });
});
