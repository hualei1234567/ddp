import { describe, expect, it } from "vitest";

import {
  normalizeDescription,
  normalizeSceneLabels,
  parseStepJsonText,
  validateSceneLabels,
} from "./collectionAssetForm";

describe("采集素材表单校验", () => {
  it("空文本表示空数组", () => {
    expect(parseStepJsonText("   ")).toEqual([]);
  });

  it("解析对象数组", () => {
    expect(parseStepJsonText('[{"order":1},{"order":2}]')).toEqual([
      { order: 1 },
      { order: 2 },
    ]);
  });

  it("拒绝非 JSON、非数组与数组内的非对象项", () => {
    expect(() => parseStepJsonText("{bad")).toThrowError(/JSON/);
    expect(() => parseStepJsonText('{"a":1}')).toThrowError(/数组/);
    expect(() => parseStepJsonText("[1]")).toThrowError(/第 1 项必须是对象/);
  });

  it("场景标签去空白、去空项、保序去重", () => {
    expect(normalizeSceneLabels([" 室内 ", "室内", "", "货架"])).toEqual([
      "室内",
      "货架",
    ]);
  });

  it("场景标签上限来自契约（32 个、单个 64 字符）", () => {
    expect(validateSceneLabels([])).toBeNull();
    expect(
      validateSceneLabels(Array.from({ length: 33 }, (_, i) => `标签${i}`)),
    ).toContain("32");
    expect(validateSceneLabels(["x".repeat(65)])).toContain("64");
  });

  it("空描述提交为 null（清空语义由服务端解释）", () => {
    expect(normalizeDescription("   ")).toBeNull();
    expect(normalizeDescription(" 说明 ")).toBe("说明");
  });
});
