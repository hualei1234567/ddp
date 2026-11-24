import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../services/api";
import {
  ASSIGNMENT_ID,
  EPISODE_ID,
  PROJECT_ID,
  TASK_ID,
  annotationSchemaFixture,
  annotationSubmissionFixture,
} from "../services/__fixtures__/annotationFixtures";
import { episodeFixture } from "../services/__fixtures__/collectionFixtures";
import type { EpisodeFormValues } from "../services/annotationValidation";
import type { AnnotationFeedback } from "../services/annotationPresentation";
import type { AnnotationSubmission } from "../types/annotation";
import type { ConflictSnapshot } from "../services/annotationConflictDraft";
import { useWorkbenchConflictFlow } from "./useWorkbenchConflictFlow";

/**
 * FR-019 完整恢复流的状态机测试（服务层以下全真实，只 mock 网络）。
 *
 * 覆盖审计要求的闭环：「冲突检测 → 提示 → 复制草稿 → 合并 → 重提交成功」，
 * 并额外锁死两条红线：
 * - 冲突时**不得**静默丢弃用户输入（快照落盘 + 输入回填）；
 * - 「无法重新提交」必须如实说 blocked，不能渲染成成功。
 */

const createSubmission = vi.fn();
const actOnSubmission = vi.fn();
const listSubmissions = vi.fn();
const getSubmission = vi.fn();

vi.mock("../services/annotation", () => ({
  annotationApi: {
    createSubmission: (...args: unknown[]) => createSubmission(...args),
    actOnSubmission: (...args: unknown[]) => actOnSubmission(...args),
    listSubmissions: (...args: unknown[]) => listSubmissions(...args),
    getSubmission: (...args: unknown[]) => getSubmission(...args),
  },
}));

const mine: Record<string, EpisodeFormValues> = {
  [EPISODE_ID]: {
    action: "PLACE",
    confidence: "0.75",
    segment: { start: "1200", end: "1800" },
  },
};

function conflict(): ApiError {
  return new ApiError({
    kind: "http",
    status: 409,
    code: "version_conflict",
    message: "资源版本已变化，请重新读取后再试",
    requestId: "req-1",
    details: { expected_version: 3, current_version: 7 },
  });
}

function serverSubmission(overrides: Partial<AnnotationSubmission> = {}): AnnotationSubmission {
  return {
    ...annotationSubmissionFixture,
    version: 7,
    records: [
      {
        ...annotationSubmissionFixture.records[0]!,
        labels: [
          {
            label_key: "action",
            value: "PICK",
            source: "HUMAN",
            label_status: "CONFIRMED",
          },
        ],
      },
    ],
    ...overrides,
  };
}

function setup() {
  const feedback: Array<AnnotationFeedback | null> = [];
  const applied: Array<Record<string, EpisodeFormValues>> = [];
  const deps = {
    projectId: PROJECT_ID,
    assignmentId: ASSIGNMENT_ID,
    taskId: TASK_ID,
    taskCode: "task-conflict",
    assignmentIdForCreate: ASSIGNMENT_ID,
    dataSource: "REAL" as const,
    schema: annotationSchemaFixture,
    episodes: [episodeFixture],
    readValues: () => mine,
    applyValues: (values: Record<string, EpisodeFormValues>) => {
      applied.push(values);
    },
    latestSubmission: null,
    setFeedback: (value: AnnotationFeedback | null) => {
      feedback.push(value);
    },
    reload: vi.fn(),
    resetDraft: vi.fn(),
    setBusyAction: vi.fn(),
  };
  const hook = renderHook(() => useWorkbenchConflictFlow(deps));
  return { hook, feedback, applied, deps };
}

beforeEach(() => {
  createSubmission.mockReset();
  actOnSubmission.mockReset();
  listSubmissions.mockReset();
  getSubmission.mockReset();
  // jsdom 有 localStorage：每个用例前清空，避免"上一个用例的冲突快照"
  // 让本用例的 restore() 读到别人的数据。
  window.localStorage.clear();
  // jsdom 未实现 createObjectURL；下载路径只需要它返回一个可用的假 URL。
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:mock"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});

describe("useWorkbenchConflictFlow（FR-019 恢复闭环）", () => {
  it("409 进入恢复流并给出两侧版本（不当作普通失败）", async () => {
    listSubmissions.mockResolvedValue({
      items: [serverSubmission()],
      next_cursor: null,
    });
    const { hook } = setup();

    let handled = false;
    await act(async () => {
      handled = await hook.result.current.enter(conflict(), 3);
    });

    expect(handled).toBe(true);
    expect(hook.result.current.recovery.phase).toBe("detected");
    expect(hook.result.current.recovery.context?.expectedVersion).toBe(3);
    expect(hook.result.current.recovery.context?.currentVersion).toBe(7);
    // 服务端最新内容已拉取（合并基准不是过期的缓存）。
    expect(listSubmissions).toHaveBeenCalled();
  });

  it("非 409 不进入恢复流（普通错误照常展示）", async () => {
    const { hook } = setup();
    const forbidden = new ApiError({
      kind: "http",
      status: 403,
      code: "forbidden",
      message: "无权",
    });
    let handled = true;
    await act(async () => {
      handled = await hook.result.current.enter(forbidden, 1);
    });
    expect(handled).toBe(false);
    expect(hook.result.current.recovery.phase).toBe("idle");
  });

  it("服务端最新已是 DRAFT：用其最新 version 重放 submit（重提交成功路径）", async () => {
    const draft = serverSubmission({
      submission_id: "55555555-5555-7555-8555-555555555555",
      status: "DRAFT",
      version: 4,
    });
    listSubmissions.mockResolvedValue({ items: [draft], next_cursor: null });
    actOnSubmission.mockResolvedValue({ ...draft, status: "SUBMITTED" });

    const { hook, feedback, deps } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });

    // 用户逐项选择后确认合并。
    await act(async () => {
      await hook.result.current.applyMerged(mine);
    });

    expect(actOnSubmission).toHaveBeenCalledWith(
      PROJECT_ID,
      draft.submission_id,
      "submit",
      { version: 4 },
    );
    // 没有新建草稿：直接重放动作。
    expect(createSubmission).not.toHaveBeenCalled();
    expect(deps.resetDraft).toHaveBeenCalled();
    expect(feedback.at(-1)?.kind).toBe("success");
  });

  it("服务端最新已终结（REWORK_REQUIRED）：基于合并结果创建新版本再提交", async () => {
    const rework = serverSubmission({
      submission_id: "66666666-6666-7666-8666-666666666666",
      status: "REWORK_REQUIRED",
      version: 8,
    });
    listSubmissions.mockResolvedValue({ items: [rework], next_cursor: null });
    createSubmission.mockResolvedValue({
      ...rework,
      submission_id: "77777777-7777-7777-8777-777777777777",
      status: "DRAFT",
      version: 1,
    });
    actOnSubmission.mockResolvedValue({ ...rework, status: "SUBMITTED" });

    const { hook } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });
    await act(async () => {
      await hook.result.current.applyMerged(mine);
    });

    expect(createSubmission).toHaveBeenCalledTimes(1);
    expect(actOnSubmission).toHaveBeenCalledWith(
      PROJECT_ID,
      "77777777-7777-7777-8777-777777777777",
      "submit",
      { version: 1 },
    );
  });

  it("流程不允许再次提交时 blocked：只回填表单并如实提示，不发请求", async () => {
    const inReview = serverSubmission({
      submission_id: "88888888-8888-7888-8888-888888888888",
      status: "IN_REVIEW",
      version: 9,
    });
    listSubmissions.mockResolvedValue({ items: [inReview], next_cursor: null });

    const { hook, feedback, applied } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });
    await act(async () => {
      await hook.result.current.applyMerged(mine);
    });

    expect(createSubmission).not.toHaveBeenCalled();
    expect(actOnSubmission).not.toHaveBeenCalled();
    // 合并结果仍然回填给用户，不丢。
    expect(applied).toHaveLength(1);
    expect(feedback.at(-1)?.title).toContain("无法重新提交");
    expect(feedback.at(-1)?.kind).toBe("warning");
  });

  it("重新提交再次 409 时重新进入恢复流，输入仍然不丢", async () => {
    const rework = serverSubmission({
      submission_id: "99999999-9999-7999-8999-999999999999",
      status: "REWORK_REQUIRED",
      version: 8,
    });
    listSubmissions.mockResolvedValue({ items: [rework], next_cursor: null });
    createSubmission.mockRejectedValue(conflict());

    const { hook } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });
    await act(async () => {
      await hook.result.current.applyMerged(mine);
    });

    // 再次冲突后仍然处于恢复态：用户可以复制草稿或再次合并。
    expect(hook.result.current.recovery.phase).toBe("detected");
    expect(hook.result.current.recovery.context?.currentVersion).toBe(7);
  });

  it("复制草稿回调拿到的是完整导出（含两侧版本与我的输入）", async () => {
    listSubmissions.mockResolvedValue({
      items: [serverSubmission()],
      next_cursor: null,
    });
    const { hook } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });

    // node 环境没有 document：copyDraft 必须走下载分支而不是抛错。
    let result = false;
    await act(async () => {
      result = await hook.result.current.recovery.copyDraft("download");
    });
    expect(result).toBe(true);
  });

  it("没有未合并快照时 restore 返回 null，不虚构冲突", () => {
    const { hook } = setup();
    let snapshot = null;
    act(() => {
      snapshot = hook.result.current.restore();
    });
    expect(snapshot).toBeNull();
    expect(hook.result.current.recovery.phase).toBe("idle");
  });

  it("刷新后（组件重挂载）恢复未合并的冲突与当时的输入", async () => {
    listSubmissions.mockResolvedValue({
      items: [serverSubmission()],
      next_cursor: null,
    });
    // 第一次挂载：发生冲突 → 快照落盘。
    const first = setup();
    await act(async () => {
      await first.hook.result.current.enter(conflict(), 3);
    });
    expect(first.hook.result.current.recovery.phase).toBe("detected");
    first.hook.unmount();

    // 模拟刷新：新挂载的 Hook 从 localStorage 读回同一份未合并冲突。
    const second = setup();
    let restored: ConflictSnapshot | null = null;
    act(() => {
      restored = second.hook.result.current.restore();
    });
    // act 回调内的赋值会被 TS 窄化，这里显式取回快照值再断言。
    const snapshot = restored as ConflictSnapshot | null;
    expect(snapshot).not.toBeNull();
    expect(snapshot).toMatchObject({
      expectedVersion: 3,
      currentVersion: 7,
    });
    // 冲突当时的输入被恢复（不是空表单）。
    expect(snapshot?.mine[EPISODE_ID]?.action).toBe("PLACE");
    expect(snapshot?.mine[EPISODE_ID]?.confidence).toBe("0.75");
    // 页面收到"已恢复冲突"的提示。
    expect(second.feedback.at(-1)?.title).toContain("未合并的标注冲突");
  });

  it("放弃合并清空冲突态（快照清除后不再弹出）", async () => {
    listSubmissions.mockResolvedValue({
      items: [serverSubmission()],
      next_cursor: null,
    });
    const { hook } = setup();
    await act(async () => {
      await hook.result.current.enter(conflict(), 3);
    });
    act(() => hook.result.current.discard());
    await waitFor(() => expect(hook.result.current.recovery.phase).toBe("idle"));
    expect(hook.result.current.recovery.context).toBeNull();
  });
});
