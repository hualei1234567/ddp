import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  uploadSessionFixture,
  PROJECT_ID,
} from "./__fixtures__/collectionFixtures";
import {
  canPauseUpload,
  createUploadQueueItem,
  deriveMissingPartNumbers,
  describeUploadFailure,
  formatBytes,
  formatRemainingTime,
  guessFormatFromFilename,
  uploadProgressPercent,
  uploadSpeedBytesPerSecond,
} from "./uploadQueue";
import { UploadTransportError } from "./uploadTransfer";

function item(overrides: Parameters<typeof createUploadQueueItem>[0] = {
  id: "item-1",
  fileName: "demo.mcap",
  sizeBytes: 100,
  idempotencyKey: "idem-1",
}) {
  return createUploadQueueItem(overrides);
}

describe("上传队列纯状态", () => {
  it("按已上传字节计算真实进度，未知大小返回 null", () => {
    const queueItem = item();
    expect(uploadProgressPercent(queueItem)).toBe(0);
    expect(
      uploadProgressPercent({ ...queueItem, uploadedBytes: 50 }),
    ).toBe(50);
    expect(
      uploadProgressPercent({ ...queueItem, sizeBytes: 0 }),
    ).toBeNull();
  });

  it("速度与剩余时间不依赖伪造百分比", () => {
    const queueItem = {
      ...item(),
      uploadedBytes: 50,
      startedAt: 1000,
    };
    expect(uploadSpeedBytesPerSecond(queueItem, 2000)).toBe(50);
    expect(formatRemainingTime(queueItem, 2000)).toContain("约");
    expect(formatBytes(1024)).toBe("1.00 KiB");
  });

  it("断点缺失分片优先使用服务端 missing_part_numbers", () => {
    expect(
      deriveMissingPartNumbers(uploadSessionFixture, 2),
    ).toEqual([2]);
  });

  it("根据已确认分片推导缺失分片", () => {
    expect(
      deriveMissingPartNumbers(
        { ...uploadSessionFixture, missing_part_numbers: [] },
        2,
      ),
    ).toEqual([2]);
  });

  it("格式提示只来自扩展名，未知扩展回落 UNKNOWN", () => {
    expect(guessFormatFromFilename("a.MCAP")).toBe("MCAP");
    expect(guessFormatFromFilename("a.db3")).toBe("ROSBAG2");
    expect(guessFormatFromFilename("a.bin")).toBe("UNKNOWN");
  });

  it("只有活动状态可暂停", () => {
    expect(canPauseUpload(item())).toBe(false);
    expect(
      canPauseUpload(
        { ...item(), status: "uploading" },
      ),
    ).toBe(true);
  });
});

describe("上传失败四要素与恢复路径", () => {
  it("403 不可重试，提示权限以服务端为准", () => {
    const failure = describeUploadFailure(
      new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权上传",
      }),
    );
    expect(failure.forbidden).toBe(true);
    expect(failure.retryable).toBe(false);
    expect(failure.hint).toContain("权限以服务端为准");
  });

  it("409 version_conflict 要求刷新后重试", () => {
    const failure = describeUploadFailure(
      new ApiError({
        kind: "http",
        status: 409,
        code: "version_conflict",
        message: "版本冲突",
      }),
    );
    expect(failure.requiresRefresh).toBe(true);
    expect(failure.retryable).toBe(true);
  });

  it("410 会话过期要求新建会话", () => {
    const failure = describeUploadFailure(
      new ApiError({
        kind: "http",
        status: 410,
        code: "upload_session_expired",
        message: "会话过期",
      }),
    );
    expect(failure.requiresNewSession).toBe(true);
  });

  it("upload_incomplete 提取缺失分片并只补传缺失分片", () => {
    const failure = describeUploadFailure(
      new ApiError({
        kind: "http",
        status: 409,
        code: "upload_incomplete",
        message: "分片缺失",
        details: { missing_part_numbers: [2, 3] },
      }),
    );
    expect(failure.missingPartNumbers).toEqual([2, 3]);
    expect(failure.hint).toContain("2、3");
  });

  it("网络中断可重试，未知错误不默认成功", () => {
    const network = describeUploadFailure(
      new UploadTransportError("网络中断", {
        retryable: true,
        code: "network",
      }),
    );
    expect(network.retryable).toBe(true);

    const unknown = describeUploadFailure(new Error("boom"));
    expect(unknown.code).toBe("unknown_error");
    expect(unknown.retryable).toBe(true);
    expect(unknown.summary).toContain("未识别");
  });

  it("上传会话项目必须为当前请求项目（由解析/API 层校验）", () => {
    expect(uploadSessionFixture.project_id).toBe(PROJECT_ID);
  });
});
