import { describe, expect, it } from "vitest";

import { annotationSchemaFixture } from "./__fixtures__/annotationFixtures";
import {
  buildSubmissionRecords,
  validateLabelValue,
} from "./annotationValidation";
import type { LabelDefinition } from "../types/annotation";

const [actionDefinition, confidenceDefinition, segmentDefinition] =
  annotationSchemaFixture.labels;

if (!actionDefinition || !confidenceDefinition || !segmentDefinition) {
  throw new Error("测试夹具缺少标签定义");
}

const multiEnumDefinition: LabelDefinition = {
  label_key: "objects",
  label_type: "MULTI_ENUM",
  required: false,
  level: "EPISODE",
  visibility: "ANNOTATOR",
  description: null,
  value_spec: { enum_values: ["CUP", "PLATE"] },
  quality_rules: [],
};

const booleanDefinition: LabelDefinition = {
  label_key: "success",
  label_type: "BOOLEAN",
  required: false,
  level: "EPISODE",
  visibility: "ANNOTATOR",
  description: null,
  value_spec: {},
  quality_rules: [],
};

const stringDefinition: LabelDefinition = {
  label_key: "note",
  label_type: "STRING",
  required: false,
  level: "EPISODE",
  visibility: "ANNOTATOR",
  description: null,
  value_spec: { max_length: 5 },
  quality_rules: [],
};

const jsonDefinition: LabelDefinition = {
  label_key: "extra",
  label_type: "JSON",
  required: false,
  level: "EPISODE",
  visibility: "ANNOTATOR",
  description: null,
  value_spec: {},
  quality_rules: [],
};

describe("标注表单完整性校验", () => {
  it("必填 ENUM 缺失报错，合法枚举通过", () => {
    expect(validateLabelValue(actionDefinition, "").status).toBe("error");
    expect(validateLabelValue(actionDefinition, "PICK")).toEqual({
      status: "valid",
      value: "PICK",
    });
    expect(validateLabelValue(actionDefinition, "UNKNOWN").status).toBe("error");
  });

  it("可选标签为空时跳过", () => {
    expect(validateLabelValue(confidenceDefinition, "").status).toBe("skip");
    expect(validateLabelValue(segmentDefinition, { start: "", end: "" }).status).toBe(
      "skip",
    );
  });

  it("NUMBER 校验有限数与 min/max", () => {
    expect(validateLabelValue(confidenceDefinition, "0.5")).toEqual({
      status: "valid",
      value: 0.5,
    });
    expect(validateLabelValue(confidenceDefinition, "abc").status).toBe("error");
    expect(validateLabelValue(confidenceDefinition, "1.2").status).toBe("error");
    expect(validateLabelValue(confidenceDefinition, "-0.1").status).toBe("error");
  });

  it("MULTI_ENUM 非空、无重复、必须在枚举内", () => {
    expect(validateLabelValue(multiEnumDefinition, [])).toEqual({
      status: "skip",
    });
    expect(validateLabelValue(multiEnumDefinition, ["CUP", "PLATE"]).status).toBe(
      "valid",
    );
    expect(validateLabelValue(multiEnumDefinition, ["CUP", "CUP"]).status).toBe(
      "error",
    );
    expect(validateLabelValue(multiEnumDefinition, ["BOWL"]).status).toBe(
      "error",
    );
  });

  it("BOOLEAN 不得把空值当成 false", () => {
    expect(validateLabelValue(booleanDefinition, null).status).toBe("skip");
    expect(validateLabelValue(booleanDefinition, false)).toEqual({
      status: "valid",
      value: false,
    });
  });

  it("STRING 校验最大长度", () => {
    expect(validateLabelValue(stringDefinition, "abcd").status).toBe("valid");
    expect(validateLabelValue(stringDefinition, "abcdef").status).toBe("error");
  });

  it("时间片段校验起止、整数与最小时长", () => {
    expect(
      validateLabelValue(segmentDefinition, {
        start: "1000",
        end: "2000",
      }),
    ).toEqual({
      status: "valid",
      value: {
        start_time_ns: 1000,
        end_time_ns: 2000,
        time_domain: "UTC",
      },
    });
    expect(
      validateLabelValue(segmentDefinition, { start: "2000", end: "1000" }).status,
    ).toBe("error");
    expect(
      validateLabelValue(segmentDefinition, { start: "1000", end: "1000" }).status,
    ).toBe("error");
    expect(
      validateLabelValue(segmentDefinition, { start: "1.5", end: "2000" }).status,
    ).toBe("error");
  });

  it("JSON 必须是合法 JSON", () => {
    expect(validateLabelValue(jsonDefinition, '{"a":1}').status).toBe("valid");
    expect(validateLabelValue(jsonDefinition, "{a:1}").status).toBe("error");
  });

  it("生成提交记录时只写入有效标签，并标记 HUMAN/CONFIRMED", () => {
    const built = buildSubmissionRecords(
      annotationSchemaFixture,
      [{ episodeId: "ep-1", episodeVersion: 1 }],
      {
        "ep-1": {
          action: "PICK",
          confidence: "0.8",
          segment: { start: "100", end: "900" },
        },
      },
    );
    expect(built.issues).toEqual([]);
    expect(built.records).toHaveLength(1);
    expect(built.records[0]?.labels).toEqual([
      {
        label_key: "action",
        value: "PICK",
        source: "HUMAN",
        label_status: "CONFIRMED",
      },
      {
        label_key: "confidence",
        value: 0.8,
        source: "HUMAN",
        label_status: "CONFIRMED",
      },
      {
        label_key: "segment",
        value: { start_time_ns: 100, end_time_ns: 900, time_domain: "UTC" },
        source: "HUMAN",
        label_status: "CONFIRMED",
      },
    ]);
  });

  it("必填缺失时阻止生成记录并返回字段定位", () => {
    const built = buildSubmissionRecords(
      annotationSchemaFixture,
      [{ episodeId: "ep-1", episodeVersion: 1 }],
      { "ep-1": { action: "", confidence: "", segment: { start: "", end: "" } } },
    );
    expect(built.records).toHaveLength(0);
    expect(built.issues[0]?.field).toBe("ep-1.action");
  });

  it("没有可选 Episode 时给出记录级错误", () => {
    const built = buildSubmissionRecords(annotationSchemaFixture, [], {});
    expect(built.records).toHaveLength(0);
    expect(built.issues.some((issue) => issue.field === "__record__")).toBe(true);
  });
});
