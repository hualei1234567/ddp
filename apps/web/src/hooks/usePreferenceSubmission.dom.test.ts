import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../services/api";
import { annotationSubmissionFixture } from "../services/__fixtures__/annotationFixtures";
import { writePreferenceLabelsUnsupported } from "../services/llmPreferenceSubmission";
import type { PreferencePairAnnotationValue } from "../types/llmAnnotation";
import {
  usePreferenceSubmission,
  type PreferenceRecordsWriter,
  type PreferenceSubmitOutcome,
} from "./usePreferenceSubmission";

const ANNOTATION_ID = annotationSubmissionFixture.records[0]!.annotation_id;

const WIN: PreferencePairAnnotationValue = {
  winner: "A",
  confidence: 0.8,
  rationale: "A 更完整",
};

function renderSubmitter({
  values,
  writeLabels = async () => undefined,
}: {
  values: Record<string, PreferencePairAnnotationValue | null>;
  writeLabels?: PreferenceRecordsWriter;
}) {
  const writer = vi.fn(writeLabels);
  const hook = renderHook(() =>
    usePreferenceSubmission({
      values,
      records: annotationSubmissionFixture.records,
      writeLabels: writer,
    }),
  );
  return { hook, writer };
}

describe("usePreferenceSubmission 提交接线", () => {
  it("有 winner 草稿时 hasDrafts=true，成功写入后返回成功反馈", async () => {
    const values = { [ANNOTATION_ID]: WIN };
    const { hook, writer } = renderSubmitter({ values });
    expect(hook.result.current.hasDrafts).toBe(true);

    let outcome: PreferenceSubmitOutcome | undefined;
    await act(async () => {
      outcome = await hook.result.current.submit();
    });

    expect(writer).toHaveBeenCalledTimes(1);
    const records = writer.mock.calls[0]![0];
    expect(records[0]?.labels.some((label) => label.label_key === "preference_pair")).toBe(
      true,
    );
    expect(outcome?.kind).toBe("submitted");
    expect(outcome?.feedback?.kind).toBe("success");
  });

  it("服务端缺写入通道：明确失败、错误可见、草稿原样保留（不丢用户输入）", async () => {
    const values = { [ANNOTATION_ID]: WIN };
    const { hook } = renderSubmitter({
      values,
      writeLabels: writePreferenceLabelsUnsupported,
    });

    let outcome: PreferenceSubmitOutcome | undefined;
    await act(async () => {
      outcome = await hook.result.current.submit();
    });

    expect(outcome?.kind).toBe("blocked");
    expect(outcome?.feedback?.kind).toBe("error");
    expect(outcome?.feedback?.description).toContain("偏好校验待补");
    // hook 不清空任何输入：草稿引用与内容保持不变。
    expect(values).toEqual({ [ANNOTATION_ID]: WIN });
    expect(hook.result.current.hasDrafts).toBe(true);
  });

  it("网络/服务端失败：返回错误反馈且草稿保留", async () => {
    const values = { [ANNOTATION_ID]: WIN };
    const { hook } = renderSubmitter({
      values,
      writeLabels: async () => {
        throw new ApiError({
          kind: "http",
          status: 409,
          code: "version_conflict",
          message: "资源版本已变化，请重新读取后再试",
          requestId: "req-pref",
        });
      },
    });

    let outcome: PreferenceSubmitOutcome | undefined;
    await act(async () => {
      outcome = await hook.result.current.submit();
    });

    expect(outcome?.kind).toBe("failed");
    // 409 冲突由既有反馈映射给出 warning（可恢复提示），关键是不渲染成功。
    expect(outcome?.feedback?.kind).not.toBe("success");
    expect(outcome?.feedback?.description).toContain("req-pref");
    expect(values).toEqual({ [ANNOTATION_ID]: WIN });
  });

  it("校验失败不调用写入器（fail-closed）且草稿保留", async () => {
    const values = {
      [ANNOTATION_ID]: { winner: "B" as const, confidence: 2, rationale: null },
    };
    const { hook, writer } = renderSubmitter({ values });

    let outcome: PreferenceSubmitOutcome | undefined;
    await act(async () => {
      outcome = await hook.result.current.submit();
    });

    expect(writer).not.toHaveBeenCalled();
    expect(outcome?.kind).toBe("invalid");
    expect(outcome?.feedback?.description).toContain("置信度必须在 0~1 之间");
    expect(values[ANNOTATION_ID]?.winner).toBe("B");
  });

  it("无 winner 草稿时 hasDrafts=false（页面走既有提交链路，零回归）", async () => {
    const values = { [ANNOTATION_ID]: null };
    const { hook, writer } = renderSubmitter({ values });
    expect(hook.result.current.hasDrafts).toBe(false);

    let outcome: PreferenceSubmitOutcome | undefined;
    await act(async () => {
      outcome = await hook.result.current.submit();
    });
    expect(outcome?.kind).toBe("empty");
    expect(writer).not.toHaveBeenCalled();
  });

  it("TIE 结论同样可提交（进入写入器 payload）", async () => {
    const values = {
      [ANNOTATION_ID]: { winner: "TIE" as const, confidence: null, rationale: null },
    };
    const { hook, writer } = renderSubmitter({ values });
    await act(async () => {
      await hook.result.current.submit();
    });
    const records = writer.mock.calls[0]![0];
    const preference = records[0]?.labels.find(
      (label) => label.label_key === "preference_pair",
    );
    expect((preference?.value as { winner: string } | undefined)?.winner).toBe("TIE");
  });
});
