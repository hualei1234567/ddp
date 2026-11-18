import { describe, expect, it } from "vitest";

import {
  presentImportProgress,
  presentImportStage,
} from "./importProgress";

describe("导入阶段展示", () => {
  it("已知阶段映射为中文", () => {
    expect(presentImportStage("DOWNLOAD")).toBe("下载");
    expect(presentImportStage("VALIDATE")).toBe("校验");
    expect(presentImportStage("PARSE")).toBe("解析");
    expect(presentImportStage("INGEST")).toBe("入库");
    expect(presentImportStage("COMPLETE")).toBe("完成");
  });

  it("null 阶段显示未测量，不默认成下载", () => {
    expect(presentImportStage(null)).toBe("未测量");
  });
});

describe("导入进度展示", () => {
  it("两侧都有值时计算真实百分比", () => {
    const view = presentImportProgress(3, 10);

    expect(view).toEqual({ measured: true, label: "3 / 10", percent: 30 });
  });

  it("任一值为 null 时显示未测量，percent 为 null 而不是 0", () => {
    expect(presentImportProgress(null, 10)).toEqual({
      measured: false,
      label: "未测量",
      percent: null,
    });
    expect(presentImportProgress(3, null)).toEqual({
      measured: false,
      label: "未测量",
      percent: null,
    });
    expect(presentImportProgress(null, null)).toEqual({
      measured: false,
      label: "未测量",
      percent: null,
    });
  });

  it("总数为 0 时不能当分母", () => {
    expect(presentImportProgress(0, 0)).toEqual({
      measured: false,
      label: "未测量",
      percent: null,
    });
  });

  it("已完成时是 100%，不因四舍五入溢出", () => {
    expect(presentImportProgress(10, 10).percent).toBe(100);
  });
});
