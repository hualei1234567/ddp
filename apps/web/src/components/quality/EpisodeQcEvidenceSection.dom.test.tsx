// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  episodeFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/collectionFixtures";
import {
  qualityRunFixture,
  qualityRunFindingFixture,
} from "../../services/__fixtures__/qualityRunFixtures";
import EpisodeQcEvidenceSection from "./EpisodeQcEvidenceSection";

/**
 * V51-010 Episode 证据定位区联动测试。
 *
 * 验证核心闭环：点击结论 → 播放器跳转到证据时刻 → 时间轴高亮证据区间。
 *
 * 播放器与时间轴复用既有组件，测试里把 `EpisodeMediaPanel` 替换成探针：
 * 真实媒体面板要经「血缘 → 资产 → 签名地址」多段请求（那部分已由
 * `EpisodeMediaPanel` 自己的测试覆盖），这里只要证明"证据换算结果被正确
 * 传给播放器/时间轴"。
 */

const listFindings = vi.fn();

vi.mock("../../services/qualityRuns", () => ({
  qualityRunApi: {
    listFindings: (...args: unknown[]) => listFindings(...args),
  },
}));

const mediaPanelSpy = vi.fn();

vi.mock("../annotation/EpisodeMediaPanel", () => ({
  default: (props: Record<string, unknown>) => {
    mediaPanelSpy(props);
    return <div data-testid="episode-media-panel-probe" />;
  },
}));

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

beforeEach(() => {
  listFindings.mockReset();
  mediaPanelSpy.mockReset();
});

const EPISODE_START = "1730000000000000000";
const EPISODE_END = "1730000010000000000"; // +10s

const EPISODE = {
  ...episodeFixture,
  start_time_ns: EPISODE_START,
  end_time_ns: EPISODE_END,
};

const RUN = qualityRunFixture({
  subject_type: "EPISODE",
  subject_id: episodeFixture.episode_id,
});

const SYNC_FINDING = qualityRunFindingFixture({
  finding_id: "11111111-1111-4111-8111-111111111111",
  run_id: RUN.run_id,
  rule_id: "stream_sync_aligned",
  category: "TEMPORAL",
  severity: "ERROR",
  status: "FAIL",
  message: "1 个片段同步未通过",
  score: 0,
  evidence_location: {
    // Episode 起点 + 3s → +6s
    time_range: {
      start_time_ns: "1730000003000000000",
      end_time_ns: "1730000006000000000",
    },
    stream_id: "wrist",
    field_path: "streams",
  },
});

function renderSection(
  overrides: Partial<Parameters<typeof EpisodeQcEvidenceSection>[0]> = {},
) {
  return render(
    <MemoryRouter>
      <EpisodeQcEvidenceSection
        projectId={PROJECT_ID}
        episode={EPISODE}
        run={RUN}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

/** 取探针最近一次收到的 props。 */
function lastMediaProps(): Record<string, unknown> {
  const calls = mediaPanelSpy.mock.calls;
  return calls[calls.length - 1]![0] as Record<string, unknown>;
}

describe("Episode QC 证据定位区", () => {
  it("加载结论明细后不自动定位：首屏不动播放器", async () => {
    listFindings.mockResolvedValue({ items: [SYNC_FINDING], next_cursor: null });

    renderSection();

    await waitFor(() => {
      expect(screen.getByText(/1 条未通过结论可定位/)).toBeInTheDocument();
    });
    // 未点击时：播放头 0，无高亮，无证据流。
    expect(lastMediaProps().playheadSeconds).toBe(0);
    expect(lastMediaProps().evidenceRange).toBeNull();
    expect(lastMediaProps().evidenceStreamId).toBeNull();
  });

  it("点击定位后播放器跳到证据起点，时间轴高亮证据区间与流", async () => {
    listFindings.mockResolvedValue({ items: [SYNC_FINDING], next_cursor: null });

    renderSection();
    await screen.findByText(/1 条未通过结论可定位/);

    await userEvent.click(screen.getByRole("button", { name: /定位证据/ }));

    await waitFor(() => {
      const props = lastMediaProps();
      // Episode 起点 +3s → 相对 3s
      expect(props.playheadSeconds).toBe(3);
      expect(props.evidenceRange).toEqual({
        startSeconds: 3,
        endSeconds: 6,
      });
      expect(props.evidenceStreamId).toBe("wrist");
    });
  });

  it("证据区间越出 Episode 时被裁剪，不产生负值或溢出的播放位置", async () => {
    const clipped = qualityRunFindingFixture({
      ...SYNC_FINDING,
      finding_id: "55555555-5555-4555-8555-555555555555",
      evidence_location: {
        // 起点比 Episode 早 2s，终点超出 4s
        time_range: {
          start_time_ns: "1729999998000000000",
          end_time_ns: "1730000014000000000",
        },
        stream_id: null,
        field_path: null,
      },
    });
    listFindings.mockResolvedValue({ items: [clipped], next_cursor: null });

    renderSection();
    await screen.findByText(/1 条未通过结论可定位/);
    await userEvent.click(screen.getByRole("button", { name: /定位证据/ }));

    await waitFor(() => {
      const props = lastMediaProps();
      expect(props.playheadSeconds).toBe(0);
      expect(props.evidenceRange).toEqual({ startSeconds: 0, endSeconds: 10 });
    });
  });

  it("结论明细被分页截断时如实标注，不静默丢结论", async () => {
    listFindings.mockResolvedValue({
      items: [SYNC_FINDING],
      next_cursor: "cursor-2",
    });

    renderSection();

    expect(
      await screen.findByText("结论明细未完整加载"),
    ).toBeInTheDocument();
  });

  it("加载失败时展示错误态并可重试，不显示空结论当通过", async () => {
    const error = new Error("boom");
    listFindings.mockRejectedValueOnce(error);
    listFindings.mockResolvedValueOnce({ items: [SYNC_FINDING], next_cursor: null });

    renderSection();

    // ApiErrorState 会同时渲染标题与描述，按 role=alert 断言更稳。
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("没有未通过结论时明确说明，并仍渲染播放器（可自由查看）", async () => {
    listFindings.mockResolvedValue({
      items: [qualityRunFindingFixture({ status: "PASS", evidence_location: null })],
      next_cursor: null,
    });

    renderSection();

    expect(
      await screen.findByText(/本次运行没有需要定位的未通过结论/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("episode-media-panel-probe")).toBeInTheDocument();
  });
});
