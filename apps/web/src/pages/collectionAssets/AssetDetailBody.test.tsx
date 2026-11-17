import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  actionSpecFixture,
  planFixture,
  scenarioFixture,
  sopFixture,
} from "../../services/__fixtures__/collectionAssetFixtures";
import type { CollectionAsset } from "../../types/collectionAsset";
import AssetDetailBody from "./AssetDetailBody";
import type { AssetDetailBodyProps } from "./AssetDetailBody";

/** AntD 会在两个中文字符的按钮文本间插入空格（"编 辑"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderDetail(
  kind: AssetDetailBodyProps["kind"],
  asset: CollectionAsset,
  overrides: Partial<AssetDetailBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <AssetDetailBody
      kind={kind}
      asset={asset}
      busy={false}
      deleting={false}
      onAction={() => Promise.resolve()}
      onEdit={() => undefined}
      onDelete={() => undefined}
      {...overrides}
    />,
  );
}

describe("采集素材详情", () => {
  it("DRAFT 可编辑、可删除，且只有服务端给的 activate 动作", () => {
    const html = renderDetail("plans", planFixture());

    expect(html).toContain("PLAN-001");
    expect(html).toContain("草稿");
    expect(hasButton(html, "编辑")).toBe(true);
    expect(hasButton(html, "删除")).toBe(true);
    expect(hasButton(html, "激活")).toBe(true);
    expect(hasButton(html, "归档")).toBe(false);
    expect(html).not.toContain('disabled=""');
  });

  it("ACTIVE 只有 archive 动作，删除入口不可用", () => {
    const html = renderDetail(
      "plans",
      planFixture({ status: "ACTIVE", allowed_actions: ["archive"] }),
    );

    expect(hasButton(html, "归档")).toBe(true);
    expect(hasButton(html, "激活")).toBe(false);
    expect(html).toContain('disabled=""');
  });

  it("ARCHIVED 是终态：无动作按钮、编辑与删除都禁用", () => {
    const html = renderDetail(
      "plans",
      planFixture({ status: "ARCHIVED", allowed_actions: [] }),
    );

    expect(hasButton(html, "激活")).toBe(false);
    expect(hasButton(html, "归档")).toBe(false);
    expect(squash(html)).toContain("没有可执行的动作");
    expect(html).toContain("已归档素材是历史任务版本快照的事实载体");
    expect(html).toContain('disabled=""');
  });

  it("SOP 的步骤只读展示，并说明不可修改", () => {
    const html = renderDetail("sops", sopFixture());

    expect(html).toContain("步骤（任何状态都不可修改；内容变更请新建版本行）");
    expect(html).toContain("检查夹爪");
    expect(html).toContain("SOP 版本");
  });

  it("动作库动作只读展示", () => {
    const html = renderDetail("action-specs", actionSpecFixture());

    expect(html).toContain("动作（任何状态都不可修改；内容变更请新建版本行）");
    expect(html).toContain("grasp");
  });

  it("场景标签可展示，并标注不版本化", () => {
    const html = renderDetail("scenarios", scenarioFixture());

    expect(html).toContain("场景标签（不版本化，可修改；由服务端保序去重）");
    expect(html).toContain("室内");
    expect(html).toContain("货架");
  });
});
