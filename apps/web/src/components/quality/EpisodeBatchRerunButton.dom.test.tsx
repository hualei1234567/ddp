import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  queuedQualityRunFixture,
  QUALITY_EPISODE_ID,
  qualityRunFixture,
} from "../../services/__fixtures__/qualityRunFixtures";
import type { QualityRerunSubject } from "./QualityBatchRerunModal";
import EpisodeBatchRerunButton from "./EpisodeBatchRerunButton";

/**
 * Episode 列表页「批量重跑」入口按钮的交互测试（FR-026 / FR-028）。
 *
 * 按钮是自治容器：从已选 Episode 收集「可重跑对象 + 最近运行结论」，
 * 打开共享的 QualityBatchRerunModal；FR-028 的失败项语义在对话框内
 * 由开关完成，这里验证容器把正确数据交给对话框。
 */

const batchCreateRuns = vi.fn();
const listRuns = vi.fn();

vi.mock("../../services/qualityRuns", () => ({
  qualityRunApi: {
    batchCreateRuns: (...args: unknown[]) => batchCreateRuns(...args),
    listRuns: (...args: unknown[]) => listRuns(...args),
  },
}));

beforeEach(() => {
  batchCreateRuns.mockReset();
  listRuns.mockReset();
});

describe("EpisodeBatchRerunButton（FR-026）", () => {
  const episodeIds = [QUALITY_EPISODE_ID, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"];

  it("未选择对象时按钮禁用并提示需要选择", () => {
    render(
      <EpisodeBatchRerunButton projectId="p1" selectedIds={[]} />,
    );

    expect(screen.getByRole("button", { name: /批量重跑/ })).toBeDisabled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("点击后拉取各对象最近一次运行并打开预览；无运行的对象也进入范围", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue({ items: [], next_cursor: null });
    render(
      <EpisodeBatchRerunButton
        projectId="p1"
        selectedIds={episodeIds}
      />,
    );

    await user.click(screen.getByRole("button", { name: /批量重跑/ }));

    expect(await screen.findByText(/将重跑 2 条/)).toBeInTheDocument();
    // 每个对象都查询了最近一次运行（结论信息供 FR-028 筛选）。
    expect(listRuns).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/已选择的 2 条 Episode/)).toBeInTheDocument();
  });

  it("确认后按去重后的对象列表调用 batch API", async () => {
    const user = userEvent.setup();
    listRuns.mockResolvedValue({ items: [], next_cursor: null });
    batchCreateRuns.mockResolvedValue([queuedQualityRunFixture()]);
    const duplicateIds = [QUALITY_EPISODE_ID, QUALITY_EPISODE_ID];
    render(
      <EpisodeBatchRerunButton
        projectId="p1"
        selectedIds={duplicateIds}
      />,
    );

    await user.click(screen.getByRole("button", { name: /批量重跑/ }));
    await user.click(await screen.findByRole("button", { name: /确认重跑/ }));

    await waitFor(() =>
      expect(batchCreateRuns).toHaveBeenCalledWith(
        "p1",
        { subject_type: "EPISODE", subject_ids: [QUALITY_EPISODE_ID] },
      ),
    );
    expect(await screen.findByText(/已排队 1 条/)).toBeInTheDocument();
  });

  it("对象最近一次运行结论为 FAILED 时，对话框可勾选只重试失败项", async () => {
    const user = userEvent.setup();
    listRuns.mockImplementation(
      (_projectId: string, options: { subject_id: string }) =>
        Promise.resolve({
          items: [
            qualityRunFixture({
              run_id: `run-${options.subject_id.slice(0, 8)}`,
              subject_id: options.subject_id,
              status:
                options.subject_id === QUALITY_EPISODE_ID ? "FAILED" : "PASSED",
            }),
          ],
          next_cursor: null,
        }),
    );
    render(
      <EpisodeBatchRerunButton
        projectId="p1"
        selectedIds={episodeIds}
      />,
    );

    await user.click(screen.getByRole("button", { name: /批量重跑/ }));
    await screen.findByText(/将重跑 2 条/);
    await user.click(screen.getByRole("checkbox", { name: /只重试失败项/ }));

    expect(screen.getByText(/将重跑 1 条/)).toBeInTheDocument();
    // 「无运行信息」提示不应出现：两个对象都有最近运行结论。
    expect(screen.queryByText(/没有历史运行/)).toBeNull();
  });
});

describe("EpisodeBatchRerunButton 失败项预取（FR-028）", () => {
  it("subjects 属性直接提供结论时跳过逐对象查询", async () => {
    const user = userEvent.setup();
    const subjects: QualityRerunSubject[] = [
      { subjectId: QUALITY_EPISODE_ID, lastStatus: "FAILED" },
    ];
    render(
      <EpisodeBatchRerunButton
        projectId="p1"
        selectedIds={[QUALITY_EPISODE_ID]}
        subjects={subjects}
      />,
    );

    await user.click(screen.getByRole("button", { name: /批量重跑/ }));

    expect(await screen.findByText(/将重跑 1 条/)).toBeInTheDocument();
    expect(listRuns).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: /只重试失败项/ }));
    expect(screen.getByText(/将重跑 1 条/)).toBeInTheDocument();
  });
});
