import { describe, expect, it } from "vitest";

import {
  GOLD_SET_CODE_MAX,
  GOLD_SET_NAME_MAX,
  METRIC_DEFINITIONS_VERSION_MAX,
  parseObjectArrayJson,
  parseObjectJson,
  validateExposurePolicy,
  validateGoldSetCode,
  validateGoldSetItems,
  validateGoldSetName,
  validateIouThreshold,
  validateMaxExposure,
  validateMetricDefinitionsVersion,
  validateRotationPolicy,
} from "./qualityForm";

describe("黄金项 JSON 解析（契约只声明「对象数组、至少 1 项」）", () => {
  it("空文本视为空数组，由 items 数量校验拦截", () => {
    const items = parseObjectArrayJson("", "黄金项");

    expect(items).toEqual([]);
    expect(validateGoldSetItems(items)).toBe("至少需要 1 个黄金项");
  });

  it("合法对象数组原样返回", () => {
    const items = parseObjectArrayJson(
      '[{"episode_id":"e-1"},{"episode_id":"e-2"}]',
      "黄金项",
    );

    expect(items).toHaveLength(2);
    expect(validateGoldSetItems(items)).toBeNull();
  });

  it("非 JSON 报错", () => {
    expect(() => parseObjectArrayJson("{oops", "黄金项")).toThrow(
      /不是合法的 JSON/,
    );
  });

  it("不是数组时报错", () => {
    expect(() => parseObjectArrayJson('{"a":1}', "黄金项")).toThrow(
      /必须是 JSON 数组/,
    );
  });

  it("数组元素不是对象时报错并指出第几项", () => {
    expect(() => parseObjectArrayJson("[1]", "黄金项")).toThrow(/第 1 项/);
    expect(() => parseObjectArrayJson("[[]]", "黄金项")).toThrow(/第 1 项/);
  });
});

describe("使用策略 JSON 解析", () => {
  it("空文本返回空对象（契约 default {}）", () => {
    expect(parseObjectJson("  ", "使用策略")).toEqual({});
  });

  it("对象正常解析，数组与标量拒绝", () => {
    expect(parseObjectJson('{"a":1}', "使用策略")).toEqual({ a: 1 });
    expect(() => parseObjectJson("[]", "使用策略")).toThrow(/必须是 JSON 对象/);
    expect(() => parseObjectJson("3", "使用策略")).toThrow(/必须是 JSON 对象/);
  });
});

describe("黄金集字段校验", () => {
  it("编码必填且不超过契约上限", () => {
    expect(validateGoldSetCode("")).toBe("请输入编码");
    expect(validateGoldSetCode("  ")).toBe("请输入编码");
    expect(validateGoldSetCode("GOLD-001")).toBeNull();
    expect(validateGoldSetCode("x".repeat(GOLD_SET_CODE_MAX + 1))).toContain(
      String(GOLD_SET_CODE_MAX),
    );
  });

  it("名称可选但不超过 200 字符", () => {
    expect(validateGoldSetName(undefined)).toBeNull();
    expect(validateGoldSetName("x".repeat(GOLD_SET_NAME_MAX + 1))).toContain(
      String(GOLD_SET_NAME_MAX),
    );
  });

  it("每人曝光上限必须是 ≥1 的整数", () => {
    expect(validateMaxExposure(undefined)).not.toBeNull();
    expect(validateMaxExposure(0)).not.toBeNull();
    expect(validateMaxExposure(1.5)).not.toBeNull();
    expect(validateMaxExposure(1)).toBeNull();
  });

  it("曝光与轮换策略只接受契约枚举", () => {
    expect(validateExposurePolicy("HIDDEN")).toBeNull();
    expect(validateExposurePolicy("PUBLIC")).toContain("曝光策略");
    expect(validateRotationPolicy("PER_TASK")).toBeNull();
    expect(validateRotationPolicy("DAILY")).toContain("轮换策略");
  });
});

describe("IAA 表单校验", () => {
  it("指标口径版本必填且不超过 64 字符", () => {
    expect(validateMetricDefinitionsVersion("")).not.toBeNull();
    expect(validateMetricDefinitionsVersion("ddp.stage3.iaa/1")).toBeNull();
    expect(
      validateMetricDefinitionsVersion(
        "x".repeat(METRIC_DEFINITIONS_VERSION_MAX + 1),
      ),
    ).not.toBeNull();
  });

  it("IoU 阈值必须在 0 到 1 之间，留空交给契约默认值", () => {
    expect(validateIouThreshold(null)).toBeNull();
    expect(validateIouThreshold(0)).toBeNull();
    expect(validateIouThreshold(1)).toBeNull();
    expect(validateIouThreshold(-0.1)).not.toBeNull();
    expect(validateIouThreshold(1.5)).not.toBeNull();
  });
});
