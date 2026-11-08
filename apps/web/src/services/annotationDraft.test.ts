import { describe, expect, it } from "vitest";

import {
  annotationDraftKey,
  clearAnnotationDraft,
  createAnnotationDraft,
  hydrateFormValues,
  loadAnnotationDraft,
  saveAnnotationDraft,
  type DraftStorage,
} from "./annotationDraft";
import {
  annotationSchemaFixture,
  ASSIGNMENT_ID,
  PROJECT_ID,
  SCHEMA_SHA,
} from "./__fixtures__/annotationFixtures";

function memoryStorage(): DraftStorage & { data: Map<string, string> } {
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

const identity = {
  projectId: PROJECT_ID,
  assignmentId: ASSIGNMENT_ID,
  schemaSha256: SCHEMA_SHA,
};

const values = {
  "ep-1": { action: "PICK", confidence: "0.5", segment: { start: "1", end: "2" } },
};

describe("标注本地草稿", () => {
  it("按项目 + 分配 + Schema 快照保存并恢复", () => {
    const storage = memoryStorage();
    const draft = createAnnotationDraft(identity, values, 1000);
    expect(saveAnnotationDraft(storage, draft)).toBe(true);
    expect(storage.data.has(annotationDraftKey(PROJECT_ID, ASSIGNMENT_ID))).toBe(
      true,
    );
    const result = loadAnnotationDraft(storage, identity);
    expect(result.status).toBe("ready");
    expect(result.draft?.values["ep-1"]?.action).toBe("PICK");
  });

  it("Schema 快照变化时标记不兼容且不加载", () => {
    const storage = memoryStorage();
    saveAnnotationDraft(storage, createAnnotationDraft(identity, values));
    const result = loadAnnotationDraft(storage, {
      ...identity,
      schemaSha256: "sha256:" + "f".repeat(64),
    });
    expect(result.status).toBe("incompatible");
    expect(result.draft?.values["ep-1"]?.action).toBe("PICK");
  });

  it("损坏的草稿标记 invalid，不进入表单", () => {
    const storage = memoryStorage();
    storage.setItem(
      annotationDraftKey(PROJECT_ID, ASSIGNMENT_ID),
      '{"version":1,"projectId":',
    );
    const result = loadAnnotationDraft(storage, identity);
    expect(result.status).toBe("invalid");
    expect(result.draft).toBeNull();
  });

  it("无草稿时返回 missing", () => {
    expect(loadAnnotationDraft(memoryStorage(), identity).status).toBe("missing");
  });

  it("清除后草稿不可恢复", () => {
    const storage = memoryStorage();
    saveAnnotationDraft(storage, createAnnotationDraft(identity, values));
    clearAnnotationDraft(storage, PROJECT_ID, ASSIGNMENT_ID);
    expect(loadAnnotationDraft(storage, identity).status).toBe("missing");
  });

  it("恢复时保留已填值并为新增标签补默认值", () => {
    const hydrated = hydrateFormValues(
      annotationSchemaFixture,
      ["ep-1", "ep-2"],
      createAnnotationDraft(identity, {
        "ep-1": { action: "PLACE" },
      }),
    );
    expect(hydrated["ep-1"]?.action).toBe("PLACE");
    expect(hydrated["ep-1"]?.confidence).toBe("");
    expect(hydrated["ep-1"]?.segment).toEqual({ start: "", end: "" });
    expect(hydrated["ep-2"]?.action).toBe("");
  });
});
