import { describe, expect, it } from "vitest";

import {
  defaultFormatVersion,
  describeExportFormat,
  exportFormatOptions,
  isKnownExportFormat,
} from "./exportFormat";

describe("导出格式 ↔ 版本配对", () => {
  it("每种已知格式配到自己的版本，不交叉", () => {
    expect(defaultFormatVersion("LE_ROBOT")).toBe("ddp.lerobot/1.0.0");
    expect(defaultFormatVersion("CANONICAL")).toBe("ddp.canonical/1.0.0");
  });

  it("未知格式不猜版本，返回 null", () => {
    expect(defaultFormatVersion("PARQUET")).toBeNull();
    expect(defaultFormatVersion("")).toBeNull();
  });

  it("已知格式识别是精确匹配", () => {
    expect(isKnownExportFormat("CANONICAL")).toBe(true);
    expect(isKnownExportFormat("canonical")).toBe(false);
  });
});

describe("导出格式展示", () => {
  it("未知格式如实回显，不冒充已知格式", () => {
    expect(describeExportFormat("PARQUET")).toBe("未知导出格式：PARQUET");
  });

  it("Canonical 的说明不宣称可直接喂给 LeRobot Loader", () => {
    const hint = describeExportFormat("CANONICAL");
    expect(hint).toContain("不能直接喂给 LeRobot Loader");
  });
});

describe("下拉选项", () => {
  it("已知格式不重复追加", () => {
    expect(exportFormatOptions("CANONICAL").map((option) => option.value)).toEqual([
      "LE_ROBOT",
      "CANONICAL",
    ]);
  });

  it("已存在任务的未知格式可回显并标注", () => {
    const options = exportFormatOptions("PARQUET");
    expect(options).toContainEqual({ value: "PARQUET", label: "PARQUET（未知格式）" });
  });
});
