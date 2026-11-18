import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CameraPicker, PlaybackControls } from "./AssetReviewPage";
import {
  clampSeek,
  defaultCameraSelection,
  formatClock,
  normalizeCameraSelection,
  toggleCameraSelection,
} from "./playback";

function renderControls() {
  return renderToStaticMarkup(
    <PlaybackControls
      cameraLabel="主视角"
      rate={1}
      currentTime={5}
      duration={72}
      excerptStartSeconds={3}
      onRateChange={() => undefined}
      onSeek={() => undefined}
      onJumpToExcerpt={() => undefined}
    />,
  );
}

describe("播放控制条", () => {
  it("提供 0.5/1/1.5/2 倍速并带可访问名称", () => {
    const html = renderControls();

    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="主视角播放倍速"');
    for (const label of ["0.5×", "1×", "1.5×", "2×"]) {
      expect(html.replace(/\s+/g, "")).toContain(label);
    }
  });

  it("显示当前时间与总时长", () => {
    const html = renderControls();

    expect(html).toContain("00:05 / 01:12");
  });

  it("时间轴可定位并带 aria-label", () => {
    const html = renderControls();

    expect(html).toContain('type="range"');
    expect(html).toContain('aria-label="主视角时间轴"');
    expect(html).toContain('aria-valuetext="00:05 / 01:12"');
  });

  it("跳至片段按钮复用片段锚点", () => {
    const html = renderControls();

    expect(html).toContain('aria-label="主视角跳至片段"');
    expect(html).toContain("跳至片段 3s");
  });
});

describe("机位选择", () => {
  it("两路机位直接并排展示，不出现选择器", () => {
    const html = renderToStaticMarkup(
      <CameraPicker
        cameras={["main", "right"]}
        value={["main", "right"]}
        onChange={() => undefined}
      />,
    );

    expect(html).toContain('aria-label="当前同屏机位"');
    expect(html).toContain("主视角");
    expect(html).toContain("右视角");
    expect(html).not.toContain("选择同屏机位");
  });

  it("超过两路时提供可键盘操作的勾选，并带可访问名称", () => {
    const html = renderToStaticMarkup(
      <CameraPicker
        cameras={["main", "right", "wrist_left"]}
        value={["main", "right"]}
        onChange={() => undefined}
      />,
    );

    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="选择同屏机位"');
    expect(html).toContain("主视角");
    expect(html).toContain("右视角");
    expect(html).toContain("左腕");
    expect(html).toContain('type="checkbox"');
  });
});

describe("播放纯逻辑", () => {
  it("时间格式化为 mm:ss，负值按 0 处理", () => {
    expect(formatClock(0)).toBe("00:00");
    expect(formatClock(65.9)).toBe("01:05");
    expect(formatClock(-3)).toBe("00:00");
    expect(formatClock(Number.NaN)).toBe("00:00");
  });

  it("时间轴目标夹到 [0, duration]", () => {
    expect(clampSeek(20, 12)).toBe(12);
    expect(clampSeek(-1, 12)).toBe(0);
    expect(clampSeek(Number.POSITIVE_INFINITY, 12)).toBe(0);
    expect(clampSeek(5, 0)).toBe(0);
  });

  it("机位选择过滤未知机位并最多保留两路", () => {
    expect(
      normalizeCameraSelection(
        ["wrist_left", "ghost", "main", "right"],
        ["main", "right", "wrist_left"],
      ),
    ).toEqual(["wrist_left", "main"]);
    expect(defaultCameraSelection(["main", "right", "wrist_left"])).toEqual([
      "main",
      "right",
    ]);
  });

  it("勾选第三路时替换最早选择，取消勾选只移除目标机位", () => {
    const cameras = ["main", "right", "wrist_left"];

    expect(
      toggleCameraSelection(["main", "right"], "wrist_left", true, cameras),
    ).toEqual(["right", "wrist_left"]);
    expect(
      toggleCameraSelection(["main", "right"], "right", false, cameras),
    ).toEqual(["main"]);
    expect(
      toggleCameraSelection(["main", "right"], "right", true, cameras),
    ).toEqual(["main", "right"]);
    expect(toggleCameraSelection(["main"], "ghost", true, cameras)).toEqual([
      "main",
    ]);
  });
});
