import { describe, expect, it } from "vitest";

import { ContractValidationError } from "./validation";
import {
  parseQuota,
  parseQuotaList,
  parseUsageEntry,
  parseUsageReport,
} from "./governanceParsers";

const PROJECT_ID = "11111111-2222-4333-8444-555555555555";

function quotaPayload(overrides: Record<string, unknown> = {}) {
  return {
    quota_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    project_id: PROJECT_ID,
    resource_kind: "EPISODES",
    limit: 5000,
    version: 1,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: null,
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

function entryPayload(overrides: Record<string, unknown> = {}) {
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

function reportPayload(entries: unknown[]) {
  return {
    project_id: PROJECT_ID,
    generated_at: "2026-11-17T02:05:00Z",
    entries,
  };
}

describe("配额解析", () => {
  it("解析合法配额并保留版本号", () => {
    const quota = parseQuota(quotaPayload());
    expect(quota.resource_kind).toBe("EPISODES");
    expect(quota.limit).toBe(5000);
    expect(quota.version).toBe(1);
    expect(quota.updated_at).toBeNull();
  });

  it("limit = 0 是合法上限，不被当成缺失", () => {
    expect(parseQuota(quotaPayload({ limit: 0 })).limit).toBe(0);
  });

  it("未知 resource_kind 直接拒绝，不透传给 UI", () => {
    expect(() =>
      parseQuota(quotaPayload({ resource_kind: "CPU_SECONDS" })),
    ).toThrow(ContractValidationError);
  });

  it("超出安全整数范围的上限被拒绝（避免静默丢精度）", () => {
    // 契约上限是 BIGINT（约 9.22e18），已超出 JS 安全整数范围。
    expect(() =>
      parseQuota(quotaPayload({ limit: Number.MAX_SAFE_INTEGER + 2 })),
    ).toThrow(ContractValidationError);
  });

  it("配额列表不补空行，同类型重复直接拒绝", () => {
    expect(parseQuotaList({ items: [] }).items).toHaveLength(0);
    expect(() =>
      parseQuotaList({
        items: [quotaPayload(), quotaPayload({ quota_id: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff" })],
      }),
    ).toThrow(ContractValidationError);
  });

  it("items 不是数组时拒绝", () => {
    expect(() => parseQuotaList({ items: null })).toThrow(
      ContractValidationError,
    );
  });
});

describe("用量条目解析", () => {
  it("used / limit / remaining / exceeded / measured_at 的 null 原样保留", () => {
    const entry = parseUsageEntry(
      entryPayload({
        used: null,
        limit: null,
        remaining: null,
        exceeded: null,
        status: "UNMEASURED",
        measured_at: null,
        measurement_source: null,
      }),
    );

    // 这是本任务的核心契约：null 不能被洗成 0 / false / 当前时间。
    expect(entry.used).toBeNull();
    expect(entry.limit).toBeNull();
    expect(entry.remaining).toBeNull();
    expect(entry.exceeded).toBeNull();
    expect(entry.measured_at).toBeNull();
    expect(entry.measurement_source).toBeNull();
    expect(entry.status).toBe("UNMEASURED");
  });

  it("真实测到的 0 解析为 0，而不是 null", () => {
    const entry = parseUsageEntry(
      entryPayload({ used: 0, limit: 0, remaining: 0, exceeded: false }),
    );
    expect(entry.used).toBe(0);
    expect(entry.limit).toBe(0);
  });

  it("未知 status 直接拒绝，不默认成功", () => {
    expect(() => parseUsageEntry(entryPayload({ status: "UNKNOWN" }))).toThrow(
      ContractValidationError,
    );
  });

  it("负数用量被拒绝", () => {
    expect(() => parseUsageEntry(entryPayload({ used: -1 }))).toThrow(
      ContractValidationError,
    );
  });
});

describe("用量报表解析", () => {
  it("保留服务端条目顺序", () => {
    const report = parseUsageReport(
      reportPayload([
        entryPayload({ resource_kind: "OBJECT_STORAGE_BYTES", status: "UNLIMITED", limit: null, remaining: null, exceeded: null }),
        entryPayload(),
      ]),
    );
    expect(report.entries.map((entry) => entry.resource_kind)).toEqual([
      "OBJECT_STORAGE_BYTES",
      "EPISODES",
    ]);
    expect(report.generated_at).toBe("2026-11-17T02:05:00Z");
  });

  it("缺少 generated_at 直接拒绝（不用本地时间兜底）", () => {
    expect(() =>
      parseUsageReport({ project_id: PROJECT_ID, entries: [] }),
    ).toThrow(ContractValidationError);
  });

  it("重复资源类型直接拒绝", () => {
    expect(() =>
      parseUsageReport(reportPayload([entryPayload(), entryPayload()])),
    ).toThrow(ContractValidationError);
  });

  it("project_id 非 UUID 时拒绝", () => {
    expect(() =>
      parseUsageReport({
        project_id: "not-a-uuid",
        generated_at: "2026-11-17T02:05:00Z",
        entries: [],
      }),
    ).toThrow(ContractValidationError);
  });

  it("真实后端响应（快照未生成 → 全部未测量）可解析且不产生 0", () => {
    // 2026-11-17 本地后端 `GET /usage-report` 的实测响应，原样作为回归样本。
    const live = {
      project_id: PROJECT_ID,
      generated_at: "2026-11-16T20:05:45.217166Z",
      entries: [
        "OBJECT_STORAGE_BYTES",
        "EPISODES",
        "DATASET_VERSIONS",
        "INGESTION_JOBS",
        "INFERENCE_TASKS",
      ].map((kind) => ({
        resource_kind: kind,
        used: null,
        limit: null,
        remaining: null,
        exceeded: null,
        status: "UNMEASURED",
        measurement_source: null,
        measured_at: null,
      })),
    };

    const report = parseUsageReport(live);

    expect(report.entries).toHaveLength(5);
    for (const entry of report.entries) {
      expect(entry.used).toBeNull();
      expect(entry.status).toBe("UNMEASURED");
      expect(entry.measured_at).toBeNull();
    }
  });
});
