import { describe, expect, it } from "vitest";

import {
  parseBatch,
  parseLedgerEntry,
  parseSegment,
  parseSupplier,
  parseSupplierMetrics,
} from "./supplierParsers";
import { ContractValidationError } from "./validation";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const SUPPLIER = "22222222-2222-4222-8222-222222222222";
const BATCH = "33333333-3333-4333-8333-333333333333";
const SEGMENT = "44444444-4444-4444-8444-444444444444";
const USER = "55555555-5555-4555-8555-555555555555";

const NOW = "2026-11-14T00:00:00Z";

describe("阶段 6 供应商响应解析", () => {
  it("解析供应商并保留服务端动作", () => {
    const supplier = parseSupplier({
      supplier_id: SUPPLIER,
      project_id: PROJECT,
      code: "SUP-001",
      name: "示例供应商",
      category: "DATA_COLLECTION",
      status: "ACTIVE",
      contact_name: null,
      contact_email: null,
      external_ref: null,
      notes: null,
      allowed_actions: ["suspend", "terminate"],
      version: 2,
      created_at: NOW,
      updated_at: NOW,
    });
    expect(supplier.allowed_actions).toEqual(["suspend", "terminate"]);
    expect(supplier.version).toBe(2);
  });

  it("供应商缺少必填字段时抛契约错误", () => {
    expect(() =>
      parseSupplier({
        supplier_id: SUPPLIER,
        project_id: PROJECT,
        code: "SUP-001",
      } as unknown),
    ).toThrow(ContractValidationError);
  });

  it("绩效缺失值保持 null，绝不折叠为 0", () => {
    const metrics = parseSupplierMetrics({
      batch_count: 3,
      settled_batch_count: 0,
      on_time_rate: null,
      first_pass_rate: null,
      rework_rate: null,
      average_cycle_hours: null,
      accepted_segments: 0,
      decided_segments: 0,
      segment_pass_rate: null,
    });
    expect(metrics.on_time_rate).toBeNull();
    expect(metrics.first_pass_rate).toBeNull();
    expect(metrics.segment_pass_rate).toBeNull();
  });

  it("解析到货批次与片段诊断快照", () => {
    const batch = parseBatch({
      batch_id: BATCH,
      project_id: PROJECT,
      supplier_id: SUPPLIER,
      batch_code: "20261020_01",
      title: null,
      status: "SCREENING",
      planned_segment_count: 2,
      received_segment_count: 2,
      planned_arrival_date: NOW,
      arrived_at: NOW,
      settled_at: null,
      rework_count: 0,
      storage_prefix: "oss://supplier/20261020_01/",
      notes: null,
      allowed_actions: ["finalize"],
      version: 3,
      created_at: NOW,
      updated_at: NOW,
    });
    expect(batch.status).toBe("SCREENING");

    const segment = parseSegment({
      segment_id: SEGMENT,
      project_id: PROJECT,
      batch_id: BATCH,
      segment_code: "seg-0001",
      status: "PENDING_REVIEW",
      raw_asset_id: null,
      duration_ns: 1_159_000_000_000,
      task_name: "采集青椒",
      diagnostics: { vlm_summary: "双人有效操作", hand_ratio: 0.873 },
      decision: null,
      decision_reason: null,
      decided_by: null,
      decided_at: null,
      registered_by: USER,
      allowed_actions: [],
      version: 2,
      created_at: NOW,
      updated_at: NOW,
    });
    expect(segment.diagnostics.hand_ratio).toBe(0.873);
    expect(segment.raw_asset_id).toBeNull();
  });

  it("解析账本条目允许供应商级事件没有批次", () => {
    const entry = parseLedgerEntry({
      ledger_id: "66666666-6666-4666-8666-666666666666",
      project_id: PROJECT,
      batch_id: null,
      supplier_id: SUPPLIER,
      resource_type: "SUPPLIER",
      resource_id: SUPPLIER,
      action: "activate",
      from_status: "DRAFT",
      to_status: "ACTIVE",
      actor_id: USER,
      request_id: "req-1",
      reason: null,
      snapshot: {},
      occurred_at: NOW,
    });
    expect(entry.batch_id).toBeNull();
    expect(entry.to_status).toBe("ACTIVE");
  });
});
