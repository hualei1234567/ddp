import { describe, expect, it } from "vitest";

import {
  buildAnnotationGoldSetDetailPath,
  buildAnnotationGoldSetListPath,
  buildAnnotationIAADetailPath,
  buildAnnotationIAAListPath,
  buildAnnotationQualityDetailPath,
  buildAnnotationQualityListPath,
  buildQualityCheckPath,
  buildQualityOverviewPath,
  readIdQuery,
  readTaskIdQuery,
} from "./annotationUrl";

const PROJECT = "99999999-9999-7999-8999-999999999999";
const ID = "11111111-1111-7111-8111-111111111111";

function queryOf(url: string): URLSearchParams {
  return new URLSearchParams(url.slice(url.indexOf("?") + 1));
}

/**
 * 质量资源从 `/annotation/*` 迁到 `/quality/*` 后，深链必须全部走新地址：
 * 只要有一个 builder 漏改，菜单高亮、返回列表与外部链接就会分裂到两个路径族。
 */
describe("质量管理路径 builder", () => {
  const cases: Array<{ name: string; url: string; path: string }> = [
    { name: "总入口", url: buildQualityOverviewPath(PROJECT), path: "/quality" },
    {
      name: "黄金集列表",
      url: buildAnnotationGoldSetListPath(PROJECT),
      path: "/quality/gold-sets",
    },
    {
      name: "黄金集详情",
      url: buildAnnotationGoldSetDetailPath(PROJECT, ID),
      path: `/quality/gold-sets/${ID}`,
    },
    {
      name: "IAA 列表",
      url: buildAnnotationIAAListPath(PROJECT),
      path: "/quality/iaa-reports",
    },
    {
      name: "IAA 详情",
      url: buildAnnotationIAADetailPath(PROJECT, ID),
      path: `/quality/iaa-reports/${ID}`,
    },
    {
      name: "抽检列表",
      url: buildAnnotationQualityListPath(PROJECT),
      path: "/quality/quality-samples",
    },
    {
      name: "抽检详情",
      url: buildAnnotationQualityDetailPath(PROJECT, ID),
      path: `/quality/quality-samples/${ID}`,
    },
    {
      name: "质量检查",
      url: buildQualityCheckPath(PROJECT, ID),
      path: "/quality/quality-checks",
    },
  ];

  for (const item of cases) {
    it(`${item.name}指向 /quality 且带项目上下文`, () => {
      expect(item.url.startsWith(`${item.path}?`)).toBe(true);
      expect(queryOf(item.url).get("project_id")).toBe(PROJECT);
    });
  }

  it("按任务聚合的列表带上 task_id，详情不带", () => {
    expect(queryOf(buildAnnotationIAAListPath(PROJECT, ID)).get("task_id")).toBe(
      ID,
    );
    expect(
      queryOf(buildAnnotationQualityListPath(PROJECT, ID)).get("task_id"),
    ).toBe(ID);
    expect(queryOf(buildAnnotationIAAListPath(PROJECT)).get("task_id")).toBeNull();
    expect(queryOf(buildQualityCheckPath(PROJECT)).get("submission_id")).toBeNull();
  });

  it("ID 参与 URL 编码，不能被拼成额外路径段", () => {
    expect(buildQualityCheckPath(PROJECT, "a/b")).toContain("submission_id=a%2Fb");
  });
});

describe("查询参数读取", () => {
  it("空值与空白都视为未选择", () => {
    expect(readIdQuery(new URLSearchParams("submission_id="), "submission_id")).toBeNull();
    expect(readIdQuery(new URLSearchParams(""), "submission_id")).toBeNull();
    expect(
      readIdQuery(new URLSearchParams("submission_id=abc"), "submission_id"),
    ).toBe("abc");
  });

  it("readTaskIdQuery 复用同一套读取规则", () => {
    expect(readTaskIdQuery(new URLSearchParams("task_id= t1 "))).toBe("t1");
    expect(readTaskIdQuery(new URLSearchParams(""))).toBeNull();
  });
});
