// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import {
  qualityRunFindingFixture,
  qualityRunFixture,
} from "../../services/__fixtures__/qualityRunFixtures";
import type { ReworkContext } from "../../services/qualityEvidence";
import QualityEvidencePanel from "./QualityEvidencePanel";

/**
 * V51-010 证据定位面板交互测试。
 *
 * 三条红线必须由测试守住：
 * 1. 没有证据定位时如实说"无法定位"，不能渲染成定位到 0 时刻；
 * 2. 面板**不代替 Reviewer**：不自动推进提交状态，返工只是预填原因；
 * 3. 时间区间、流、字段三个维度分别可缺失，界面不能把缺失显示成有值。
 */

const EPISODE_START = "1763510400000000000";
const EPISODE_END = "1763510460000000000"; // +60s

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

function renderPanel(
  overrides: Partial<Parameters<typeof QualityEvidencePanel>[0]> = {},
) {
  const props: Parameters<typeof QualityEvidencePanel>[0] = {
    run: qualityRunFixture({ subject_type: "EPISODE" }),
    findings: [],
    episodeStartNs: EPISODE_START,
    episodeEndNs: EPISODE_END,
    selectedFindingId: null,
    onSelectFinding: () => undefined,
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <QualityEvidencePanel {...props} />
    </MemoryRouter>,
  );
}

const FAIL_WITH_TIME = qualityRunFindingFixture({
  finding_id: "11111111-1111-4111-8111-111111111111",
  rule_id: "stream_sync_aligned",
  rule_version: "1.1.0",
  category: "TEMPORAL",
  severity: "ERROR",
  status: "FAIL",
  message: "1 个片段同步未通过",
  score: 0,
  evidence: { tolerance_ns: 40_000_000, expected: 40_000_000, actual: 61_000_000 },
  evidence_location: {
    time_range: {
      start_time_ns: "1763510401500000000",
      end_time_ns: "1763510403000000000",
    },
    stream_id: "wrist",
    field_path: "streams",
  },
});

const FAIL_FIELD_ONLY = qualityRunFindingFixture({
  finding_id: "22222222-2222-4222-8222-222222222222",
  rule_id: "number_label_within_range",
  rule_version: "1",
  category: "ANNOTATION",
  severity: "ERROR",
  status: "FAIL",
  message: "1 个数值标签越界：velocity",
  score: 0,
  evidence: { expected: { min_value: 0, max_value: 5 }, actual: 9.5 },
  evidence_location: { time_range: null, stream_id: null, field_path: "labels.velocity" },
});

const PASS_FINDING = qualityRunFindingFixture({
  finding_id: "33333333-3333-4333-8333-333333333333",
  rule_id: "assets_present",
  status: "PASS",
  evidence_location: null,
});

describe("QC 证据定位面板", () => {
  it("列出可定位的未通过结论，不列通过项", () => {
    renderPanel({ findings: [FAIL_WITH_TIME, FAIL_FIELD_ONLY, PASS_FINDING] });

    expect(screen.getByText("stream_sync_aligned")).toBeInTheDocument();
    expect(screen.getByText("number_label_within_range")).toBeInTheDocument();
    // 通过项不会出现在"未通过结论可定位"列表里。
    expect(screen.queryByText("assets_present")).not.toBeInTheDocument();
  });

  it("没有未通过结论时如实说明，而不是显示空列表", () => {
    renderPanel({ findings: [PASS_FINDING] });

    expect(
      screen.getByText(/本次运行没有需要定位的未通过结论/),
    ).toBeInTheDocument();
  });

  it("点击「定位证据」回调选中结论", async () => {
    const onSelectFinding = vi.fn();
    renderPanel({ findings: [FAIL_WITH_TIME], onSelectFinding });

    // AntD 会在按钮 accessible name 里带上图标名，按正则取文本部分。
    await userEvent.click(
      screen.getByRole("button", { name: /定位证据/ }),
    );

    expect(onSelectFinding).toHaveBeenCalledWith(FAIL_WITH_TIME.finding_id);
  });

  it("选中后展示规则/版本/严重级别/字段/流/相对区间/期望-实际-阈值", () => {
    renderPanel({
      findings: [FAIL_WITH_TIME],
      selectedFindingId: FAIL_WITH_TIME.finding_id,
    });

    expect(screen.getByText("1.1.0")).toBeInTheDocument();
    expect(screen.getByText("streams")).toBeInTheDocument();
    expect(screen.getByText("wrist")).toBeInTheDocument();
    // 1.5s – 3.0s（绝对纳秒换算成相对秒）
    expect(screen.getByText("00:01.5 – 00:03.0")).toBeInTheDocument();
    // 期望/实际取自证据，不由前端重算
    expect(screen.getByText(/期望 40000000/)).toBeInTheDocument();
    expect(screen.getByText(/实际 61000000/)).toBeInTheDocument();
  });

  it("只有字段定位时明确说明播放器不跳转", () => {
    renderPanel({
      findings: [FAIL_FIELD_ONLY],
      selectedFindingId: FAIL_FIELD_ONLY.finding_id,
    });

    expect(
      screen.getByText(/该结论没有时间维度：播放器不跳转/),
    ).toBeInTheDocument();
    expect(screen.getByText("labels.velocity")).toBeInTheDocument();
  });

  it("没有证据定位时显示「无法定位」，不渲染成 0 时刻", () => {
    const noLocation = qualityRunFindingFixture({
      finding_id: "44444444-4444-4444-8444-444444444444",
      rule_id: "asset_sha256_valid",
      status: "FAIL",
      score: 0,
      evidence_location: null,
    });
    renderPanel({
      findings: [noLocation],
      selectedFindingId: noLocation.finding_id,
    });

    expect(screen.getByText("该结论无法定位")).toBeInTheDocument();
    expect(
      screen.getByText(/规则没有给出时间区间、流或字段/),
    ).toBeInTheDocument();
    // 不显示任何相对时刻。
    expect(screen.queryByText("00:00.0")).not.toBeInTheDocument();
  });

  it("红线条幅：面板明确声明不代替 Reviewer", () => {
    renderPanel({ findings: [FAIL_WITH_TIME] });

    expect(
      screen.getByText(/QC 提供证据与建议，不代替 Reviewer/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/仍由复核人按既有状态机与权限决定/),
    ).toBeInTheDocument();
  });

  it("创建返工时把规则版本与证据位置附进上下文", async () => {
    const onCreateRework = vi.fn<(context: ReworkContext) => void>();
    renderPanel({
      findings: [FAIL_WITH_TIME],
      selectedFindingId: FAIL_WITH_TIME.finding_id,
      onCreateRework,
    });

    await userEvent.click(
      screen.getByRole("button", { name: /用该证据创建返工/ }),
    );

    expect(onCreateRework).toHaveBeenCalledTimes(1);
    const context = onCreateRework.mock.calls[0]![0];
    expect(context.ruleId).toBe("stream_sync_aligned");
    expect(context.ruleVersion).toBe("1.1.0");
    expect(context.findingId).toBe(FAIL_WITH_TIME.finding_id);
    expect(context.reason).toContain("stream_sync_aligned@1.1.0");
    expect(context.reason).toContain("wrist");
  });

  it("父级未提供返工入口时按钮置灰并说明原因", () => {
    renderPanel({
      findings: [FAIL_WITH_TIME],
      selectedFindingId: FAIL_WITH_TIME.finding_id,
      reworkUnavailableReason: "当前提交不在可返工状态。",
    });

    expect(
      screen.getByRole("button", { name: /用该证据创建返工/ }),
    ).toBeDisabled();
    expect(screen.getByText("当前提交不在可返工状态。")).toBeInTheDocument();
  });

  it("返工请求进行中时按钮进入 loading 且不可重复提交", () => {
    renderPanel({
      findings: [FAIL_WITH_TIME],
      selectedFindingId: FAIL_WITH_TIME.finding_id,
      onCreateRework: () => undefined,
      reworkBusy: true,
    });

    expect(
      screen.getByRole("button", { name: /用该证据创建返工/ }),
    ).toBeDisabled();
  });
});
