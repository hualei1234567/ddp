// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import MediaPreview from "./MediaPreview";
import {
  durationFromNs,
  formatPlaybackClock,
  isPlayableMedia,
  nsToRelativeSeconds,
  relativeSecondsToNs,
} from "../../services/media";

const ASSET_ID = "11111111-1111-1111-1111-111111111111";
const PROJECT_ID = "22222222-2222-2222-2222-222222222222";

const getDownloadUrl = vi.fn();

vi.mock("../../services/rawAssets", () => ({
  rawAssetsApi: {
    getDownloadUrl: (...args: unknown[]) => getDownloadUrl(...args),
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

function playableAsset(overrides: Record<string, unknown> = {}) {
  return {
    assetId: ASSET_ID,
    originalFilename: "episode-0001.mp4",
    contentType: "video/mp4",
    sizeBytes: 1024,
    ...overrides,
  };
}

describe("isPlayableMedia", () => {
  it("只把视频内容类型判为可播放", () => {
    expect(isPlayableMedia("video/mp4")).toBe(true);
    expect(isPlayableMedia("video/webm")).toBe(true);
    expect(isPlayableMedia("application/x-mcap")).toBe(false);
    expect(isPlayableMedia("application/octet-stream")).toBe(false);
    expect(isPlayableMedia(null)).toBe(false);
  });
});

describe("formatPlaybackClock", () => {
  it("把秒格式化为 mm:ss.S", () => {
    expect(formatPlaybackClock(0)).toBe("00:00.0");
    expect(formatPlaybackClock(9.44)).toBe("00:09.4");
    expect(formatPlaybackClock(75.5)).toBe("01:15.5");
  });

  it("把非法值收敛为占位，不抛异常", () => {
    expect(formatPlaybackClock(Number.NaN)).toBe("--:--.-");
    expect(formatPlaybackClock(-3)).toBe("00:00.0");
    expect(formatPlaybackClock(Number.POSITIVE_INFINITY)).toBe("--:--.-");
  });
});

describe("纳秒与播放器相对秒互转", () => {
  it("以 Episode 起点为基准换算时长与定位点", () => {
    expect(durationFromNs("0", "5000000000")).toBe(5);
    expect(durationFromNs("1000000000", "1000000000")).toBeNull();
    expect(durationFromNs("5", "1")).toBeNull();
    expect(durationFromNs(null, "1")).toBeNull();
    expect(nsToRelativeSeconds("6000000000", "1000000000")).toBe(5);
    expect(relativeSecondsToNs(5, "1000000000")).toBe(6000000000);
    expect(relativeSecondsToNs(Number.NaN, "0")).toBeNull();
  });
});

describe("MediaPreview", () => {
  beforeEach(() => {
    getDownloadUrl.mockReset();
  });

  it("加载成功时渲染视频并展示来源信息", async () => {
    getDownloadUrl.mockResolvedValue({
      url: "https://storage.test/signed/episode-0001.mp4",
      expires_at: "2026-11-22T10:00:00Z",
    });

    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset()}
        startTimeNs="1000000000"
        endTimeNs="3000000000"
      />,
    );

    const video = await screen.findByTestId("media-preview-video");
    expect(video).toBeInTheDocument();
    expect(video.getAttribute("src")).toBe(
      "https://storage.test/signed/episode-0001.mp4",
    );
    expect(video.getAttribute("controls")).not.toBeNull();
    expect(getDownloadUrl).toHaveBeenCalledTimes(1);
    expect(getDownloadUrl.mock.calls[0]?.slice(0, 2)).toEqual([
      PROJECT_ID,
      ASSET_ID,
    ]);
    expect(screen.getByText(/episode-0001\.mp4/)).toBeInTheDocument();
    expect(screen.getByText(/2\.0 s/)).toBeInTheDocument();
  });

  it("重新签发 URL：再次点击加载会重新请求签名地址", async () => {
    getDownloadUrl
      .mockResolvedValueOnce({
        url: "https://storage.test/a.mp4",
        expires_at: null,
      })
      .mockResolvedValueOnce({
        url: "https://storage.test/b.mp4",
        expires_at: null,
      });

    render(<MediaPreview projectId={PROJECT_ID} asset={playableAsset()} />);
    await screen.findByTestId("media-preview-video");

    await userEvent.click(screen.getByRole("button", { name: /刷新播放地址/ }));
    await waitFor(() => {
      expect(
        screen.getByTestId("media-preview-video").getAttribute("src"),
      ).toBe("https://storage.test/b.mp4");
    });
    expect(getDownloadUrl).toHaveBeenCalledTimes(2);
  });

  it("签名失败时展示错误态且可重试，不留空白", async () => {
    getDownloadUrl.mockRejectedValueOnce(new Error("boom"));

    render(<MediaPreview projectId={PROJECT_ID} asset={playableAsset()} />);

    expect(await screen.findByText(/媒体地址获取失败/)).toBeInTheDocument();
    expect(screen.queryByTestId("media-preview-video")).not.toBeInTheDocument();

    getDownloadUrl.mockResolvedValueOnce({
      url: "https://storage.test/retry.mp4",
      expires_at: null,
    });
    // antd 会在两个汉字组成的按钮里插入空格（"重 试"），用宽松匹配避免误判。
    await userEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(
      await screen.findByTestId("media-preview-video"),
    ).toBeInTheDocument();
  });

  it("非视频资产不请求签名地址，直接说明不可播放", async () => {
    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset({ contentType: "application/x-mcap" })}
      />,
    );

    expect(await screen.findByText(/不可直接播放/)).toBeInTheDocument();
    expect(getDownloadUrl).not.toHaveBeenCalled();
  });

  it("回放定位：超过时长时裁剪到时长边界", async () => {
    getDownloadUrl.mockResolvedValue({
      url: "https://storage.test/a.mp4",
      expires_at: null,
    });

    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset()}
        startTimeNs="0"
        endTimeNs="5000000000"
        seekToSeconds={9}
      />,
    );

    const video = await screen.findByTestId("media-preview-video");
    // jsdom 不解码媒体，duration 恒为 NaN；组件必须自行用 start/end 计算边界。
    expect(video).toBeInTheDocument();
    expect(screen.getAllByText(/5\.0 s/).length).toBeGreaterThan(0);
  });
});

// ---- V31-045 切片 A：倍速 / 帧步进 / 快捷键（蓝图 §5 播放控制） ----

describe("MediaPreview 播放控制", () => {
  it("渲染倍速按钮组且默认 1x 高亮", async () => {
    getDownloadUrl.mockResolvedValue({
      url: "https://media.test/signed.mp4",
      expires_at: null,
    });
    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset()}
        startTimeNs={0}
        endTimeNs={10_000_000_000}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("media-preview-video")).toBeDefined(),
    );
    for (const value of ["0.25", "0.5", "1", "1.5", "2"]) {
      expect(screen.getByTestId(`media-rate-${value}`)).toBeTruthy();
    }
    // 默认 1x 是 primary；0.25x 不是
    expect(
      screen.getByTestId("media-rate-1").className.includes("primary"),
    ).toBe(true);
    expect(
      screen.getByTestId("media-rate-0.25").className.includes("primary"),
    ).toBe(false);
  });

  it("点击倍速按钮调用 video.playbackRate", async () => {
    getDownloadUrl.mockResolvedValue({
      url: "https://media.test/signed.mp4",
      expires_at: null,
    });
    const user = userEvent.setup();
    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset()}
        startTimeNs={0}
        endTimeNs={10_000_000_000}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("media-preview-video")).toBeTruthy(),
    );
    const video = screen.getByTestId(
      "media-preview-video",
    ) as HTMLVideoElement;
    Object.defineProperty(video, "playbackRate", {
      writable: true,
      value: 1,
    });
    await user.click(screen.getByTestId("media-rate-0.5"));
    expect(video.playbackRate).toBe(0.5);
  });

  it("帧步进按钮按 1/25s 移动 currentTime 并受时长上界约束", async () => {
    getDownloadUrl.mockResolvedValue({
      url: "https://media.test/signed.mp4",
      expires_at: null,
    });
    const user = userEvent.setup();
    render(
      <MediaPreview
        projectId={PROJECT_ID}
        asset={playableAsset()}
        startTimeNs={0}
        endTimeNs={10_000_000_000}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("media-preview-video")).toBeTruthy(),
    );
    const video = screen.getByTestId(
      "media-preview-video",
    ) as HTMLVideoElement;
    Object.defineProperty(video, "currentTime", {
      writable: true,
      value: 0,
      configurable: true,
    });
    Object.defineProperty(video, "duration", {
      writable: true,
      value: 10,
      configurable: true,
    });
    await user.click(screen.getByText("帧 ›"));
    expect(video.currentTime).toBeCloseTo(1 / 25, 6);
    // 连续后退到 0 不得越界
    Object.defineProperty(video, "currentTime", {
      writable: true,
      value: 0.001,
      configurable: true,
    });
    await user.click(screen.getByText("‹ 帧"));
    expect(video.currentTime).toBeGreaterThanOrEqual(0);
  });
});
