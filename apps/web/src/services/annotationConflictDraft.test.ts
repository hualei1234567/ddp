import { describe, expect, it } from "vitest";

import {
  browserConflictStorage,
  clearConflictSnapshot,
  conflictSnapshotKey,
  loadConflictSnapshot,
  saveConflictSnapshot,
  type ConflictStorage,
  type ConflictSnapshot,
} from "./annotationConflictDraft";
import { ASSIGNMENT_ID, EPISODE_ID, PROJECT_ID } from "./__fixtures__/annotationFixtures";

function memoryStorage(): ConflictStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
    removeItem: (key) => {
      data.delete(key);
    },
  };
}

function snapshot(overrides: Partial<ConflictSnapshot> = {}): ConflictSnapshot {
  return {
    version: 1,
    projectId: PROJECT_ID,
    assignmentId: ASSIGNMENT_ID,
    taskCode: "task-conflict",
    expectedVersion: 3,
    currentVersion: 7,
    mine: {
      [EPISODE_ID]: {
        action: "PLACE",
        confidence: "0.75",
        segment: { start: "1200", end: "1800" },
      },
    },
    server: {
      [EPISODE_ID]: { action: "PICK" },
    },
    choices: { [`${EPISODE_ID}.action`]: "server" },
    updatedAt: 1000,
    ...overrides,
  };
}

describe("FR-019 冲突快照持久化（不丢数据）", () => {
  it("保存后能在「刷新后」重新读回全部输入与已做的合并选择", () => {
    const storage = memoryStorage();
    expect(saveConflictSnapshot(storage, snapshot())).toBe(true);

    // 模拟刷新：全新读取，不共享任何内存状态。
    const loaded = loadConflictSnapshot(storage, {
      projectId: PROJECT_ID,
      assignmentId: ASSIGNMENT_ID,
    });
    expect(loaded.status).toBe("ready");
    expect(loaded.snapshot?.mine[EPISODE_ID]?.action).toBe("PLACE");
    expect(loaded.snapshot?.mine[EPISODE_ID]?.confidence).toBe("0.75");
    expect(loaded.snapshot?.expectedVersion).toBe(3);
    expect(loaded.snapshot?.currentVersion).toBe(7);
    expect(loaded.snapshot?.choices[`${EPISODE_ID}.action`]).toBe("server");
  });

  it("键按项目 + 分配隔离，不包含服务端版本（刷新后未知版本也能读）", () => {
    expect(conflictSnapshotKey(PROJECT_ID, ASSIGNMENT_ID)).toBe(
      `ddp.annotation.conflict:${PROJECT_ID}:${ASSIGNMENT_ID}`,
    );
    const storage = memoryStorage();
    saveConflictSnapshot(storage, snapshot());
    // 另一个分配读不到本分配的冲突。
    expect(
      loadConflictSnapshot(storage, {
        projectId: PROJECT_ID,
        assignmentId: "99999999-9999-7999-8999-999999999999",
      }).status,
    ).toBe("missing");
  });

  it("没有快照时返回 missing，不虚构冲突", () => {
    expect(
      loadConflictSnapshot(memoryStorage(), {
        projectId: PROJECT_ID,
        assignmentId: ASSIGNMENT_ID,
      }).status,
    ).toBe("missing");
  });

  it("损坏的快照返回 invalid（页面提示先复制草稿），不静默当作无冲突", () => {
    const storage = memoryStorage();
    storage.setItem(
      conflictSnapshotKey(PROJECT_ID, ASSIGNMENT_ID),
      '{"version":1,"projectId":',
    );
    expect(
      loadConflictSnapshot(storage, {
        projectId: PROJECT_ID,
        assignmentId: ASSIGNMENT_ID,
      }).status,
    ).toBe("invalid");
  });

  it("结构不符（缺字段/版本不认识）也返回 invalid", () => {
    const storage = memoryStorage();
    storage.setItem(
      conflictSnapshotKey(PROJECT_ID, ASSIGNMENT_ID),
      JSON.stringify({ version: 99, projectId: PROJECT_ID }),
    );
    expect(
      loadConflictSnapshot(storage, {
        projectId: PROJECT_ID,
        assignmentId: ASSIGNMENT_ID,
      }).status,
    ).toBe("invalid");
  });

  it("存储写入失败（配额满/隐私模式）返回 false，不假装已保存", () => {
    const failing: ConflictStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => undefined,
    };
    expect(saveConflictSnapshot(failing, snapshot())).toBe(false);
  });

  it("合并成功后清除快照，已解决的冲突不再弹出", () => {
    const storage = memoryStorage();
    saveConflictSnapshot(storage, snapshot());
    clearConflictSnapshot(storage, PROJECT_ID, ASSIGNMENT_ID);
    expect(
      loadConflictSnapshot(storage, {
        projectId: PROJECT_ID,
        assignmentId: ASSIGNMENT_ID,
      }).status,
    ).toBe("missing");
  });

  it("新冲突覆盖旧冲突：同一分配只保留最新一次的「我的版本」", () => {
    const storage = memoryStorage();
    saveConflictSnapshot(storage, snapshot());
    saveConflictSnapshot(
      storage,
      snapshot({
        expectedVersion: 7,
        currentVersion: 9,
        mine: { [EPISODE_ID]: { action: "PICK" } },
      }),
    );
    const loaded = loadConflictSnapshot(storage, {
      projectId: PROJECT_ID,
      assignmentId: ASSIGNMENT_ID,
    });
    expect(loaded.snapshot?.currentVersion).toBe(9);
    expect(loaded.snapshot?.mine[EPISODE_ID]?.action).toBe("PICK");
    expect(storage.data.size).toBe(1);
  });

  it("localStorage 不可用时返回 null（由页面提示无法跨刷新恢复）", () => {
    // node 环境下没有 window，等价于隐私模式里的"存储不可用"。
    expect(browserConflictStorage()).toBeNull();
  });
});
