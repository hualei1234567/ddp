import { describe, expect, it } from "vitest";

import {
  collectionBatchFixture,
  collectionTaskFixture,
  episodeFixture,
  PROJECT_ID,
  uploadSessionFixture,
} from "./__fixtures__/collectionFixtures";
import {
  parseCollectionBatch,
  parseCollectionBatchPage,
  parseCollectionTask,
  parseCollectionTaskPage,
  parseEpisode,
  parseEpisodePage,
  parseLineageResponse,
} from "./collectionParsers";
import {
  parseCompleteUploadResponse,
  parseUploadPartAck,
  parseUploadPartList,
  parseUploadPartUrl,
  parseUploadSession,
} from "./uploadParsers";
import {
  ASSET_ID,
  JOB_ID,
  uploadPartFixture,
  USER_ID,
} from "./__fixtures__/collectionFixtures";
import { LINEAGE_RELATION_LABELS } from "./lineagePresentation";
import { ContractValidationError } from "./validation";

describe("采集任务与批次解析", () => {
  it("解析合法任务并保留服务端动作集合", () => {
    const task = parseCollectionTask(collectionTaskFixture);
    expect(task.task_id).toBe(collectionTaskFixture.task_id);
    expect(task.status).toBe("IN_PROGRESS");
    expect(task.assigned_device_ids).toEqual(
      collectionTaskFixture.assigned_device_ids,
    );
    expect(task.allowed_actions).toEqual(["pause", "block", "submit", "cancel"]);
  });

  it("未知任务状态直接拒绝，不默认成功", () => {
    expect(() =>
      parseCollectionTask({ ...collectionTaskFixture, status: "WHATEVER" }),
    ).toThrow(ContractValidationError);
  });

  it("缺少 allowed_actions 时返回 undefined，前端不推断动作", () => {
    const { allowed_actions: _ignored, ...withoutActions } =
      collectionTaskFixture;
    void _ignored;
    const task = parseCollectionTask(withoutActions);
    expect(task.allowed_actions).toBeUndefined();
  });

  it("解析任务与批次分页", () => {
    expect(
      parseCollectionTaskPage({
        items: [collectionTaskFixture],
        next_cursor: "cursor-2",
      }).next_cursor,
    ).toBe("cursor-2");
    expect(
      parseCollectionBatchPage({ items: [collectionBatchFixture], next_cursor: null })
        .items[0]?.batch_id,
    ).toBe(collectionBatchFixture.batch_id);
    expect(parseCollectionBatch(collectionBatchFixture).sequence_no).toBe(1);
  });

  it("分页 next_cursor 缺失时按 null 处理", () => {
    expect(
      parseCollectionTaskPage({ items: [] }).next_cursor,
    ).toBeNull();
  });
});

describe("Episode 解析", () => {
  it("解析时间基准、标定快照与长整型字符串", () => {
    const episode = parseEpisode(episodeFixture);
    expect(episode.start_time_ns).toBe("1730000000000000000");
    expect(episode.time_base.sync_status).toBe("VERIFIED");
    expect(episode.calibration_snapshot).toHaveLength(1);
    expect(episode.quality_status).toBe("NOT_CHECKED");
  });

  it("真后端以整数返回纳秒时间戳时无损转成字符串", () => {
    // 契约（schemas.stage2.yaml）把 start_time_ns 声明为 integer，真后端按
    // 数字返回；前端统一用字符串承载，避免大数在后续运算里丢精度。
    const episode = parseEpisode({
      ...episodeFixture,
      start_time_ns: 69000000000,
      end_time_ns: 81000000000,
      duration_ns: 12000000000,
    });
    expect(episode.start_time_ns).toBe("69000000000");
    expect(episode.end_time_ns).toBe("81000000000");
    expect(episode.duration_ns).toBe("12000000000");
  });

  it("纳秒整数超出 JS 安全范围时报错，不静默接受已丢失的精度", () => {
    // epoch 纳秒约 1.7e18，超过 Number.MAX_SAFE_INTEGER（9.0e15）。
    expect(() =>
      parseEpisode({
        ...episodeFixture,
        start_time_ns: 1730000000000000000,
      }),
    ).toThrow(/安全整数范围/);
  });

  it("纳秒字段既非字符串也非整数时拒绝解析", () => {
    expect(() =>
      parseEpisode({ ...episodeFixture, duration_ns: null }),
    ).toThrow(ContractValidationError);
  });

  it("标定快照为空时仍解析：真实 Episode 可以没有标定", () => {
    // 后端 `EpisodeOut.calibration_snapshot` 默认空数组；要求非空会让公开
    // 数据集与未标定 Episode 在标注台全部不可用（见 collectionParsers.ts）。
    const episode = parseEpisode({
      ...episodeFixture,
      calibration_snapshot: [],
    });
    expect(episode.calibration_snapshot).toEqual([]);
  });

  it("解析 Episode 分页", () => {
    expect(
      parseEpisodePage({ items: [episodeFixture], next_cursor: null }).items[0]
        ?.episode_id,
    ).toBe(episodeFixture.episode_id);
  });

  it("兼容真后端：顶层无 time_domain/raw_asset_ids，Manifest 与时间可为 null", () => {
    const {
      time_domain: _timeDomain,
      raw_asset_ids: _rawAssetIds,
      ...withoutDraftOnlyFields
    } = episodeFixture;
    void _timeDomain;
    void _rawAssetIds;
    const episode = parseEpisode({
      ...withoutDraftOnlyFields,
      manifest_key: null,
      manifest_sha256: null,
      created_at: null,
      updated_at: null,
      attempt: 1,
      max_attempts: 3,
    });
    expect(episode.time_domain).toBe("UTC");
    expect(episode.raw_asset_ids).toEqual([]);
    expect(episode.manifest_key).toBeNull();
    expect(episode.created_at).toBeNull();
    expect(episode.attempt).toBe(1);
  });

  it("解析正反向血缘并校验方向回显", () => {
    const edge = {
      edge_id: "66666666-6666-7666-8666-666666666666",
      project_id: PROJECT_ID,
      from_node_type: "RAW_ASSET",
      from_node_id: "77777777-7777-7777-8777-777777777777",
      to_node_type: "EPISODE",
      to_node_id: "55555555-5555-7555-8555-555555555555",
      relation: "SEGMENTED_INTO",
      metadata: {},
      created_by: "88888888-8888-7888-8888-888888888888",
      created_at: "2026-11-08T03:30:00Z",
    };
    const response = parseLineageResponse(
      { items: [edge], direction: "reverse" },
      "reverse",
    );
    expect(response.items[0]?.relation).toBe("SEGMENTED_INTO");
    const trainedOn = parseLineageResponse(
      {
        items: [
          {
            ...edge,
            from_node_type: "DATASET_VERSION",
            to_node_type: "TRAINING_RUN",
            relation: "TRAINED_ON",
          },
        ],
        direction: "forward",
      },
      "forward",
    );
    expect(trainedOn.items[0]?.relation).toBe("TRAINED_ON");
    expect(LINEAGE_RELATION_LABELS.TRAINED_ON).toBe("用于训练");
    expect(() =>
      parseLineageResponse({ items: [edge], direction: "forward" }, "reverse"),
    ).toThrow(ContractValidationError);
    expect(() =>
      parseLineageResponse(
        {
          items: [{ ...edge, relation: "UNKNOWN_RELATION" }],
          direction: "forward",
        },
        "forward",
      ),
    ).toThrow(ContractValidationError);
  });
});

describe("上传响应解析", () => {
  it("解析断点会话、已确认分片与缺失分片", () => {
    const session = parseUploadSession(uploadSessionFixture);
    expect(session.uploaded_part_count).toBe(1);
    expect(session.missing_part_numbers).toEqual([2]);
    expect(session.uploaded_parts[0]?.sha256).toMatch(/^sha256:/);
  });

  it("解析分片 URL、确认与分片列表", () => {
    const partUrl = parseUploadPartUrl({
      part_number: 1,
      method: "PUT",
      url: "https://storage.example/part-1",
      headers: { "Content-Length": "16777216" },
      expires_at: "2026-11-08T12:15:00Z",
    });
    expect(partUrl.method).toBe("PUT");

    const ack = parseUploadPartAck({
      part_number: 1,
      etag: '"etag"',
      size_bytes: 16,
      sha256: uploadPartFixture.sha256,
      recorded_at: "2026-11-08T03:00:00Z",
      uploaded_bytes: 16,
      uploaded_part_count: 1,
    });
    expect(ack.part_number).toBe(1);

    const list = parseUploadPartList({
      items: [uploadPartFixture],
      missing_part_numbers: [2],
      uploaded_bytes: 16,
    });
    expect(list.missing_part_numbers).toEqual([2]);
  });

  it("完成响应的原始资产可以为 null，但会话与 Job 必须完整", () => {
    const response = parseCompleteUploadResponse({
      upload_session: {
        ...uploadSessionFixture,
        status: "COMPLETED",
        verification_status: "PENDING",
      },
      raw_asset: {
        asset_id: ASSET_ID,
        project_id: PROJECT_ID,
        code: "RAW-0001",
        source_type: "UPLOAD",
        collection_task_id: collectionTaskFixture.task_id,
        batch_id: collectionBatchFixture.batch_id,
        upload_session_id: uploadSessionFixture.upload_session_id,
        original_filename: "episode-001.mcap",
        object_key: `raw/${PROJECT_ID}/2026/11/08/${ASSET_ID}/content.mcap`,
        size_bytes: 32,
        content_type: "application/x-mcap",
        format: "MCAP",
        sha256: uploadPartFixture.sha256,
        status: "VALIDATING",
        captured_at: null,
        ingested_at: null,
        device_ids: [],
        sensor_ids: [],
        sensor_snapshot: null,
        raw_metadata: {},
        storage_class: "STANDARD",
        retention_policy_id: null,
        quarantine_reason: null,
        quarantine_codes: [],
        scan_report_key: null,
        verified_at: null,
        archived_at: null,
        tombstoned_at: null,
        duplicate_of_asset_id: null,
        uploaded_by: USER_ID,
        version: 1,
        created_at: "2026-11-08T03:00:00Z",
        updated_at: "2026-11-08T03:00:00Z",
        created_by: USER_ID,
        updated_by: null,
        allowed_actions: ["tombstone"],
      },
      deduplicated: false,
      validation_job_id: JOB_ID,
      replayed: false,
    });
    expect(response.upload_session.status).toBe("COMPLETED");
    expect(response.raw_asset?.status).toBe("VALIDATING");
    expect(response.validation_job_id).toBe(JOB_ID);
  });
});
