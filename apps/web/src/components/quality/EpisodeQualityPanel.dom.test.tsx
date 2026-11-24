import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  qualityRunDiffFixture,
  qualityRunFixture,
  queuedQualityRunFixture,
  QUALITY_BASELINE_RUN_ID,
  QUALITY_CANDIDATE_RUN_ID,
  QUALITY_EPISODE_ID,
} from "../../services/__fixtures__/qualityRunFixtures";
import EpisodeQualityPanel from "./EpisodeQualityPanel";

/**
 * Episode 详情页质检面板交互测试（FR-026 / FR-027 / FR-028）。
 *
 * 面板职责：展示该 Episode 的运行历史；选择候选运行与基线（最近完成
 * 运行）对比；提供单对象重跑与失败项重试入口。
 */

const listRuns = vi.fn();
const getRunDiff = vi.fn();
const batchCreateRuns = vi.fn();

vi.mock("../../services/qualityRuns", () => ({
  qualityRunApi: {
    listRuns: (...args: unknown[]) => listRuns(...args),
    getRunDiff: (...args: unknown[]) => getRunDiff(...args),
    batchCreateRuns: (...args: unknown[]) => batchCreateRuns(...args),
  },
}));

beforeEach(() => {
  listRuns.mockReset();
  getRunDiff.mockReset();
  batchCreateRuns.mockReset();
});

const historyPage = {
  items: [
    qualityRunFixture({
      run_id: QUALITY_BASELINE_RUN_ID,
      subject_id: QUALITY_EPISODE_ID,
      status: "FAILED",
      created_at: "2026-11-19T08:00:00Z",
    }),
    qualityRunFixture({
      run_id: QUALITY_CANDIDATE_RUN_ID,
      subject_id: QUALITY_EPISODE_ID,
      status: "PASSED",
      created_at: "2026-11-20T08:00:00Z",
    }),
  ],
  next_cursor: null,
};

function renderPanel() {
  // 面板内含「查看证据」链接（V51-010），需要 Router 上下文。
  return render(
    <MemoryRouter>
      <EpisodeQualityPanel projectId="p1" episodeId={QUALITY_EPISODE_ID} />
    </MemoryRouter>,
  );
}

describe("EpisodeQualityPanel 运行历史", () => {
  it("展示运行列表与整体结论；结论来自服务端不重新判定", async () => {
    listRuns.mockResolvedValue(historyPage);

    renderPanel();

    expect(await screen.findByText(QUALITY_BASELINE_RUN_ID)).toBeInTheDocument();
    expect(screen.getByText(QUALITY_CANDIDATE_RUN_ID)).toBeInTheDocument();
    // 最近完成（基线）标记 + 每条运行的整体结论。
    expect(screen.getAllByText(/基线/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/FAILED/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/PASSED/).length).toBeGreaterThan(0);
  });

  it("没有历史运行时展示空态", async () => {
    listRuns.mockResolvedValue({ items: [], next_cursor: null });

    renderPanel();

    expect(await screen.findByText(/尚未质检/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /对比/ })).toBeNull();
  });
});

describe("EpisodeQualityPanel 差异报告（FR-027）", () => {
  it("为每条可对比运行提供「与基线对比」入口，点击后展示四类分组", async () => {
    const user = userEvent.setup();
    listRuns
      .mockResolvedValueOnce(historyPage)
      .mockResolvedValueOnce(historyPage);
    getRunDiff.mockResolvedValue(qualityRunDiffFixture());

    renderPanel();

    const compareButtons = await screen.findAllByRole("button", {
      name: /与基线对比/,
    });
    expect(compareButtons.length).toBe(1); // 只有非基线（候选）行有对比入口。
    await user.click(compareButtons[0]!);

    expect(getRunDiff).toHaveBeenCalledWith(
      "p1",
      QUALITY_BASELINE_RUN_ID,
      QUALITY_CANDIDATE_RUN_ID,
    );
    expect(await screen.findByText(/新增问题/)).toBeInTheDocument();
    expect(screen.getByText(/已消除问题/)).toBeInTheDocument();
    // 分组标题带计数；「无变化」同时出现在分组标题与说明里，用 getAllByText。
    expect(screen.getAllByText(/无变化/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/结论变化/).length).toBeGreaterThan(0);
  });

  it("候选与基线是同一条运行时不提供对比入口", async () => {
    listRuns.mockResolvedValue({
      items: [historyPage.items[0]!],
      next_cursor: null,
    });

    renderPanel();

    await screen.findByText(QUALITY_BASELINE_RUN_ID);
    expect(screen.queryByRole("button", { name: /与基线对比/ })).toBeNull();
  });

  it("diff 请求失败时展示错误并保留历史", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue(historyPage);
    getRunDiff.mockRejectedValue(new Error("422"));

    renderPanel();

    const compareButtons = await screen.findAllByRole("button", {
      name: /与基线对比/,
    });
    await user.click(compareButtons[0]!);

    expect(await screen.findByText(/差异报告加载失败/)).toBeInTheDocument();
    expect(screen.getByText(QUALITY_BASELINE_RUN_ID)).toBeInTheDocument();
  });
});

describe("EpisodeQualityPanel 重跑与失败项重试（FR-026 / FR-028）", () => {
  it("提供单对象重跑入口，确认后走 batch API；回执明确非结论", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue(historyPage);
    batchCreateRuns.mockResolvedValue([queuedQualityRunFixture()]);

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /重新质检/ }));
    await user.click(await screen.findByRole("button", { name: /确认重跑/ }));

    expect(batchCreateRuns).toHaveBeenCalledWith("p1", {
      subject_type: "EPISODE",
      subject_ids: [QUALITY_EPISODE_ID],
    });
    // 对话框与面板各有一条红线提示。
    expect(
      await screen.findAllByText(/排队回执不是质检结论/),
    ).not.toHaveLength(0);
  });

  it("最近完成运行结论为 FAILED 时，预选只重试失败项", async () => {
    const user = userEvent.setup();
    // 基线（最近完成）是 FAILED；候选是 PASSED。
    listRuns.mockResolvedValue(historyPage);
    batchCreateRuns.mockResolvedValue([queuedQualityRunFixture()]);

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /重新质检/ }));
    // 详情页单对象场景：FAILED 的对象本身就是重试范围。
    expect(await screen.findByText(/将重跑 1 条/)).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /只重试失败项/ }),
    ).toBeChecked();
  });
});

describe("V51-010 证据定位入口", () => {
  it("每条运行都给出「查看证据」深链，指向 Episode 页并带上 qc_run_id", async () => {
    listRuns.mockResolvedValue(historyPage);

    renderPanel();

    const links = await screen.findAllByRole("link", { name: /定位运行.*的证据/ });
    expect(links.length).toBe(historyPage.items.length);
    expect(links[0]).toHaveAttribute(
      "href",
      expect.stringContaining(`qc_run_id=${QUALITY_BASELINE_RUN_ID}`),
    );
    // 深链必须落在 Episode 详情页（播放器与时间轴在那里，蓝图 §1 不新建播放器）。
    expect(links[0]!.getAttribute("href")).toContain(
      `/episodes/${QUALITY_EPISODE_ID}`,
    );
  });
});
