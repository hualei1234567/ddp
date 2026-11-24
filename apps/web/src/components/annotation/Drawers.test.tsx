import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

/**
 * Drawer 用 Portal 渲染，`renderToStaticMarkup` 不会输出内容。
 * 这里只把 Drawer 换成普通容器，验证抽屉内的表单与提示文案；
 * 其余 antd 组件保持真实实现。
 */
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    Drawer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  };
});

import AssignmentCreateDrawer from "./AssignmentCreateDrawer";
import BatchCreateDrawer from "./BatchCreateDrawer";
import QualitySampleCreateDrawer from "./QualitySampleCreateDrawer";
import SampleSetCreateDrawer from "./SampleSetCreateDrawer";
import SchemaDefinitionDrawer from "./SchemaDefinitionDrawer";

const noop = () => undefined;

describe("标注写入口抽屉", () => {
  it("新建 Schema 提供编码、层级与标签定义", () => {
    const html = renderToStaticMarkup(
      <SchemaDefinitionDrawer
        open
        mode="create"
        onClose={noop}
        onSubmit={() => Promise.resolve()}
      />,
    );
    expect(html).toContain("编码");
    expect(html).toContain("标注层级");
    expect(html).toContain("标签定义");
    expect(html).toContain("添加标签");
  });

  it("新建样本集支持点名 Episode 与数据来源确认", () => {
    const html = renderToStaticMarkup(
      <SampleSetCreateDrawer open onClose={noop} onSubmit={() => Promise.resolve()} />,
    );
    expect(html).toContain("点名 Episode（可选）");
    expect(html).toContain("排除 Episode（可选）");
    expect(html).toContain("已确认数据来源与抽样风险提示");
  });

  it("新建批次要求计划项数", () => {
    const html = renderToStaticMarkup(
      <BatchCreateDrawer open onClose={noop} onSubmit={() => Promise.resolve()} />,
    );
    expect(html).toContain("计划项数");
    expect(html).toContain("条目引用（可选）");
  });

  it("没有批次时明确提示先建批次", () => {
    const html = renderToStaticMarkup(
      <AssignmentCreateDrawer
        open
        projectId="11111111-1111-7111-8111-111111111111"
        batches={[]}
        onClose={noop}
        onSubmit={() => Promise.resolve()}
      />,
    );
    expect(html).toContain("任务尚未创建批次，请先新建批次再指派人员。");
  });

  it("抽检抽屉提供按计划生成与手工创建两种模式", () => {
    const html = renderToStaticMarkup(
      <QualitySampleCreateDrawer
        open
        projectId="11111111-1111-7111-8111-111111111111"
        taskId="22222222-2222-7222-8222-222222222222"
        onClose={noop}
        onSubmit={() => Promise.resolve()}
      />,
    );
    expect(html).toContain("按计划生成");
    expect(html).toContain("手工创建");
    expect(html).toContain("抽样策略版本");
  });
});
