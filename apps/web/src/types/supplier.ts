/** 阶段 6 供应商与到货验收领域类型。 */

export interface Supplier {
  supplier_id: string;
  project_id: string;
  code: string;
  name: string;
  category: string;
  status: string;
  contact_name: string | null;
  contact_email: string | null;
  external_ref: string | null;
  notes: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SupplierMetrics {
  batch_count: number;
  settled_batch_count: number;
  /** 缺失为 null，界面必须显示「暂无」而不是 0。 */
  on_time_rate: number | null;
  first_pass_rate: number | null;
  rework_rate: number | null;
  average_cycle_hours: number | null;
  accepted_segments: number;
  decided_segments: number;
  segment_pass_rate: number | null;
}

export interface DeliveryBatch {
  batch_id: string;
  project_id: string;
  supplier_id: string;
  batch_code: string;
  title: string | null;
  status: string;
  planned_segment_count: number;
  received_segment_count: number;
  planned_arrival_date: string | null;
  arrived_at: string | null;
  settled_at: string | null;
  rework_count: number;
  storage_prefix: string | null;
  notes: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InboundSegment {
  segment_id: string;
  project_id: string;
  batch_id: string;
  segment_code: string;
  status: string;
  raw_asset_id: string | null;
  duration_ns: number | null;
  task_name: string | null;
  diagnostics: Record<string, unknown>;
  decision: string | null;
  decision_reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  registered_by: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface AcceptanceLedgerEntry {
  ledger_id: string;
  project_id: string;
  batch_id: string | null;
  supplier_id: string;
  resource_type: string;
  resource_id: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor_id: string;
  request_id: string | null;
  reason: string | null;
  snapshot: Record<string, unknown>;
  occurred_at: string;
}

export const SUPPLIER_STATUS_VALUES = ["DRAFT", "ACTIVE", "SUSPENDED", "TERMINATED"] as const;
export const SUPPLIER_CATEGORY_VALUES = [
  "DATA_COLLECTION",
  "ANNOTATION",
  "HARDWARE",
  "INTEGRATION",
  "OTHER",
] as const;
export const BATCH_STATUS_VALUES = [
  "ANNOUNCED",
  "IN_TRANSIT",
  "ARRIVED",
  "SCREENING",
  "ACCEPTED",
  "PARTIALLY_ACCEPTED",
  "REJECTED",
  "REWORK",
  "CLOSED",
] as const;
export const SEGMENT_STATUS_VALUES = [
  "REGISTERED",
  "PENDING_REVIEW",
  "ACCEPTED",
  "REJECTED",
  "NEED_MORE",
] as const;
export const SEGMENT_DECISION_VALUES = ["ACCEPT", "REJECT", "NEED_MORE"] as const;
