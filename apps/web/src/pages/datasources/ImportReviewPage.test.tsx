import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DataReview, QualityReport } from "../../types/datasource";
import { QualitySection, ReviewSection } from "./ImportReviewPage";

const PROJECT = "11111111-1111-1111-1111-111111111111";
const IMPORT = "22222222-2222-2222-2222-222222222222";

const passingReport: QualityReport = {
  import_id: IMPORT,
  rule_version: "1.0.0",
  sample_scope: "IMPORT",
  total_assets: 39,
  total_episodes: 15,
  passed: true,
  findings: [
    { rule_id: "asset_present", passed: true, detail: "39 个资产均有对象键" },
  ],
  failures: [],
};

const failingReport: QualityReport = {
  ...passingReport,
  passed: false,
  findings: [
    ...passingReport.findings,
    {
      rule_id: "excerpt_within_duration",
      passed: false,
      detail: "片段 3 的摘录区间超出视频时长",
    },
  ],
  failures: [
    {
      rule_id: "excerpt_within_duration",
      passed: false,
      detail: "片段 3 的摘录区间超出视频时长",
    },
  ],
};

const pendingReview: DataReview = {
  review_id: "33333333-3333-3333-3333-333333333333",
  project_id: PROJECT,
  import_id: IMPORT,
  status: "PENDING",
  decision: null,
  reason: null,
  reviewer_id: null,
  submitted_by: "44444444-4444-4444-4444-444444444444",
  created_at: "2026-11-15T00:00:00Z",
  decided_at: null,
  version: 1,
};

function renderQuality(report: QualityReport | null, error: string | null = null) {
  return renderToStaticMarkup(
    <QualitySection
      report={report}
      loading={false}
      error={error}
      onReload={() => undefined}
    />,
  );
}

function renderReview(
  review: DataReview | null,
  forbidden = false,
  error: string | null = null,
) {
  return renderToStaticMarkup(
    <ReviewSection
      projectId={PROJECT}
      importId={IMPORT}
      review={review}
      loading={false}
      error={error}
      forbidden={forbidden}
      onReload={() => undefined}
    />,
  );
}

describe("质量报告区", () => {
  it("通过时显示规则版本与统计", () => {
    const html = renderQuality(passingReport);

    expect(html).toContain("质量校验通过");
    expect(html).toContain("1.0.0");
    expect(html).toContain("15");
    expect(html).toContain("39");
  });

  it("未通过时逐条列出失败规则，不做前端二次判定", () => {
    const html = renderQuality(failingReport);

    expect(html).toContain("质量校验未通过");
    expect(html).toContain("excerpt_within_duration");
    expect(html).toContain("摘录区间超出视频时长");
  });

  it("加载失败时显示错误与重试，不冒充空数据", () => {
    const html = renderQuality(null, "网络超时");

    expect(html).toContain("质量报告加载失败");
    expect(html).toContain("网络超时");
    expect(html.replace(/\s+/g, "")).toContain("重试");
  });

  it("确实没有报告时才显示空态", () => {
    expect(renderQuality(null)).toContain("尚无质量报告");
  });
});

describe("人工审核区", () => {
  it("待审核且有权时显示三种结论按钮", () => {
    const html = renderReview(pendingReview);

    expect(html).toContain("待审核");
    // AntD 会在两个汉字的按钮文案间插空格（"通 过"），因此断言按去空格后匹配。
    const flat = html.replace(/\s+/g, "");
    expect(flat).toContain("通过");
    expect(flat).toContain("需修正");
    expect(flat).toContain("拒绝");
  });

  it("无审核权限时解释原因，不渲染动作按钮", () => {
    const html = renderReview(pendingReview, true);

    expect(html).toContain("无审核权限");
    expect(html).toContain("职责分离");
    expect(html.replace(/\s+/g, "")).not.toContain("需修正");
  });

  it("已有结论时不再渲染动作按钮", () => {
    const html = renderReview({
      ...pendingReview,
      status: "APPROVED",
      decision: "ACCEPT",
      reviewer_id: "55555555-5555-5555-5555-555555555555",
      decided_at: "2026-11-15T01:00:00Z",
      reason: "抽查 3 条片段画面正常",
      version: 2,
    });

    expect(html).toContain("已通过");
    expect(html).toContain("抽查 3 条片段画面正常");
    expect(html.replace(/\s+/g, "")).not.toContain("需修正");
  });

  it("加载失败时显示错误与重试", () => {
    const html = renderReview(null, false, "请求被拒绝");

    expect(html).toContain("审核记录加载失败");
    expect(html).toContain("请求被拒绝");
  });

  it("尚未创建审核记录时显示空态", () => {
    expect(renderReview(null)).toContain("尚未创建审核记录");
  });
});
