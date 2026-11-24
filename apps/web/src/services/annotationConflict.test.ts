import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  buildConflictContext,
  buildConflictDraftExport,
  conflictDraftFileName,
  diffFieldKey,
  diffLabelValues,
  formValuesFromRecords,
  hasComparableRecords,
  isBlankValue,
  mergeByChoice,
  parseConflictVersions,
  planConflictResubmission,
  serializeConflictDraft,
  summarizeDiff,
} from "./annotationConflict";
import {
  EPISODE_ID,
  annotationSubmissionFixture,
  PROJECT_ID,
  ASSIGNMENT_ID,
  RECORD_SHA,
} from "./__fixtures__/annotationFixtures";
import type { EpisodeFormValues } from "./annotationValidation";
import type { AnnotationSubmission } from "../types/annotation";

function conflictError(details: Record<string, unknown> | null): ApiError {
  return new ApiError({
    kind: "http",
    status: 409,
    code: "version_conflict",
    message: "资源版本已变化，请重新读取后再试",
    requestId: "req-conflict",
    details,
  });
}

const mine: Record<string, EpisodeFormValues> = {
  [EPISODE_ID]: {
    action: "PLACE",
    confidence: "0.75",
    segment: { start: "1200", end: "1800" },
  },
};

/** 服务端最新版本：action 被他人改成 PICK、confidence 被清空、新增 segment。 */
function serverSubmission(): AnnotationSubmission {
  return {
    ...annotationSubmissionFixture,
    version: 7,
    submission_version: 2,
    records: [
      {
        ...annotationSubmissionFixture.records[0]!,
        labels: [
          { label_key: "action", value: "PICK", source: "HUMAN", label_status: "CONFIRMED" },
          { label_key: "confidence", value: null, source: "HUMAN", label_status: "CONFIRMED" },
          {
            label_key: "segment",
            value: { start_time_ns: 1200, end_time_ns: 1800, time_domain: "UTC" },
            source: "HUMAN",
            label_status: "CONFIRMED",
          },
        ],
      },
    ],
  };
}

describe("FR-019 冲突检测", () => {
  it("从 409 details 解析出「我基于 M、服务端当前 N」", () => {
    const versions = parseConflictVersions(
      conflictError({ expected_version: 3, current_version: 7 }),
      3,
    );
    expect(versions).toEqual({ expectedVersion: 3, currentVersion: 7 });
  });

  it("服务端只给 current_version 时，expected 取客户端实际发送的版本", () => {
    expect(parseConflictVersions(conflictError({ current_version: 5 }), 2)).toEqual({
      expectedVersion: 2,
      currentVersion: 5,
    });
  });

  it("非 409 不当作冲突（避免把普通失败渲染成恢复流）", () => {
    const forbidden = new ApiError({
      kind: "http",
      status: 403,
      code: "forbidden",
      message: "无权",
      details: { current_version: 3 },
    });
    expect(parseConflictVersions(forbidden, 1)).toBeNull();
    expect(parseConflictVersions(new Error("boom"), 1)).toBeNull();
  });

  it("409 但没有 current_version 时返回 null：不猜版本号", () => {
    expect(parseConflictVersions(conflictError(null), 1)).toBeNull();
    expect(parseConflictVersions(conflictError({ foo: "bar" }), 1)).toBeNull();
  });
});

describe("FR-019 差异计算与合并", () => {
  it("识别服务端新增 / 仅我有 / 值不同三类差异", () => {
    const server = formValuesFromRecords(serverSubmission().records);
    const diffs = diffLabelValues(mine, server);
    const byKey = new Map(diffs.map((diff) => [diffFieldKey(diff), diff]));

    // action：我 PLACE、服务端 PICK → 值不同。
    expect(byKey.get(`${EPISODE_ID}.action`)?.kind).toBe("changed");
    // confidence：我有 0.75、服务端为 null → 仅我有。
    expect(byKey.get(`${EPISODE_ID}.confidence`)?.kind).toBe("removed");
    // segment：两侧都有内容且相同 → 不产生差异。
    expect(byKey.has(`${EPISODE_ID}.segment`)).toBe(false);
    expect(summarizeDiff(diffs)).toEqual({
      total: 2,
      added: 0,
      removed: 1,
      changed: 1,
    });
  });

  it("服务端有、我空 → 报告为服务端新增", () => {
    const surface = formValuesFromRecords(serverSubmission().records);
    const diffs = diffLabelValues({ [EPISODE_ID]: { action: "PICK" } }, surface);
    const added = diffs.filter((diff) => diff.kind === "added");
    // confidence 服务端是 null（空），两侧都空 → 不算差异；
    // segment 服务端有内容而我完全没填 → 服务端新增。
    expect(added.map((diff) => diff.labelKey)).toEqual(["segment"]);
  });

  it("空串与 undefined 视为相同，不产生噪声差异", () => {
    expect(isBlankValue("")).toBe(true);
    expect(isBlankValue(null)).toBe(true);
    expect(isBlankValue({ start: "", end: "" })).toBe(true);
    const diffs = diffLabelValues(
      { [EPISODE_ID]: { action: "PICK", confidence: "" } },
      { [EPISODE_ID]: { action: "PICK" } },
    );
    expect(diffs).toEqual([]);
  });

  it("默认保留我的值，只有显式选「采用服务端」的字段被替换", () => {
    const server = formValuesFromRecords(serverSubmission().records);
    const diffs = diffLabelValues(mine, server);
    const changedField = diffs.find((diff) => diff.kind === "changed")!;

    // 不传任何选择：全部保留我的。
    const untouched = mergeByChoice(mine, server, {});
    expect(untouched[EPISODE_ID]?.action).toBe("PLACE");
    expect(untouched[EPISODE_ID]?.confidence).toBe("0.75");

    // 只对 action 选服务端：action 变 PICK，confidence 仍是我的。
    const merged = mergeByChoice(mine, server, {
      [diffFieldKey(changedField)]: "server",
    });
    expect(merged[EPISODE_ID]?.action).toBe("PICK");
    expect(merged[EPISODE_ID]?.confidence).toBe("0.75");
    // 服务端把 segment 落成 {start_time_ns..} 后再回填成表单值，两边等价。
    expect(merged[EPISODE_ID]?.segment).toEqual({ start: "1200", end: "1800" });
  });

  it("选「采用服务端」删除某字段时，合并结果为清空而不是保留旧值", () => {
    const server = formValuesFromRecords(serverSubmission().records);
    const removed = diffLabelValues(mine, server).find(
      (diff) => diff.kind === "removed",
    )!;
    const merged = mergeByChoice(mine, server, {
      [diffFieldKey(removed)]: "server",
    });
    expect(merged[EPISODE_ID]?.confidence).toBeUndefined();
  });
});

describe("FR-019 复制草稿导出", () => {
  it("导出包含两侧版本、我的全部输入、服务端值与差异清单", () => {
    const context = buildConflictContext(
      { expectedVersion: 3, currentVersion: 7 },
      serverSubmission(),
    );
    const draft = buildConflictDraftExport({
      projectId: PROJECT_ID,
      assignmentId: ASSIGNMENT_ID,
      taskCode: "task-conflict",
      context,
      mine,
      now: new Date("2026-11-24T06:00:00Z"),
    });
    expect(draft.format).toBe("ddp.annotation.conflict.v1");
    expect(draft.expectedVersion).toBe(3);
    expect(draft.currentVersion).toBe(7);
    expect(draft.mine[EPISODE_ID]?.action).toBe("PLACE");
    // 不丢数据：未填/未通过校验的字段也在导出里（confidence 是"仅我有"）。
    expect(draft.diffs.map((diff) => diff.labelKey).sort()).toEqual([
      "action",
      "confidence",
    ]);
    expect(draft.server?.[EPISODE_ID]?.action).toBe("PICK");

    const text = serializeConflictDraft(draft);
    expect(JSON.parse(text)).toMatchObject({ currentVersion: 7 });
    expect(conflictDraftFileName(draft)).toBe(
      "annotation-conflict-task-conflict-2026-11-24T06-00-00-000Z.json",
    );
  });

  it("服务端记录不可比时 server 为 null，不伪造空差异", () => {
    const empty = { ...annotationSubmissionFixture, records: [] };
    expect(hasComparableRecords(empty)).toBe(false);
    const context = buildConflictContext(
      { expectedVersion: 1, currentVersion: 2 },
      empty,
    );
    const draft = buildConflictDraftExport({
      projectId: PROJECT_ID,
      assignmentId: ASSIGNMENT_ID,
      taskCode: "task",
      context,
      mine,
    });
    expect(draft.server).toBeNull();
    expect(draft.diffs).toEqual([]);
    // 关键：我的输入仍在。
    expect(draft.mine[EPISODE_ID]?.action).toBe("PLACE");
  });

  it("盲审脱敏后标签值为空的记录视为不可比", () => {
    const redacted: AnnotationSubmission = {
      ...annotationSubmissionFixture,
      records: [
        {
          ...annotationSubmissionFixture.records[0]!,
          labels: [],
          record_sha256: RECORD_SHA,
        },
      ],
    };
    expect(hasComparableRecords(redacted)).toBe(false);
  });
});

describe("FR-019 重新提交规划（不谎报成功）", () => {
  it("服务端最新仍是 DRAFT：用它的最新 version 重放动作", () => {
    expect(
      planConflictResubmission({
        submission_id: "sub-1",
        status: "DRAFT",
        version: 4,
      }),
    ).toEqual({ kind: "retry-action", submissionId: "sub-1", version: 4 });
  });

  it("最新版本已终结但可替代：基于合并结果创建新版本", () => {
    for (const status of ["REWORK_REQUIRED", "APPROVED", "WITHDRAWN"]) {
      expect(
        planConflictResubmission({ submission_id: "sub-2", status, version: 5 }),
      ).toMatchObject({ kind: "create-new-version" });
    }
  });

  it("流程不允许再次提交时明确 blocked，不伪装成成功", () => {
    const plan = planConflictResubmission({
      submission_id: "sub-3",
      status: "IN_REVIEW",
      version: 6,
    });
    expect(plan.kind).toBe("blocked");
    if (plan.kind === "blocked") {
      expect(plan.reason).toContain("IN_REVIEW");
    }
  });

  it("服务端已无提交版本时创建新版本", () => {
    expect(planConflictResubmission(null).kind).toBe("create-new-version");
  });
});

describe("FR-019 服务端标签值反向映射", () => {
  it("时间片段与数字标签能还原成表单可编辑的值", () => {
    const values = formValuesFromRecords([
      {
        ...annotationSubmissionFixture.records[0]!,
        episode_id: EPISODE_ID,
        labels: [
          {
            label_key: "segment",
            value: { start_time_ns: 10, end_time_ns: 20, time_domain: "UTC" },
            source: "HUMAN",
            label_status: "CONFIRMED",
          },
          {
            label_key: "confidence",
            value: 0.5,
            source: "HUMAN",
            label_status: "CONFIRMED",
          },
        ],
      },
    ]);
    expect(values[EPISODE_ID]?.segment).toEqual({ start: "10", end: "20" });
    expect(values[EPISODE_ID]?.confidence).toBe("0.5");
  });

  it("多选标签还原为字符串数组", () => {
    const values = formValuesFromRecords([
      {
        ...annotationSubmissionFixture.records[0]!,
        labels: [
          {
            label_key: "tags",
            value: ["a", "b"],
            source: "HUMAN",
            label_status: "CONFIRMED",
          },
        ],
      },
    ]);
    expect(values[EPISODE_ID]?.tags).toEqual(["a", "b"]);
  });
});
