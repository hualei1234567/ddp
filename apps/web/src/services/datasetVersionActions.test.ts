import { describe, expect, it } from "vitest";

import {
  datasetVersionActionConfirm,
  datasetVersionActionPayload,
} from "./datasetVersionActions";

describe("数据集版本动作交互契约", () => {
  it("冻结必须带上 qa_verified，否则服务端 422", () => {
    expect(datasetVersionActionPayload("freeze")).toEqual({ qa_verified: true });
  });

  it("冻结需要二次确认，文案说明不可变与 QA 前置", () => {
    const text = datasetVersionActionConfirm("freeze");
    expect(text).toContain("不可变");
    expect(text).toContain("QA");
  });

  it("其他动作不带 payload、不弹额外确认（原因由 ActionButtons 统一处理）", () => {
    for (const action of ["validate", "archive", "build", "ready"]) {
      expect(datasetVersionActionPayload(action)).toBeNull();
      expect(datasetVersionActionConfirm(action)).toBeNull();
    }
  });
});
