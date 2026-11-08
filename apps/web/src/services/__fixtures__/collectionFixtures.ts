import type {
  CollectionBatch,
  CollectionTask,
  Episode,
  UploadPartSummary,
  UploadSession,
} from "../../types/collection";

/**
 * 合成样例，仅用于解析器与组件状态测试。
 * 不代表真实采集数据，也不进入任何业务验收或指标。
 */
export const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
export const OTHER_PROJECT_ID = "99999999-9999-7999-8999-999999999999";
export const TASK_ID = "22222222-2222-7222-8222-222222222222";
export const BATCH_ID = "33333333-3333-7333-8333-333333333333";
export const SESSION_ID = "44444444-4444-7444-8444-444444444444";
export const EPISODE_ID = "55555555-5555-7555-8555-555555555555";
export const LINEAGE_ID = "66666666-6666-7666-8666-666666666666";
export const ASSET_ID = "77777777-7777-7777-8777-777777777777";
export const USER_ID = "88888888-8888-7888-8888-888888888888";
export const ROBOT_ID = "99999999-9999-7999-9999-999999999999";
export const DEVICE_ID = "aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa";
export const SENSOR_ID = "bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb";
export const CALIBRATION_ID = "cccccccc-cccc-7ccc-8ccc-cccccccccccc";
export const JOB_ID = "dddddddd-dddd-7ddd-8ddd-dddddddddddd";

const SHA = "sha256:" + "a".repeat(64);

export const collectionTaskFixture: CollectionTask = {
  task_id: TASK_ID,
  project_id: PROJECT_ID,
  code: "COL-0001",
  name: "厨房桌面抓取采集",
  requirement_id: null,
  plan_id: null,
  sop_id: "eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee",
  sop_version: "sop-kitchen-v3",
  scenario_id: null,
  action_spec_id: null,
  action_spec_version: null,
  status: "IN_PROGRESS",
  priority: "P0",
  assigned_to: USER_ID,
  assigned_robot_id: ROBOT_ID,
  assigned_device_ids: [DEVICE_ID],
  assigned_sensor_ids: [SENSOR_ID],
  location_id: null,
  scheduled_start: "2026-11-08T01:00:00Z",
  scheduled_end: "2026-11-08T09:00:00Z",
  actual_start: "2026-11-08T01:10:00Z",
  actual_end: null,
  target_episode_count: 20,
  target_duration_sec: 7200,
  submitted_episode_count: 8,
  accepted_episode_count: 5,
  quality_summary: { failed_count: 1 },
  status_reason: null,
  blocked_reason: null,
  version: 7,
  created_at: "2026-11-07T08:00:00Z",
  updated_at: "2026-11-08T03:20:00Z",
  created_by: USER_ID,
  updated_by: USER_ID,
  allowed_actions: ["pause", "block", "submit", "cancel"],
};

export const collectionBatchFixture: CollectionBatch = {
  batch_id: BATCH_ID,
  project_id: PROJECT_ID,
  task_id: TASK_ID,
  code: "B-01",
  sequence_no: 1,
  status: "IN_PROGRESS",
  assigned_to: USER_ID,
  scheduled_start: "2026-11-08T01:00:00Z",
  scheduled_end: "2026-11-08T04:00:00Z",
  expected_count: 10,
  submitted_count: 6,
  accepted_count: 4,
  status_reason: null,
  version: 3,
  created_at: "2026-11-07T08:10:00Z",
  updated_at: "2026-11-08T03:10:00Z",
  allowed_actions: ["pause", "submit"],
};

export const uploadPartFixture: UploadPartSummary = {
  part_number: 1,
  size_bytes: 16 * 1024 * 1024,
  etag: '"etag-part-1"',
  sha256: SHA,
  recorded_at: "2026-11-08T03:00:00Z",
};

export const uploadSessionFixture: UploadSession = {
  upload_session_id: SESSION_ID,
  project_id: PROJECT_ID,
  collection_task_id: TASK_ID,
  batch_id: BATCH_ID,
  status: "UPLOADING",
  verification_status: "PENDING",
  verification_error_code: null,
  verified_at: null,
  original_filename: "episode-001.mcap",
  declared_content_type: "application/x-mcap",
  declared_size_bytes: 32 * 1024 * 1024,
  expected_sha256: SHA,
  format_hint: "MCAP",
  part_size_bytes: 16 * 1024 * 1024,
  max_size_bytes: 5 * 1024 * 1024 * 1024,
  allowed_content_types: ["application/x-mcap", "application/octet-stream"],
  object_key_prefix: `raw/${PROJECT_ID}/2026/11/08/${ASSET_ID}/`,
  object_key: null,
  expires_at: "2026-11-09T03:00:00Z",
  completed_at: null,
  aborted_at: null,
  failure_code: null,
  failure_summary: null,
  uploaded_bytes: 16 * 1024 * 1024,
  uploaded_part_count: 1,
  missing_part_numbers: [2],
  uploaded_parts: [uploadPartFixture],
  deduplicated: false,
  deduplicated_asset_id: null,
  asset_id: null,
  version: 2,
  created_at: "2026-11-08T02:50:00Z",
  updated_at: "2026-11-08T03:00:00Z",
  created_by: USER_ID,
};

export const episodeFixture: Episode = {
  episode_id: EPISODE_ID,
  episode_lineage_id: LINEAGE_ID,
  episode_version: 1,
  project_id: PROJECT_ID,
  code: "EP-0001",
  task_id: TASK_ID,
  batch_id: BATCH_ID,
  robot_id: ROBOT_ID,
  device_ids: [DEVICE_ID],
  sensor_ids: [SENSOR_ID],
  raw_asset_ids: [ASSET_ID],
  start_time_ns: "1730000000000000000",
  end_time_ns: "1730000010000000000",
  duration_ns: "10000000000",
  time_domain: "UTC",
  time_base: {
    time_domain: "UTC",
    clock_source: "PTP",
    clock_id: "ptp0",
    offset_to_utc_ns: "0",
    drift_ppm: 0.2,
    uncertainty_ns: "1000",
    sync_method: "PTP",
    sync_ref: "grandmaster-1",
    sync_status: "VERIFIED",
  },
  canonical_schema_version: "ddp.canonical/1.0.0-draft",
  pipeline_id: "pipeline-basic",
  pipeline_version: "1.0.0",
  segmentation_rule_version: "seg-manual-v1",
  status: "READY",
  quality_status: "NOT_CHECKED",
  annotation_status: "NOT_STARTED",
  success: true,
  failure_reason: null,
  scene_labels: ["kitchen"],
  language_instruction: "把杯子放到托盘上",
  frame_count: 300,
  calibration_snapshot: [
    {
      calibration_id: CALIBRATION_ID,
      calibration_version: 2,
      checksum: SHA,
      target_type: "SENSOR",
      target_id: SENSOR_ID,
      schema_version: "ddp.calibration/1.0.0",
    },
  ],
  manifest_key: `canonical/${PROJECT_ID}/${EPISODE_ID}/1/manifest.json`,
  manifest_sha256: SHA,
  preview_refs: [],
  supersedes_id: null,
  version: 1,
  created_at: "2026-11-08T03:30:00Z",
  updated_at: "2026-11-08T03:40:00Z",
  created_by: USER_ID,
  updated_by: USER_ID,
};
