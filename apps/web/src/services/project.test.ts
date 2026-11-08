import { describe, expect, it } from "vitest";

import {
  buildProjectQuery,
  isProjectId,
  resolveProjectId,
} from "./project";

const projectA = "11111111-1111-7111-8111-111111111111";
const projectB = "22222222-2222-7222-8222-222222222222";
const projectC = "33333333-3333-7333-8333-333333333333";

describe("项目上下文", () => {
  it("只接受 UUID 格式的项目 ID", () => {
    expect(isProjectId(projectA)).toBe(true);
    expect(isProjectId(projectA.toUpperCase())).toBe(true);
    expect(isProjectId("demo-project")).toBe(false);
    expect(isProjectId("")).toBe(false);
    expect(isProjectId(null)).toBe(false);
  });

  it("优先级为 URL > 会话缓存 > 环境配置", () => {
    expect(
      resolveProjectId({
        query: projectA,
        stored: projectB,
        env: projectC,
      }),
    ).toBe(projectA);

    expect(
      resolveProjectId({ query: "invalid", stored: projectB, env: projectC }),
    ).toBe(projectB);

    expect(
      resolveProjectId({ query: null, stored: null, env: projectC }),
    ).toBe(projectC);
  });

  it("没有合法来源时返回 null，不使用硬编码默认项目", () => {
    expect(
      resolveProjectId({ query: null, stored: "x", env: undefined }),
    ).toBeNull();
  });

  it("输出统一小写并在跳转查询中编码", () => {
    expect(
      resolveProjectId({
        query: projectA.toUpperCase(),
        stored: null,
        env: undefined,
      }),
    ).toBe(projectA);
    expect(buildProjectQuery(projectA)).toBe(`?project_id=${projectA}`);
    expect(buildProjectQuery(null)).toBe("");
  });
});
