import { describe, expect, it } from "vitest";

import {
  buildEvidenceJumpTarget,
  buildReworkContext,
  describeEvidenceLocatability,
  findingNeedsEvidence,
  formatRelativeSeconds,
  parseNanoseconds,
  relativeRangeFromNs,
} from "./qualityEvidence";
import { qualityRunFindingFixture } from "./__fixtures__/qualityRunFixtures";

/**
 * V51-010 证据定位换算测试。
 *
 * 这一层的错误代价最高：换算错会让播放器跳到错误时刻，用户会以为看到了证据。
 * 因此重点覆盖边界——越界裁剪、不相交、非法输入、超过 2^53 的纳秒。
 */

// 2025-11-24T00:00:00Z 附近；远大于 2^53 = 9007199254740992
const EPISODE_START = "1763510400000000000";
const EPISODE_END = "1763510460000000000"; // +60s

describe("parseNanoseconds", () => {
  it("解析十进制字符串为 bigint，避免大数丢精度", () => {
    expect(parseNanoseconds(EPISODE_START)).toBe(1763510400000000000n);
  });

  it("接受安全整数，拒绝超出安全范围的 number", () => {
    expect(parseNanoseconds(1_000)).toBe(1_000n);
    // 2^53+1 用 number 表达已被改值，必须拒绝而不是静默接受。
    expect(parseNanoseconds(Number.MAX_SAFE_INTEGER + 1)).toBeNull();
  });

  it("拒绝非十进制形态", () => {
    for (const bad of ["1e9", "0x10", "", " 12.5 ", "abc"]) {
      expect(parseNanoseconds(bad)).toBeNull();
    }
  });

  it("null 返回 null（未提供不等于 0）", () => {
    expect(parseNanoseconds(null)).toBeNull();
  });
});

describe("relativeRangeFromNs", () => {
  it("把绝对纳秒区间换算成相对 Episode 起点的秒", () => {
    const result = relativeRangeFromNs(
      {
        start_time_ns: "1763510402000000000",
        end_time_ns: "1763510405500000000",
      },
      EPISODE_START,
      EPISODE_END,
    );

    expect(result).toEqual({ startSeconds: 2, endSeconds: 5.5 });
  });

  it("区间超出 Episode 时裁剪到边界", () => {
    const result = relativeRangeFromNs(
      {
        start_time_ns: "1763510399000000000", // 比 Episode 起点早 1s
        end_time_ns: "1763510461000000000", // 比终点晚 1s
      },
      EPISODE_START,
      EPISODE_END,
    );

    expect(result).toEqual({ startSeconds: 0, endSeconds: 60 });
  });

  it("与 Episode 完全不相交时返回 null，不画贴边假区间", () => {
    const result = relativeRangeFromNs(
      {
        start_time_ns: "1763510500000000000",
        end_time_ns: "1763510501000000000",
      },
      EPISODE_START,
      EPISODE_END,
    );

    expect(result).toBeNull();
  });

  it("缺 Episode 边界时返回 null", () => {
    const range = {
      start_time_ns: "1763510402000000000",
      end_time_ns: "1763510405500000000",
    };

    expect(relativeRangeFromNs(range, null, EPISODE_END)).toBeNull();
    expect(relativeRangeFromNs(range, EPISODE_START, null)).toBeNull();
  });

  it("倒置或零宽区间返回 null", () => {
    expect(
      relativeRangeFromNs(
        { start_time_ns: "1763510405000000000", end_time_ns: "1763510402000000000" },
        EPISODE_START,
        EPISODE_END,
      ),
    ).toBeNull();
    expect(
      relativeRangeFromNs(
        { start_time_ns: "1763510402000000000", end_time_ns: "1763510402000000000" },
        EPISODE_START,
        EPISODE_END,
      ),
    ).toBeNull();
  });
});

describe("buildEvidenceJumpTarget", () => {
  it("时间区间同时给出跳转位置与高亮区间", () => {
    const target = buildEvidenceJumpTarget(
      {
        time_range: {
          start_time_ns: "1763510401500000000",
          end_time_ns: "1763510403000000000",
        },
        stream_id: "wrist",
        field_path: "streams",
      },
      EPISODE_START,
      EPISODE_END,
    );

    expect(target.locatable).toBe(true);
    expect(target.seekSeconds).toBe(1.5);
    expect(target.highlight).toEqual({ startSeconds: 1.5, endSeconds: 3 });
    expect(target.streamId).toBe("wrist");
    expect(target.fieldPath).toBe("streams");
  });

  it("只有字段路径时不跳转播放器，但仍可定位", () => {
    const target = buildEvidenceJumpTarget(
      { time_range: null, stream_id: null, field_path: "labels.action" },
      EPISODE_START,
      EPISODE_END,
    );

    expect(target.locatable).toBe(true);
    expect(target.seekSeconds).toBeNull();
    expect(target.highlight).toBeNull();
    expect(target.fieldPath).toBe("labels.action");
  });

  it("没有定位时 locatable=false 且不猜测位置", () => {
    const target = buildEvidenceJumpTarget(null, EPISODE_START, EPISODE_END);

    expect(target.locatable).toBe(false);
    expect(target.seekSeconds).toBeNull();
    expect(target.highlight).toBeNull();
    expect(target.streamId).toBeNull();
    expect(target.fieldPath).toBeNull();
  });
});

describe("describeEvidenceLocatability", () => {
  it("无法定位时如实说明，而不是留空", () => {
    const target = buildEvidenceJumpTarget(null, EPISODE_START, EPISODE_END);

    expect(describeEvidenceLocatability(target)).toContain("没有可定位");
  });

  it("给出区间、流与字段的可读描述", () => {
    const target = buildEvidenceJumpTarget(
      {
        time_range: {
          start_time_ns: "1763510401500000000",
          end_time_ns: "1763510403000000000",
        },
        stream_id: "wrist",
        field_path: "streams",
      },
      EPISODE_START,
      EPISODE_END,
    );

    const text = describeEvidenceLocatability(target);
    expect(text).toContain("00:01.5");
    expect(text).toContain("00:03.0");
    expect(text).toContain("wrist");
    expect(text).toContain("streams");
  });
});

describe("findingNeedsEvidence", () => {
  it("只有 FAIL / ERROR 需要证据定位", () => {
    expect(findingNeedsEvidence(qualityRunFindingFixture({ status: "FAIL" }))).toBe(true);
    expect(findingNeedsEvidence(qualityRunFindingFixture({ status: "ERROR" }))).toBe(true);
    expect(findingNeedsEvidence(qualityRunFindingFixture({ status: "PASS" }))).toBe(false);
    expect(
      findingNeedsEvidence(qualityRunFindingFixture({ status: "NOT_APPLICABLE" })),
    ).toBe(false);
  });
});

describe("buildReworkContext", () => {
  it("原因包含规则版本、严重级别与证据位置，但不含自动结论", () => {
    const finding = qualityRunFindingFixture({
      rule_id: "number_label_within_range",
      rule_version: "1",
      severity: "ERROR",
      status: "FAIL",
      message: "1 个数值标签越界：velocity",
    });

    const context = buildReworkContext(finding, "字段 labels.velocity");

    expect(context.ruleId).toBe("number_label_within_range");
    expect(context.ruleVersion).toBe("1");
    expect(context.findingId).toBe(finding.finding_id);
    expect(context.reason).toContain("number_label_within_range@1");
    expect(context.reason).toContain("字段 labels.velocity");
    // 只提供证据与建议，不写"必须返工"这类代替 Reviewer 的结论。
    expect(context.reason).not.toContain("必须");
  });

  it("超长原因被截断到服务端 2000 字符上限内", () => {
    const finding = qualityRunFindingFixture({ message: "x".repeat(3000) });

    const context = buildReworkContext(finding, "字段 labels.action");

    expect(context.reason.length).toBeLessThanOrEqual(2000);
    expect(context.reason.endsWith("…")).toBe(true);
  });

  it("没有证据位置时原因里不出现空的证据描述", () => {
    const finding = qualityRunFindingFixture({ message: "同步超差" });

    const context = buildReworkContext(finding, "");

    expect(context.reason).not.toContain("证据位置");
  });
});

describe("formatRelativeSeconds", () => {
  it("按 mm:ss.s 格式化，与播放器时钟同口径", () => {
    expect(formatRelativeSeconds(0)).toBe("00:00.0");
    // 与既有 formatPlaybackClock 同口径（toFixed(1)，0.25 落到 .3）。
    expect(formatRelativeSeconds(63.25)).toBe("01:03.3");
  });

  it("非法与负数不会渲染成负时钟", () => {
    expect(formatRelativeSeconds(Number.NaN)).toBe("--:--.-");
    expect(formatRelativeSeconds(-5)).toBe("00:00.0");
  });
});
