import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  describeAnnotationActionError,
  isSeparationError,
  presentAnnotationStatus,
  presentProgress,
} from "./annotationPresentation";

function httpError(status: number, code: string): ApiError {
  return new ApiError({
    kind: "http",
    status,
    code,
    requestId: "req-1",
    message: "服务端拒绝",
  });
}

describe("标注状态与错误展示", () => {
  it("已知状态映射中文标签与色彩语义", () => {
    expect(presentAnnotationStatus("IN_PROGRESS").label).toBe("进行中");
    expect(presentAnnotationStatus("APPROVED").tone).toBe("success");
    expect(presentAnnotationStatus("REJECTED").tone).toBe("error");
    expect(presentAnnotationStatus("INSUFFICIENT_DATA").label).toBe("样本不足");
    expect(presentAnnotationStatus("UNKNOWN-STATUS").label).toBe("UNKNOWN-STATUS");
  });

  it("进度缺失显示 N/A，而不是 0", () => {
    expect(presentProgress({ submitted_items: 3 }, "submitted_items")).toBe("3");
    expect(presentProgress({}, "submitted_items")).toBe("N/A");
    expect(presentProgress({}, "missing_semantics")).toBe("N/A");
  });

  it("复核自审 403 显示职责分离说明并保留错误码", () => {
    const feedback = describeAnnotationActionError(
      httpError(403, "annotation_review_self_forbidden"),
    );
    expect(feedback.title).toContain("职责分离");
    expect(feedback.description).toContain("annotation_review_self_forbidden");
    expect(feedback.description).toContain("req-1");
  });

  it("仲裁人冲突 403 显示职责分离说明", () => {
    const feedback = describeAnnotationActionError(
      httpError(403, "annotation_arbiter_conflict"),
    );
    expect(feedback.title).toContain("职责分离");
    expect(feedback.description).toContain("annotation_arbiter_conflict");
  });

  it("黄金集答案 403 使用专用文案", () => {
    const feedback = describeAnnotationActionError(
      httpError(403, "gold_set_access_denied"),
    );
    expect(feedback.title).toBe("黄金集期望答案不可见");
    expect(feedback.description).toContain("gold_set_access_denied");
    expect(feedback.description).toContain("审计");
  });

  it("版本冲突提示保留本地输入并刷新", () => {
    const feedback = describeAnnotationActionError(
      httpError(409, "version_conflict"),
    );
    expect(feedback.kind).toBe("warning");
    expect(feedback.description).toContain("本地输入已保留");
  });

  it("职责分离错误码集合正确识别", () => {
    expect(
      isSeparationError(httpError(403, "annotation_review_self_forbidden")),
    ).toBe(true);
    expect(isSeparationError(httpError(403, "gold_set_access_denied"))).toBe(
      true,
    );
    expect(isSeparationError(httpError(403, "forbidden"))).toBe(false);
  });
});
