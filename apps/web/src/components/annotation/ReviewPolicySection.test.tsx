import { Form } from "antd";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ReviewPolicySection, {
  type ReviewPolicySectionProps,
} from "./ReviewPolicySection";

const noop = () => undefined;

function renderSection(
  overrides: Partial<ReviewPolicySectionProps> = {},
): string {
  const props: ReviewPolicySectionProps = {
    reviewMode: "SINGLE",
    autoApprove: false,
    canAutoApprove: false,
    onAutoApproveChange: noop,
    ...overrides,
  };
  return renderToStaticMarkup(
    <Form
      initialValues={{
        review_mode: props.reviewMode,
        auto_approve: props.autoApprove,
      }}
    >
      <ReviewPolicySection {...props} />
    </Form>,
  );
}

describe("创建任务页双标配置区", () => {
  it("默认单人模式只展示复核模式，不展示阈值与自动通过", () => {
    const html = renderSection();

    expect(html).toContain("单人（SINGLE）");
    expect(html).toContain("双标（DOUBLE）");
    expect(html).not.toContain("一致性阈值（%）");
    expect(html).not.toContain("ant-checkbox");
  });

  it("切到双标后展示一致性指标与阈值输入", () => {
    const html = renderSection({ reviewMode: "DOUBLE" });

    expect(html).toContain("一致性阈值（%）");
    expect(html).toContain("判定指标");
    expect(html).toContain("百分比一致率");
  });

  it("有权限时自动通过默认未勾选，并提示需要二次确认", () => {
    const html = renderSection({
      reviewMode: "DOUBLE",
      canAutoApprove: true,
      autoApprove: false,
    });

    expect(html).toContain("达标后自动通过提交");
    expect(html).toContain("ant-checkbox");
    expect(html).not.toContain("ant-checkbox-checked");
    expect(html).toContain("默认关闭");
    expect(html).toContain("二次确认");
  });

  it("无权限时隐藏开关，并声明服务端仍是权威", () => {
    const html = renderSection({
      reviewMode: "DOUBLE",
      canAutoApprove: false,
    });

    expect(html).not.toContain("ant-checkbox");
    expect(html).not.toContain("达标后自动通过提交");
    expect(html).toContain("仅项目负责人可开启");
    expect(html).toContain("服务端仍会校验权限");
  });

  it("已勾选时展示生效后果，便于提交前复核", () => {
    const html = renderSection({
      reviewMode: "DOUBLE",
      canAutoApprove: true,
      autoApprove: true,
    });

    expect(html).toContain("ant-checkbox-checked");
    expect(html).toContain("自动通过已开启");
    expect(html).toContain("项目负责人身份");
    expect(html).toContain("未达标仍会升级仲裁");
  });
});
