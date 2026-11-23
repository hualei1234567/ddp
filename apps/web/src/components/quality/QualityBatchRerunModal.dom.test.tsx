import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  queuedQualityRunFixture,
  QUALITY_EPISODE_ID,
  QUALITY_OTHER_EPISODE_ID_FIXTURE,
} from "../../services/__fixtures__/qualityRunFixtures";
import QualityBatchRerunModal from "./QualityBatchRerunModal";
import type { QualityRerunSubject } from "./QualityBatchRerunModal";

/**
 * 批量重跑对话框交互测试（V31-033 / FR-026 + FR-028）。
 *
 * 语义红线（ADR-0045）：batch 响应是**排队回执**而不是质检结论——
 * 确认前展示预览数量；确认后渲染「已排队」并明确提示非结果，
 * 任何把入队成功渲染成「检查通过」的实现都会在这里失败。
 */
type ModalProps = Parameters<typeof QualityBatchRerunModal>[0];

function renderModal(overrides: Partial<ModalProps> = {}) {
  return render(
    <QualityBatchRerunModal
      open
      subjects={[{ subjectId: QUALITY_EPISODE_ID, lastStatus: null }]}
      scopeLabel="已选择的 Episode"
      rerun={vi.fn().mockResolvedValue([])}
      onClose={vi.fn()}
      {...overrides}
    />,
  );
}

describe("QualityBatchRerunModal（FR-026）", () => {
  const subjects: QualityRerunSubject[] = [
    { subjectId: QUALITY_EPISODE_ID, lastStatus: null },
    { subjectId: QUALITY_OTHER_EPISODE_ID_FIXTURE, lastStatus: null },
  ];

  it("确认前展示预览数量与范围说明，不出现任何结论文案", () => {
    renderModal({ subjects });

    expect(screen.getByText(/将重跑 2 条/)).toBeInTheDocument();
    expect(screen.getByText(/已选择的 Episode/)).toBeInTheDocument();
    // 预览阶段不得暗示任何结论。
    expect(screen.queryByText(/已排队/)).toBeNull();
    expect(screen.queryByText(/PASSED/)).toBeNull();
    expect(screen.queryByText(/检查通过/)).toBeNull();
  });

  it("确认后调用 rerun 并渲染排队回执；明确提示不是质检结论", async () => {
    const user = userEvent.setup();
    const rerun = vi.fn().mockResolvedValue([
      queuedQualityRunFixture(),
      queuedQualityRunFixture({ subject_id: QUALITY_OTHER_EPISODE_ID_FIXTURE }),
    ]);
    renderModal({ subjects, rerun });

    await user.click(screen.getByRole("button", { name: /确认重跑/ }));

    expect(rerun).toHaveBeenCalledTimes(1);
    expect(rerun).toHaveBeenCalledWith(subjects.map((item) => item.subjectId));
    expect(await screen.findByText(/已排队 2 条/)).toBeInTheDocument();
    // 红线文案：排队 ≠ 结论。
    expect(screen.getByText(/排队回执不是质检结论/)).toBeInTheDocument();
    expect(screen.getByText(/尚无质检结论/)).toBeInTheDocument();
    // 回执展示生命周期 QUEUED，不伪造结论状态。
    expect(screen.getAllByText(/QUEUED/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/PASSED/)).toBeNull();
    expect(screen.queryByText(/检查通过/)).toBeNull();
  });

  it("回执条目展示 run_id 与对象 ID，可逐条核对入队对象", async () => {
    const user = userEvent.setup();
    const queued = queuedQualityRunFixture();
    renderModal({
      rerun: vi.fn().mockResolvedValue([queued]),
    });

    await user.click(screen.getByRole("button", { name: /确认重跑/ }));

    expect(await screen.findByText(queued.run_id)).toBeInTheDocument();
    expect(screen.getByText(queued.subject_id)).toBeInTheDocument();
  });

  it("rerun 失败时保留预览并展示错误信息，不产生回执", async () => {
    const user = userEvent.setup();
    renderModal({
      rerun: vi.fn().mockRejectedValue(new Error("网络失败")),
    });

    await user.click(screen.getByRole("button", { name: /确认重跑/ }));

    expect(await screen.findByText(/重跑入队失败/)).toBeInTheDocument();
    expect(screen.queryByText(/已排队/)).toBeNull();
    // 仍停留在预览，可再次确认。
    expect(screen.getByRole("button", { name: /确认重跑/ })).toBeEnabled();
  });

  it("对象列表为空时确认按钮禁用并说明原因", () => {
    renderModal({ subjects: [] });

    expect(screen.getByText(/将重跑 0 条/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认重跑/ })).toBeDisabled();
  });

  it("回执阶段提供关闭按钮并回调 onClose", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({
      rerun: vi.fn().mockResolvedValue([queuedQualityRunFixture()]),
      onClose,
    });

    await user.click(screen.getByRole("button", { name: /确认重跑/ }));
    await screen.findByText(/已排队 1 条/);
    // antd 会在两个中文字符间插入空格，accessible name 是「关 闭」。
    await user.click(screen.getByRole("button", { name: /关\s*闭/ }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});

describe("QualityBatchRerunModal 失败项重试（FR-028）", () => {
  const failedEpisode = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const passedEpisode = QUALITY_EPISODE_ID;
  const incompleteEpisode = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const subjects: QualityRerunSubject[] = [
    {
      subjectId: failedEpisode,
      lastStatus: "FAILED",
    },
    {
      subjectId: passedEpisode,
      lastStatus: "PASSED",
    },
    { subjectId: incompleteEpisode, lastStatus: "INCOMPLETE" },
  ];

  it("默认重跑全部所选对象；开关打开后只重跑上一次结论为 FAILED 的对象", async () => {
    const user = userEvent.setup();
    const rerun = vi.fn().mockResolvedValue([queuedQualityRunFixture()]);
    renderModal({ subjects, rerun });

    await user.click(screen.getByRole("checkbox", { name: /只重试失败项/ }));
    expect(screen.getByText(/将重跑 1 条/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /确认重跑/ }));

    // FR-028 红线：PASSED / INCOMPLETE 对象绝不进入重试列表。
    expect(rerun).toHaveBeenCalledWith([failedEpisode]);
  });

  it("开关打开但没有 FAILED 对象时确认禁用并说明", async () => {
    const user = userEvent.setup();
    const rerun = vi.fn();
    renderModal({
      rerun,
      subjects: [
        { subjectId: passedEpisode, lastStatus: "PASSED" },
        { subjectId: incompleteEpisode, lastStatus: "INCOMPLETE" },
      ],
    });

    await user.click(screen.getByRole("checkbox", { name: /只重试失败项/ }));

    expect(
      screen.getByText(/上一次运行没有结论为 FAILED 的对象/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认重跑/ })).toBeDisabled();
    // 防御性断言：即使误触也不会发出请求。
    await user.click(screen.getByRole("button", { name: /确认重跑/ }));
    expect(rerun).not.toHaveBeenCalled();
  });
});
