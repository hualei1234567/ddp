import { describe, expect, it } from "vitest";

import {
  annotationSampleSetFixture,
  annotationSchemaFixture,
  annotationSubmissionFixture,
  annotationTaskFixture,
  goldSetFixture,
  iaaReportFixture,
} from "./__fixtures__/annotationFixtures";
import {
  parseAnnotationSampleSet,
  parseAnnotationSchema,
  parseAnnotationTask,
} from "./annotationParsers";
import {
  parseAnnotationSubmission,
  parseGoldSet,
  parseIAAReport,
} from "./annotationResultParsers";
import { ContractValidationError } from "./validation";

describe("标注响应严格解析", () => {
  it("解析 Schema 标签、枚举与快照 Hash", () => {
    const schema = parseAnnotationSchema(annotationSchemaFixture);
    expect(schema.schema_id).toBe(annotationSchemaFixture.schema_id);
    expect(schema.labels.map((label) => label.label_type)).toEqual([
      "ENUM",
      "NUMBER",
      "TIME_RANGE",
    ]);
    expect(schema.schema_sha256).toMatch(/^sha256:/);
  });

  it("未知 Schema 状态拒绝进入 UI", () => {
    expect(() =>
      parseAnnotationSchema({ ...annotationSchemaFixture, status: "UNKNOWN" }),
    ).toThrow(ContractValidationError);
  });

  it("Schema 缺少 Hash 或格式错误时拒绝", () => {
    expect(() =>
      parseAnnotationSchema({ ...annotationSchemaFixture, schema_sha256: "abc" }),
    ).toThrow(ContractValidationError);
  });

  it("解析样本集 Episode 引用与快照统计", () => {
    const sampleSet = parseAnnotationSampleSet(annotationSampleSetFixture);
    expect(sampleSet.episode_refs).toHaveLength(1);
    expect(sampleSet.candidate_count).toBe(2);
    expect(sampleSet.status).toBe("SNAPSHOTTED");
  });

  it("解析任务进度与缺失语义", () => {
    const task = parseAnnotationTask(annotationTaskFixture);
    expect(task.progress.missing_semantics).toBe("NONE");
    expect(task.schema_version).toBe(1);
  });

  it("解析提交记录并校验记录项目/提交一致性", () => {
    const submission = parseAnnotationSubmission(annotationSubmissionFixture);
    expect(submission.records).toHaveLength(1);
    expect(submission.records[0]?.labels[0]?.value).toBe("PICK");
    expect(submission.data_source).toBe("REAL");
  });

  it("提交记录跨项目时拒绝", () => {
    const tampered = {
      ...annotationSubmissionFixture,
      records: [
        {
          ...annotationSubmissionFixture.records[0],
          project_id: "99999999-9999-7999-8999-999999999999",
        },
      ],
    };
    expect(() => parseAnnotationSubmission(tampered)).toThrow(
      ContractValidationError,
    );
  });

  it("黄金集默认视图不包含 expected_labels", () => {
    const gold = parseGoldSet(goldSetFixture);
    expect(gold.items[0]?.answers_redacted).toBe(true);
    expect(gold.items[0]?.expected_labels).toBeUndefined();
  });

  it("黄金集授权视图可解析 expected_labels", () => {
    const answers = parseGoldSet({
      ...goldSetFixture,
      items: [
        {
          ...goldSetFixture.items[0],
          answers_redacted: false,
          expected_labels: [{ label_key: "action", value: "PICK" }],
        },
      ],
    });
    expect(answers.items[0]?.expected_labels?.[0]?.value).toBe("PICK");
  });

  it("IAA 样本不足保留 null 值，不转成 0", () => {
    const report = parseIAAReport(iaaReportFixture);
    expect(report.status).toBe("INSUFFICIENT_DATA");
    expect(report.metrics[0]?.value).toBeNull();
    expect(report.metrics[0]?.numerator).toBeNull();
  });
});
