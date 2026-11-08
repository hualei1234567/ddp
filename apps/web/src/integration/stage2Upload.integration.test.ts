import { beforeAll, describe, expect, it } from "vitest";

import type {
  AssetFormat,
  CollectionTask,
  CompleteUploadResponse,
  UploadPartSummary,
  UploadSession,
} from "../types/collection";
import { ApiError, ApiClient } from "../services/api";
import {
  createCollectionsApi,
  type CollectionsApi,
} from "../services/collections";
import { createRawAssetsApi, type RawAssetsApi } from "../services/rawAssets";
import { createUploadsApi, type UploadsApi } from "../services/uploads";
import { deriveMissingPartNumbers } from "../services/uploadQueue";
import {
  computePartChecksum,
  hashFileSha256,
  planFileParts,
  putUploadPart,
} from "../services/uploadTransfer";

/**
 * DDP-307 真实后端 + 真实 MinIO 集成测试。
 *
 * 默认单测不会连接外部环境：只有显式设置 DDP_IT_BASE_URL、
 * DDP_IT_PROJECT_ID、DDP_IT_USER_ID 时才执行；否则整组跳过。
 * 启动方式见 apps/web/scripts/integration/run-stage2-upload.sh。
 *
 * 本文件不包含 Mock 数据；所有会话、分片、资产都来自真实 HTTP + S3。
 */

function readEnv(name: string): string {
  const runtime = globalThis as {
    process?: { env?: Record<string, string | undefined> };
  };
  return runtime.process?.env?.[name]?.trim() ?? "";
}

const BASE_URL = readEnv("DDP_IT_BASE_URL");
const PROJECT_ID = readEnv("DDP_IT_PROJECT_ID");
const USER_ID = readEnv("DDP_IT_USER_ID");
const DATABASE_URL = readEnv("DDP_DATABASE_URL");
const BACKEND_DIR = readEnv("DDP_IT_BACKEND_DIR");
const PYTHON = readEnv("DDP_IT_PYTHON");
const ASSET_STATE_HELPER = readEnv("DDP_IT_ASSET_STATE_HELPER");

if (BASE_URL !== "" && (PROJECT_ID === "" || USER_ID === "")) {
  throw new Error(
    "DDP_IT_BASE_URL 已设置，但缺少 DDP_IT_PROJECT_ID 或 DDP_IT_USER_ID",
  );
}

const JSON_CONTENT_TYPE = "application/json";
const JSON_FORMAT: AssetFormat = "JSON";
const TEST_TIMEOUT_MS = 180_000;

const integration = BASE_URL === "" ? describe.skip : describe;

function requireEnv(name: string, value: string): string {
  if (value === "") {
    throw new Error(`集成测试缺少环境变量：${name}`);
  }
  return value;
}

function createAuthenticatedClient(): ApiClient {
  const userId = requireEnv("DDP_IT_USER_ID", USER_ID);
  return new ApiClient({
    baseUrl: requireEnv("DDP_IT_BASE_URL", BASE_URL),
    timeoutMs: 60_000,
    fetchImpl: (input, init) => {
      const headers = new Headers(init?.headers);
      headers.set("X-User-Id", userId);
      return fetch(input, { ...init, headers });
    },
  });
}

let collectionsApi!: CollectionsApi;
let uploadsApi!: UploadsApi;
let rawAssetsApi!: RawAssetsApi;
const projectId = PROJECT_ID;
const encoder = new TextEncoder();

let partSizeBytes = 0;

/** 生成内容签名可识别的 JSON 字节；同一参数生成的字节完全一致。 */
function makeJsonBlob(sizeBytes: number, marker: string): Blob {
  if (sizeBytes < 64) {
    throw new Error("集成测试 JSON 样本至少需要 64 字节");
  }
  const bytes = new Uint8Array(sizeBytes);
  bytes.fill(0x20);
  const prefix = encoder.encode(`{"marker":"${marker}",`);
  const suffix = encoder.encode(`"end":true}`);
  bytes.set(prefix, 0);
  bytes.set(suffix, sizeBytes - suffix.length);
  return new Blob([bytes], { type: JSON_CONTENT_TYPE });
}

function uniqueMarker(label: string): string {
  return `${label}-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 10)}`;
}

async function createTask(label: string): Promise<CollectionTask> {
  return collectionsApi.createTask(
    projectId,
    {
      code: `IT307-${Date.now().toString(36)}-${Math.random()
        .toString(36)
        .slice(2, 8)}`,
      name: `DDP-307 ${label}`,
      priority: "P1",
      target_episode_count: 0,
      target_duration_sec: 0,
    },
    { idempotencyKey: crypto.randomUUID() },
  );
}

async function createSession(
  taskId: string | null,
  blob: Blob,
  filename: string,
  options: {
    contentType?: string;
    formatHint?: AssetFormat | null;
    declaredSizeBytes?: number;
  } = {},
): Promise<UploadSession> {
  return uploadsApi.createSession(
    projectId,
    {
      collection_task_id: taskId,
      original_filename: filename,
      declared_content_type: options.contentType ?? JSON_CONTENT_TYPE,
      declared_size_bytes: options.declaredSizeBytes ?? blob.size,
      format_hint:
        options.formatHint === undefined ? JSON_FORMAT : options.formatHint,
    },
    { idempotencyKey: crypto.randomUUID() },
  );
}

function sortedParts(session: UploadSession): UploadPartSummary[] {
  return [...session.uploaded_parts].sort(
    (left, right) => left.part_number - right.part_number,
  );
}

async function uploadPart(
  session: UploadSession,
  blob: Blob,
  partNumber: number,
  options: { ackSizeBytes?: number } = {},
): Promise<UploadPartSummary> {
  const plan = planFileParts(blob.size, session.part_size_bytes);
  const part = plan.find((entry) => entry.partNumber === partNumber);
  if (!part) {
    throw new Error(`分片计划缺少第 ${partNumber} 片`);
  }
  const partBlob = blob.slice(part.start, part.end);
  const partUrl = await uploadsApi.createPartUrl(
    projectId,
    session.upload_session_id,
    partNumber,
  );
  if (!("x-amz-checksum-sha256" in partUrl.headers)) {
    console.warn(
      `[契约偏差] 预签名分片 ${partNumber} 未包含 x-amz-checksum-sha256；` +
        "当前按服务端返回头发送，分片 SHA256 仍通过 ack 提交。",
    );
  }
  const checksum = await computePartChecksum(partBlob);
  const etag = await putUploadPart(partUrl, partBlob);
  const ack = await uploadsApi.ackPart(
    projectId,
    session.upload_session_id,
    partNumber,
    {
      etag,
      size_bytes: options.ackSizeBytes ?? part.sizeBytes,
      sha256: checksum.prefixed,
    },
  );
  return {
    part_number: ack.part_number,
    size_bytes: ack.size_bytes,
    etag: ack.etag,
    sha256: ack.sha256,
    recorded_at: ack.recorded_at,
  };
}

async function completeSession(
  sessionId: string,
  blob: Blob,
  overrides: { expectedSizeBytes?: number; expectedSha256?: string } = {},
): Promise<CompleteUploadResponse> {
  const latest = await uploadsApi.getSession(projectId, sessionId);
  const response = await uploadsApi.complete(
    projectId,
    sessionId,
    {
      expected_sha256:
        overrides.expectedSha256 ?? (await hashFileSha256(blob)),
      expected_size_bytes: overrides.expectedSizeBytes ?? blob.size,
      parts: sortedParts(latest).map((part) => ({
        part_number: part.part_number,
        etag: part.etag,
        size_bytes: part.size_bytes,
        sha256: part.sha256,
      })),
    },
    { version: latest.version, idempotencyKey: crypto.randomUUID() },
  );
  if (
    response.upload_session.uploaded_parts.length === 0 &&
    latest.uploaded_parts.length > 0
  ) {
    console.warn(
      "[契约偏差] complete 响应的 upload_session.uploaded_parts 为空，" +
        "但 uploaded_part_count 已更新；前端不能依赖该数组恢复分片列表。",
    );
  }
  return response;
}

async function expectApiError(
  promise: Promise<unknown>,
  status: number,
  code: string,
): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (!(error instanceof ApiError)) {
      throw error;
    }
    expect(error.status).toBe(status);
    expect(error.code).toBe(code);
    return error;
  }
  throw new Error(`预期请求失败（HTTP ${status} ${code}），但实际成功`);
}

/**
 * 测试夹具：VALIDATE Worker 不在本任务范围内运行。为覆盖后端 dedup 分支，
 * 这里按 Worker 的权威迁移把首资产置为 VALID；只修改本任务临时 SQLite。
 * 不使用 Mock 资产，也不修改 backend 代码。
 */
function markAssetValid(assetId: string): Promise<void> {
  if (ASSET_STATE_HELPER === "" || PYTHON === "" || BACKEND_DIR === "") {
    throw new Error("缺少 DDP_IT_ASSET_STATE_HELPER / DDP_IT_PYTHON / DDP_IT_BACKEND_DIR");
  }
  return import("node:child_process").then(({ execFileSync }) => {
    execFileSync(PYTHON, [ASSET_STATE_HELPER, assetId], {
      env: {
        ...runtimeEnv(),
        DDP_DATABASE_URL: requireEnv("DDP_DATABASE_URL", DATABASE_URL),
        PYTHONPATH: BACKEND_DIR,
      },
      stdio: "pipe",
    });
  });
}

function runtimeEnv(): Record<string, string | undefined> {
  const runtime = globalThis as {
    process?: { env?: Record<string, string | undefined> };
  };
  return runtime.process?.env ?? {};
}

integration("DDP-307 真实上传链路联调", () => {
  beforeAll(async () => {
    const client = createAuthenticatedClient();
    collectionsApi = createCollectionsApi(client);
    uploadsApi = createUploadsApi(client);
    rawAssetsApi = createRawAssetsApi(client);

    const probeBlob = makeJsonBlob(256, uniqueMarker("probe"));
    const probe = await createSession(null, probeBlob, "probe.json");
    partSizeBytes = probe.part_size_bytes;
    expect(partSizeBytes).toBeGreaterThanOrEqual(5 * 1024 * 1024);
    await uploadsApi.abort(projectId, probe.upload_session_id, {
      version: probe.version,
      reason: "集成测试读取 part_size_bytes",
      idempotencyKey: crypto.randomUUID(),
    });
  }, TEST_TIMEOUT_MS);

  it(
    "创建采集任务→两分片（末片小分片）→ack→complete→RawAsset 列表/详情",
    async () => {
      const task = await createTask("主链路");
      expect(task.status).toBe("DRAFT");
      expect(task.project_id).toBe(projectId);

      const blob = makeJsonBlob(partSizeBytes + 1024, uniqueMarker("main"));
      const session = await createSession(task.task_id, blob, "main.json");
      const plan = planFileParts(blob.size, session.part_size_bytes);
      expect(plan.map((part) => part.partNumber)).toEqual([1, 2]);
      expect(plan[1]?.sizeBytes).toBe(1024);

      const first = await uploadPart(session, blob, 1);
      expect(first.size_bytes).toBe(partSizeBytes);
      const second = await uploadPart(session, blob, 2);
      expect(second.size_bytes).toBe(1024);

      const list = await uploadsApi.listParts(
        projectId,
        session.upload_session_id,
      );
      expect(list.items.map((part) => part.part_number)).toEqual([1, 2]);
      expect(list.missing_part_numbers).toEqual([]);
      expect(list.uploaded_bytes).toBe(blob.size);

      const detail = await uploadsApi.getSession(
        projectId,
        session.upload_session_id,
      );
      expect(detail.status).toBe("UPLOADING");
      expect(detail.uploaded_part_count).toBe(2);
      expect(detail.declared_size_bytes).toBe(blob.size);

      const response = await completeSession(
        session.upload_session_id,
        blob,
      );
      expect(response.replayed).toBe(false);
      expect(response.deduplicated).toBe(false);
      expect(response.upload_session.status).toBe("COMPLETED");
      expect(response.upload_session.verification_status).toBe("PENDING");
      expect(response.raw_asset).not.toBeNull();

      const asset = response.raw_asset;
      if (asset === null) {
        throw new Error("complete 未返回 RawAsset");
      }
      expect(asset.asset_id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
      );
      expect(asset.sha256).toBe(await hashFileSha256(blob));
      expect(asset.size_bytes).toBe(blob.size);
      expect(asset.content_type).toBe(JSON_CONTENT_TYPE);
      expect(asset.format).toBe(JSON_FORMAT);
      expect(asset.status).toBe("VALIDATING");
      expect(asset.source_type).toBe("UPLOAD");
      expect(asset.upload_session_id).toBe(session.upload_session_id);
      expect(asset.collection_task_id).toBe(task.task_id);

      const page = await rawAssetsApi.listAssets(projectId, {
        status: "VALIDATING",
        collectionTaskId: task.task_id,
      });
      expect(
        page.items.some((item) => item.asset_id === asset.asset_id),
      ).toBe(true);
      expect(page.items.every((item) => item.project_id === projectId)).toBe(
        true,
      );

      const listed = await rawAssetsApi.getAsset(projectId, asset.asset_id);
      expect(listed.asset_id).toBe(asset.asset_id);
      expect(listed.sha256).toBe(asset.sha256);
      expect(listed.object_key).toBe(asset.object_key);
      expect(listed.device_time_raw).toBeNull();
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "断点恢复：缺失分片可由 getSession/ListParts 发现并只补传缺失分片",
    async () => {
      const blob = makeJsonBlob(partSizeBytes + 1024, uniqueMarker("resume"));
      const session = await createSession(null, blob, "resume.json");
      await uploadPart(session, blob, 1);

      const checkpoint = await uploadsApi.getSession(
        projectId,
        session.upload_session_id,
      );
      expect(checkpoint.uploaded_part_count).toBe(1);
      expect(checkpoint.missing_part_numbers).toEqual([2]);
      expect(deriveMissingPartNumbers(checkpoint, 2)).toEqual([2]);

      const list = await uploadsApi.listParts(
        projectId,
        session.upload_session_id,
      );
      expect(list.items.map((part) => part.part_number)).toEqual([1]);
      expect(list.missing_part_numbers).toEqual([2]);

      const incomplete = await expectApiError(
        completeSession(session.upload_session_id, blob),
        409,
        "upload_incomplete",
      );
      expect(incomplete.details?.missing_part_numbers).toEqual([2]);

      await uploadPart(session, blob, 2);
      const response = await completeSession(
        session.upload_session_id,
        blob,
      );
      expect(response.upload_session.status).toBe("COMPLETED");
      expect(response.upload_session.uploaded_part_count).toBe(2);
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "小文件单分片可直接 complete",
    async () => {
      const blob = makeJsonBlob(512, uniqueMarker("single"));
      const session = await createSession(null, blob, "single.json");
      const plan = planFileParts(blob.size, session.part_size_bytes);
      expect(plan).toHaveLength(1);
      expect(plan[0]?.sizeBytes).toBe(512);

      await uploadPart(session, blob, 1);
      const response = await completeSession(
        session.upload_session_id,
        blob,
      );
      expect(response.deduplicated).toBe(false);
      expect(response.upload_session.uploaded_part_count).toBe(1);
      expect(response.raw_asset?.size_bytes).toBe(512);
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "同内容重复上传命中 dedup，复用既有 RawAsset",
    async () => {
      const blob = makeJsonBlob(1024, "ddp-307-dedup-fixed-content");
      const firstSession = await createSession(null, blob, "dedup-1.json");
      await uploadPart(firstSession, blob, 1);
      const first = await completeSession(
        firstSession.upload_session_id,
        blob,
      );
      const firstAssetId = first.raw_asset?.asset_id;
      if (!firstAssetId) {
        throw new Error("首次上传未返回 RawAsset");
      }
      expect(first.raw_asset?.status).toBe("VALIDATING");

      await markAssetValid(firstAssetId);
      const verified = await rawAssetsApi.getAsset(projectId, firstAssetId);
      expect(verified.status).toBe("VALID");

      const secondSession = await createSession(null, blob, "dedup-2.json");
      await uploadPart(secondSession, blob, 1);
      const second = await completeSession(
        secondSession.upload_session_id,
        blob,
      );
      expect(second.deduplicated).toBe(true);
      expect(second.upload_session.deduplicated).toBe(true);
      expect(second.upload_session.deduplicated_asset_id).toBe(firstAssetId);
      expect(second.raw_asset?.asset_id).toBe(firstAssetId);
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "声明内容类型不在白名单返回 415 file_type_unsupported",
    async () => {
      const blob = makeJsonBlob(256, uniqueMarker("415"));
      await expectApiError(
        createSession(null, blob, "not-allowed.txt", {
          contentType: "text/plain",
        }),
        415,
        "file_type_unsupported",
      );
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "ack 分片大小与服务端预期不符返回 422 size_mismatch",
    async () => {
      const blob = makeJsonBlob(512, uniqueMarker("ack-size"));
      const session = await createSession(null, blob, "ack-size.json");
      const plan = planFileParts(blob.size, session.part_size_bytes);
      const partSize = plan[0]?.sizeBytes ?? 0;
      const error = await expectApiError(
        uploadPart(session, blob, 1, { ackSizeBytes: partSize + 1 }),
        422,
        "size_mismatch",
      );
      expect(error.details?.expected).toBe(partSize);
      expect(error.details?.actual).toBe(partSize + 1);

      const latest = await uploadsApi.getSession(
        projectId,
        session.upload_session_id,
      );
      await uploadsApi.abort(projectId, session.upload_session_id, {
        version: latest.version,
        reason: "集成测试 ack 大小不符后清理",
        idempotencyKey: crypto.randomUUID(),
      });
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "complete 总大小与声明不符返回 422 size_mismatch",
    async () => {
      const blob = makeJsonBlob(512, uniqueMarker("total-size"));
      const session = await createSession(null, blob, "total-size.json");
      await uploadPart(session, blob, 1);

      const error = await expectApiError(
        completeSession(session.upload_session_id, blob, {
          expectedSizeBytes: blob.size + 1,
        }),
        422,
        "size_mismatch",
      );
      expect(error.details?.expected).toBe(blob.size);

      const latest = await uploadsApi.getSession(
        projectId,
        session.upload_session_id,
      );
      await uploadsApi.abort(projectId, session.upload_session_id, {
        version: latest.version,
        reason: "集成测试总大小不符后清理",
        idempotencyKey: crypto.randomUUID(),
      });
    },
    TEST_TIMEOUT_MS,
  );

  it(
    "上传后取消调用 abort，会话进入 ABORTED 且不可继续取分片 URL",
    async () => {
      const blob = makeJsonBlob(512, uniqueMarker("abort"));
      const session = await createSession(null, blob, "abort.json");
      await uploadPart(session, blob, 1);

      const latest = await uploadsApi.getSession(
        projectId,
        session.upload_session_id,
      );
      const aborted = await uploadsApi.abort(
        projectId,
        session.upload_session_id,
        {
          version: latest.version,
          reason: "DDP-307 集成测试用户取消",
          idempotencyKey: crypto.randomUUID(),
        },
      );
      expect(aborted.status).toBe("ABORTED");
      expect(aborted.aborted_at).not.toBeNull();

      await expectApiError(
        uploadsApi.createPartUrl(projectId, session.upload_session_id, 2),
        409,
        "upload_session_not_uploading",
      );
    },
    TEST_TIMEOUT_MS,
  );
});
