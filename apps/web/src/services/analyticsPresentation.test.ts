import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  describeQuotaWriteError,
  filterUsageRows,
  formatBytes,
  formatCount,
  formatUsageQuantity,
  mergeQuotaVersions,
  presentExceeded,
  presentLimit,
  presentQuotaStatus,
  presentRemaining,
  presentUsed,
  resourceKindLabel,
  validateQuotaLimit,
  type UsageRow,
} from "./analyticsPresentation";
import type { Quota, ResourceKind, UsageEntry } from "../types/governance";

const PROJECT_ID = "11111111-2222-4333-8444-555555555555";

function entry(overrides: Partial<UsageEntry> = {}): UsageEntry {
  return {
    resource_kind: "EPISODES",
    used: 12,
    limit: 5000,
    remaining: 4988,
    exceeded: false,
    status: "OK",
    measurement_source: "episodes",
    measured_at: "2026-11-17T02:00:00Z",
    ...overrides,
  };
}

function quota(resourceKind: ResourceKind, version: number): Quota {
  return {
    quota_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    project_id: PROJECT_ID,
    resource_kind: resourceKind,
    limit: 100,
    version,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
  };
}

describe("用量格式化", () => {
  it("字节按二进制单位格式化", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MiB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GiB");
  });

  it("计数用千分位分组", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(1234)).toBe("1,234");
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  it("只有 OBJECT_STORAGE_BYTES 走字节单位", () => {
    expect(formatUsageQuantity("OBJECT_STORAGE_BYTES", 2048)).toBe("2.0 KiB");
    expect(formatUsageQuantity("EPISODES", 2048)).toBe("2,048");
  });

  it("非法值不显示成 0", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatCount(Number.NaN)).toBe("—");
  });

  it("未知资源类型原样展示", () => {
    expect(resourceKindLabel("CPU_SECONDS")).toBe("CPU_SECONDS");
    expect(resourceKindLabel("EPISODES")).toBe("Episode");
  });
});

describe("三态呈现：未测量 / 未配置上限 / 无法判断", () => {
  it("used = null 显示「未测量」，绝不显示 0", () => {
    const unmeasured = entry({
      used: null,
      limit: null,
      remaining: null,
      exceeded: null,
      status: "UNMEASURED",
      measured_at: null,
      measurement_source: null,
    });

    expect(presentUsed(unmeasured)).toBe("未测量");
    expect(presentUsed(unmeasured)).not.toBe("0");
    expect(presentUsed(unmeasured)).not.toContain("0 B");
    expect(presentLimit(unmeasured)).toBe("未配置上限");
    expect(presentRemaining(unmeasured)).toBe("无法计算");
    expect(presentExceeded(unmeasured)).toBe("无法判断");
  });

  it("used = 0 显示真实的 0（与未测量区分）", () => {
    const zero = entry({ used: 0, limit: 0, remaining: 0, exceeded: false });
    expect(presentUsed(zero)).toBe("0");
    expect(presentLimit(zero)).toBe("0");
    expect(presentRemaining(zero)).toBe("0");
  });

  it("字节型未测量时也不出现 0 B", () => {
    const bytes = entry({
      resource_kind: "OBJECT_STORAGE_BYTES",
      used: null,
      limit: null,
      remaining: null,
      exceeded: null,
      status: "UNMEASURED",
    });
    expect(presentUsed(bytes)).toBe("未测量");
    expect(presentUsed(bytes)).not.toContain("0 B");
    expect(presentLimit(bytes)).toBe("未配置上限");
  });

  it("超限时剩余量展示服务端截断后的 0，并标记已超限", () => {
    const over = entry({
      used: 6000,
      limit: 5000,
      remaining: 0,
      exceeded: true,
      status: "OVER_LIMIT",
    });
    expect(presentRemaining(over)).toBe("0");
    expect(presentExceeded(over)).toBe("已超限");
  });

  it("OVerLimit 与 AT_LIMIT 用不同颜色", () => {
    expect(presentQuotaStatus("OVER_LIMIT").color).toBe("red");
    expect(presentQuotaStatus("AT_LIMIT").color).toBe("orange");
  });

  it("UNMEASURED 不得使用绿色或「正常」文案", () => {
    const unmeasured = presentQuotaStatus("UNMEASURED");
    const ok = presentQuotaStatus("OK");

    expect(unmeasured.label).toBe("未测量");
    expect(unmeasured.color).not.toBe("green");
    expect(unmeasured.label).not.toBe(ok.label);
    expect(unmeasured).not.toEqual(ok);
  });

  it("UNLIMITED 单独一档，不与 OK 合并", () => {
    expect(presentQuotaStatus("UNLIMITED").label).toBe("未设上限");
    expect(presentQuotaStatus("UNLIMITED").color).not.toBe(
      presentQuotaStatus("OK").color,
    );
  });

  it("未知状态原样展示", () => {
    expect(presentQuotaStatus("SOMETHING_NEW")).toEqual({
      label: "SOMETHING_NEW",
      color: "default",
    });
  });
});

describe("配额版本合并", () => {
  it("按资源类型挂上已配置配额的版本", () => {
    const rows = mergeQuotaVersions(
      [entry(), entry({ resource_kind: "DATASET_VERSIONS" })],
      [quota("EPISODES", 3)],
    );

    expect(rows[0]?.quotaVersion).toBe(3);
    // 未配置上限的类型没有条目 → 版本为 null（新建时无需 If-Match）。
    expect(rows[1]?.quotaVersion).toBeNull();
  });

  it("配额列表为空时全部版本为 null", () => {
    const rows = mergeQuotaVersions([entry()], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.quotaVersion).toBeNull();
  });
});

describe("状态筛选", () => {
  const rows: UsageRow[] = mergeQuotaVersions(
    [
      entry(),
      entry({
        resource_kind: "DATASET_VERSIONS",
        used: null,
        status: "UNMEASURED",
        limit: null,
        remaining: null,
        exceeded: null,
      }),
    ],
    [],
  );

  it("不传状态时返回全部", () => {
    expect(filterUsageRows(rows, undefined)).toHaveLength(2);
  });

  it("按状态过滤，未测量单独一档", () => {
    expect(filterUsageRows(rows, "UNMEASURED")).toHaveLength(1);
    expect(filterUsageRows(rows, "OK")).toHaveLength(1);
    expect(filterUsageRows(rows, "OVER_LIMIT")).toHaveLength(0);
  });
});

describe("配额写入失败反馈", () => {
  it("403 如实说明服务端拒绝，不假装没有该操作", () => {
    const failure = describeQuotaWriteError(
      new ApiError({ kind: "http", status: 403, message: "无权", code: "forbidden" }),
    );
    expect(failure.message).toContain("403");
    expect(failure.refreshRequired).toBe(false);
  });

  it("409 提示版本冲突并要求刷新", () => {
    const failure = describeQuotaWriteError(
      new ApiError({ kind: "http", status: 409, message: "conflict", code: "conflict" }),
    );
    expect(failure.message).toContain("409");
    expect(failure.refreshRequired).toBe(true);
  });

  it("428 提示缺少 If-Match 并要求刷新", () => {
    const failure = describeQuotaWriteError(
      new ApiError({ kind: "http", status: 428, message: "precondition" }),
    );
    expect(failure.message).toContain("If-Match");
    expect(failure.refreshRequired).toBe(true);
  });

  it("其它错误透传服务端消息", () => {
    const failure = describeQuotaWriteError(
      new ApiError({ kind: "http", status: 422, message: "limit 不能为负" }),
    );
    expect(failure.message).toBe("limit 不能为负");
    expect(failure.refreshRequired).toBe(false);
  });
});

describe("上限输入校验", () => {
  it("0 合法，空值不合法", () => {
    expect(validateQuotaLimit(0)).toBeNull();
    expect(validateQuotaLimit(5000)).toBeNull();
    expect(validateQuotaLimit(null)).not.toBeNull();
    expect(validateQuotaLimit(undefined)).not.toBeNull();
  });

  it("小数、负数、超安全整数被拒绝", () => {
    expect(validateQuotaLimit(1.5)).not.toBeNull();
    expect(validateQuotaLimit(-1)).not.toBeNull();
    expect(validateQuotaLimit(Number.MAX_SAFE_INTEGER + 1)).not.toBeNull();
  });
});
