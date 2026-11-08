import { describe, expect, it } from "vitest";

import {
  buildEpisodeActionInput,
  describeEpisodeWriteFailure,
  EMPTY_EPISODE_ACTION_FORM,
} from "./episodeActions";
import { ApiError } from "./api";

describe("Episode 动作请求体", () => {
  it("ready 只提交 Manifest 与帧数", () => {
    const result = buildEpisodeActionInput("ready", {
      ...EMPTY_EPISODE_ACTION_FORM,
      manifestText: '{"schema_version":"ddp.canonical/1.0.0-draft"}',
      frameCount: "300",
    });
    expect(result.error).toBeNull();
    expect(result.input).toEqual({
      manifest: { schema_version: "ddp.canonical/1.0.0-draft" },
      frame_count: 300,
    });
  });

  it("ready 的非法 JSON/帧数在前端拦截，不发送网络请求", () => {
    expect(
      buildEpisodeActionInput("ready", {
        ...EMPTY_EPISODE_ACTION_FORM,
        manifestText: "{",
        frameCount: "1",
      }).error,
    ).toContain("合法 JSON");
    expect(
      buildEpisodeActionInput("ready", {
        ...EMPTY_EPISODE_ACTION_FORM,
        manifestText: "[]",
        frameCount: "1",
      }).error,
    ).toContain("JSON 对象");
    expect(
      buildEpisodeActionInput("ready", {
        ...EMPTY_EPISODE_ACTION_FORM,
        manifestText: "{}",
        frameCount: "-1",
      }).error,
    ).toContain("非负整数");
  });

  it("quality-result 必须选择质检结果", () => {
    expect(
      buildEpisodeActionInput("quality-result", EMPTY_EPISODE_ACTION_FORM)
        .error,
    ).toContain("PASS");
    expect(
      buildEpisodeActionInput("quality-result", {
        ...EMPTY_EPISODE_ACTION_FORM,
        qualityStatus: "FAIL",
        reason: "画面遮挡",
      }).input,
    ).toEqual({ quality_status: "FAIL", reason: "画面遮挡" });
  });

  it("其他动作提交原因；空原因转为 null", () => {
    expect(
      buildEpisodeActionInput("reject", {
        ...EMPTY_EPISODE_ACTION_FORM,
        reason: "  质量问题  ",
      }).input,
    ).toEqual({ reason: "质量问题" });
    expect(
      buildEpisodeActionInput("complete", EMPTY_EPISODE_ACTION_FORM).input,
    ).toEqual({ reason: null });
  });
});

describe("Episode 写失败提示", () => {
  it("409 版本冲突要求刷新后重试", () => {
    const feedback = describeEpisodeWriteFailure(
      new ApiError({
        kind: "http",
        status: 409,
        code: "version_conflict",
        message: "版本冲突",
      }),
      "动作未生效",
    );
    expect(feedback.kind).toBe("warning");
    expect(feedback.description).toContain("刷新");
  });

  it("403 与 404 不误导为可重试", () => {
    expect(
      describeEpisodeWriteFailure(
        new ApiError({ kind: "http", status: 403, message: "无权" }),
        "动作未生效",
      ).title,
    ).toContain("无权");
    expect(
      describeEpisodeWriteFailure(
        new ApiError({ kind: "http", status: 404, message: "不存在" }),
        "动作未生效",
      ).title,
    ).toContain("不存在");
  });
});
