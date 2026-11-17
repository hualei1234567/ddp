import { describe, expect, it } from "vitest";

import {
  emptyLabelFormValues,
  formValuesToLabels,
  labelsToFormValues,
} from "./annotationSchemaForm";

const label = {
  label_key: "action",
  label_type: "ENUM" as const,
  required: true,
  level: "EPISODE" as const,
  visibility: "ANNOTATOR" as const,
  description: "动作",
  value_spec: { options: ["pick", "place"] },
  quality_rules: ["must_not_be_empty"],
};

describe("Schema 标签表单转换", () => {
  it("领域标签转表单并回写后保持一致", () => {
    const values = labelsToFormValues([label]);
    expect(values[0]?.value_spec_text).toBe('{"options":["pick","place"]}');
    expect(formValuesToLabels(values)).toEqual([label]);
  });

  it("空 value_spec 视为空对象", () => {
    const values = [{ ...emptyLabelFormValues(), label_key: "note" }];
    expect(formValuesToLabels(values)[0]?.value_spec).toEqual({});
  });

  it("非法 JSON 直接报错，不静默吞掉", () => {
    const values = [
      { ...emptyLabelFormValues(), label_key: "note", value_spec_text: "{oops" },
    ];
    expect(() => formValuesToLabels(values)).toThrow(/不是合法 JSON/);
  });

  it("值定义必须是对象", () => {
    const values = [
      { ...emptyLabelFormValues(), label_key: "note", value_spec_text: "[1,2]" },
    ];
    expect(() => formValuesToLabels(values)).toThrow(/必须是 JSON 对象/);
  });

  it("标签键重复与空标签集被拒绝", () => {
    const values = [
      { ...emptyLabelFormValues(), label_key: "x" },
      { ...emptyLabelFormValues(), label_key: "x" },
    ];
    expect(() => formValuesToLabels(values)).toThrow(/重复/);
    expect(() => formValuesToLabels([])).toThrow(/至少需要一个/);
  });
});
