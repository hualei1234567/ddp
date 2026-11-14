/** 阶段 6 供应商域响应解析器。 */

import type {
  AcceptanceLedgerEntry,
  DeliveryBatch,
  InboundSegment,
  Supplier,
  SupplierMetrics,
} from "../types/supplier";
import {
  asRecord,
  parseCursorPage,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readStringArray,
  readUuid,
} from "./validation";

export function parseSupplier(value: unknown): Supplier {
  const record = asRecord(value, "supplier");
  return {
    supplier_id: readUuid(record, "supplier_id", "supplier"),
    project_id: readUuid(record, "project_id", "supplier"),
    code: readString(record, "code", "supplier"),
    name: readString(record, "name", "supplier"),
    category: readString(record, "category", "supplier"),
    status: readString(record, "status", "supplier"),
    contact_name: readOptionalString(record, "contact_name", "supplier"),
    contact_email: readOptionalString(record, "contact_email", "supplier"),
    external_ref: readOptionalString(record, "external_ref", "supplier"),
    notes: readOptionalString(record, "notes", "supplier"),
    allowed_actions: readStringArray(record, "allowed_actions", "supplier"),
    version: readPositiveInteger(record, "version", "supplier"),
    created_at: readString(record, "created_at", "supplier"),
    updated_at: readString(record, "updated_at", "supplier"),
  };
}

export function parseSupplierPage(value: unknown) {
  return parseCursorPage(value, "suppliers", parseSupplier);
}

export function parseSupplierMetrics(value: unknown): SupplierMetrics {
  const record = asRecord(value, "supplierMetrics");
  return {
    batch_count: readInteger(record, "batch_count", "supplierMetrics"),
    settled_batch_count: readInteger(record, "settled_batch_count", "supplierMetrics"),
    // null 表示"无数据"，解析层不得把 null 折叠成 0。
    on_time_rate: readOptionalNumber(record, "on_time_rate", "supplierMetrics"),
    first_pass_rate: readOptionalNumber(record, "first_pass_rate", "supplierMetrics"),
    rework_rate: readOptionalNumber(record, "rework_rate", "supplierMetrics"),
    average_cycle_hours: readOptionalNumber(
      record,
      "average_cycle_hours",
      "supplierMetrics",
    ),
    accepted_segments: readInteger(record, "accepted_segments", "supplierMetrics"),
    decided_segments: readInteger(record, "decided_segments", "supplierMetrics"),
    segment_pass_rate: readOptionalNumber(
      record,
      "segment_pass_rate",
      "supplierMetrics",
    ),
  };
}

export function parseBatch(value: unknown): DeliveryBatch {
  const record = asRecord(value, "batch");
  return {
    batch_id: readUuid(record, "batch_id", "batch"),
    project_id: readUuid(record, "project_id", "batch"),
    supplier_id: readUuid(record, "supplier_id", "batch"),
    batch_code: readString(record, "batch_code", "batch"),
    title: readOptionalString(record, "title", "batch"),
    status: readString(record, "status", "batch"),
    planned_segment_count: readInteger(record, "planned_segment_count", "batch"),
    received_segment_count: readInteger(record, "received_segment_count", "batch"),
    planned_arrival_date: readOptionalString(record, "planned_arrival_date", "batch"),
    arrived_at: readOptionalString(record, "arrived_at", "batch"),
    settled_at: readOptionalString(record, "settled_at", "batch"),
    rework_count: readInteger(record, "rework_count", "batch"),
    storage_prefix: readOptionalString(record, "storage_prefix", "batch"),
    notes: readOptionalString(record, "notes", "batch"),
    allowed_actions: readStringArray(record, "allowed_actions", "batch"),
    version: readPositiveInteger(record, "version", "batch"),
    created_at: readString(record, "created_at", "batch"),
    updated_at: readString(record, "updated_at", "batch"),
  };
}

export function parseBatchPage(value: unknown) {
  return parseCursorPage(value, "batches", parseBatch);
}

function readRecord(
  record: Record<string, unknown>,
  key: string,
  path: string,
): Record<string, unknown> {
  const value = record[key];
  if (value === undefined || value === null) {
    return {};
  }
  return asRecord(value, `${path}.${key}`);
}

export function parseSegment(value: unknown): InboundSegment {
  const record = asRecord(value, "segment");
  return {
    segment_id: readUuid(record, "segment_id", "segment"),
    project_id: readUuid(record, "project_id", "segment"),
    batch_id: readUuid(record, "batch_id", "segment"),
    segment_code: readString(record, "segment_code", "segment"),
    status: readString(record, "status", "segment"),
    raw_asset_id: readOptionalUuid(record, "raw_asset_id", "segment"),
    duration_ns: record.duration_ns === null ? null : readInteger(record, "duration_ns", "segment"),
    task_name: readOptionalString(record, "task_name", "segment"),
    diagnostics: readRecord(record, "diagnostics", "segment"),
    decision: readOptionalString(record, "decision", "segment"),
    decision_reason: readOptionalString(record, "decision_reason", "segment"),
    decided_by: readOptionalUuid(record, "decided_by", "segment"),
    decided_at: readOptionalString(record, "decided_at", "segment"),
    registered_by: readOptionalUuid(record, "registered_by", "segment"),
    allowed_actions: readStringArray(record, "allowed_actions", "segment"),
    version: readPositiveInteger(record, "version", "segment"),
    created_at: readString(record, "created_at", "segment"),
    updated_at: readString(record, "updated_at", "segment"),
  };
}

export function parseSegmentPage(value: unknown) {
  return parseCursorPage(value, "segments", parseSegment);
}

export function parseLedgerEntry(value: unknown): AcceptanceLedgerEntry {
  const record = asRecord(value, "ledger");
  return {
    ledger_id: readUuid(record, "ledger_id", "ledger"),
    project_id: readUuid(record, "project_id", "ledger"),
    batch_id: readOptionalUuid(record, "batch_id", "ledger"),
    supplier_id: readUuid(record, "supplier_id", "ledger"),
    resource_type: readString(record, "resource_type", "ledger"),
    resource_id: readUuid(record, "resource_id", "ledger"),
    action: readString(record, "action", "ledger"),
    from_status: readOptionalString(record, "from_status", "ledger"),
    to_status: readOptionalString(record, "to_status", "ledger"),
    actor_id: readUuid(record, "actor_id", "ledger"),
    request_id: readOptionalString(record, "request_id", "ledger"),
    reason: readOptionalString(record, "reason", "ledger"),
    snapshot: readRecord(record, "snapshot", "ledger"),
    occurred_at: readString(record, "occurred_at", "ledger"),
  };
}

export function parseLedgerPage(value: unknown) {
  return parseCursorPage(value, "ledger", parseLedgerEntry);
}
