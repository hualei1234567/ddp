import { describe, expect, it } from "vitest";

import {
  annotationTaskFixture,
  annotationSchemaFixture,
  workbenchDataFixture,
} from "./__fixtures__/annotationFixtures";
import {
  buildWorkbenchRecords,
  hasHiddenRequiredLabels,
  schemaSnapshotMatches,
} from "./annotationWorkbench";

describe("工作台提交载荷与快照", () => {
  it("按 Episode 生成提交记录并转换标签值", () => {
    const built = buildWorkbenchRecords(workbenchDataFixture, {
      "55555555-5555-7555-8555-555555555555": {
        action: "PICK",
        confidence: "0.75",
        segment: { start: "10", end: "20" },
      },
    });
    expect(built.issues).toEqual([]);
    expect(built.records).toHaveLength(1);
    expect(built.records[0]?.episode_version).toBe(1);
    expect(built.records[0]?.labels.map((label) => label.value)).toEqual([
      "PICK",
      0.75,
      { start_time_ns: 10, end_time_ns: 20, time_domain: "UTC" },
    ]);
  });

  it("必填标签缺失时不生成记录", () => {
    const built = buildWorkbenchRecords(workbenchDataFixture, {
      "55555555-5555-7555-8555-555555555555": { action: "" },
    });
    expect(built.records).toHaveLength(0);
    expect(built.issues[0]?.field).toBe(
      "55555555-5555-7555-8555-555555555555.action",
    );
  });

  it("Schema 快照与任务冻结值不一致时识别", () => {
    expect(schemaSnapshotMatches(annotationTaskFixture, annotationSchemaFixture)).toBe(
      true,
    );
    expect(
      schemaSnapshotMatches(
        { ...annotationTaskFixture, schema_sha256: "sha256:" + "e".repeat(64) },
        annotationSchemaFixture,
      ),
    ).toBe(false);
    expect(
      schemaSnapshotMatches(annotationTaskFixture, {
        ...annotationSchemaFixture,
        status: "DRAFT",
      }),
    ).toBe(false);
  });

  it("识别当前角色不可见但必填的标签", () => {
    expect(hasHiddenRequiredLabels(annotationSchemaFixture)).toBe(false);
    expect(
      hasHiddenRequiredLabels({
        ...annotationSchemaFixture,
        labels: [
          ...annotationSchemaFixture.labels,
          {
            label_key: "secret",
            label_type: "STRING",
            required: true,
            level: "EPISODE",
            visibility: "GOLD_ONLY",
            description: null,
            value_spec: {},
            quality_rules: [],
          },
        ],
      }),
    ).toBe(true);
  });
});
