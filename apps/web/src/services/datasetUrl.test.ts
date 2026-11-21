import { describe, expect, it } from "vitest";

import {
  buildEpisodeSelectionFilters,
  datasetDetailPath,
  readEpisodeIdsParam,
} from "./datasetUrl";

const DATASET = "22222222-2222-4222-8222-222222222222";
const EP_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const EP_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

describe("数据集路径与 Episode 过滤", () => {
  it("未带 Episode 时保持原有详情路径", () => {
    expect(datasetDetailPath(DATASET)).toBe(`/datasets/${DATASET}`);
    expect(datasetDetailPath(DATASET, [])).toBe(`/datasets/${DATASET}`);
  });

  it("带 Episode 时去重后写入查询参数", () => {
    expect(datasetDetailPath(DATASET, [EP_A, EP_B, EP_A])).toBe(
      `/datasets/${DATASET}?episode_ids=${encodeURIComponent(`${EP_A},${EP_B}`)}`,
    );
  });

  it("从查询参数读回 Episode 并去掉空值与重复", () => {
    const params = new URLSearchParams({
      episode_ids: ` ${EP_A} , ,${EP_B},${EP_A}`,
    });
    expect(readEpisodeIdsParam(params)).toEqual([EP_A, EP_B]);
  });

  it("缺少参数时返回空数组", () => {
    expect(readEpisodeIdsParam(new URLSearchParams())).toEqual([]);
  });

  it("选择过滤条件按 selection_filters 契约生成 episode_ids", () => {
    expect(buildEpisodeSelectionFilters([EP_A, EP_A])).toEqual({
      episode_ids: [EP_A],
    });
  });
});
