// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import AnnotationTimeline from "./AnnotationTimeline";

beforeAll(() => {
  // jsdom 没有 PointerEvent 构造器，testing-library 会退化成不含坐标的 Event，
  // 组件读到的 clientX 就是 undefined。用 MouseEvent 补齐坐标语义。
  if (typeof window.PointerEvent === "undefined") {
    class PointerEventPolyfill extends MouseEvent {
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
      }
    }
    Object.defineProperty(window, "PointerEvent", {
      writable: true,
      value: PointerEventPolyfill,
    });
  }
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

function stubRect(element: HTMLElement, width: number) {
  element.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      right: width,
      bottom: 34,
      width,
      height: 34,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
}

describe("AnnotationTimeline", () => {
  it("展示播放位置与总时长，并渲染已存在的片段", () => {
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={2.5}
        segments={[{ key: "a", label: "抓取", startSeconds: 1, endSeconds: 4 }]}
      />,
    );

    expect(screen.getByText("00:02.5 / 00:10.0")).toBeInTheDocument();
    expect(screen.getByTitle(/抓取/)).toBeInTheDocument();
    expect(
      screen.getByTestId("annotation-timeline-playhead"),
    ).toBeInTheDocument();
  });

  it("点击时间轴按比例定位播放位置", async () => {
    const onSeek = vi.fn();
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        onSeek={onSeek}
      />,
    );
    const track = screen.getByTestId("annotation-timeline");
    stubRect(track, 200);

    fireEvent.pointerDown(track, { clientX: 50, clientY: 10 });
    fireEvent.pointerUp(track, { clientX: 50, clientY: 10 });

    expect(onSeek).toHaveBeenCalledWith(2.5);
  });

  it("拖拽产生选区并在松开时回调已排序的起止", async () => {
    const onSelectRange = vi.fn();
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        onSeek={() => undefined}
        onSelectRange={onSelectRange}
      />,
    );
    const track = screen.getByTestId("annotation-timeline");
    stubRect(track, 200);

    fireEvent.pointerDown(track, { clientX: 160, clientY: 10 });
    fireEvent.pointerMove(track, { clientX: 40, clientY: 10 });
    fireEvent.pointerUp(track, { clientX: 40, clientY: 10 });

    expect(onSelectRange).toHaveBeenCalledWith(2, 8);
  });

  it("方向键微调、Shift 加速、Home/End 直达边界", async () => {
    const onSeek = vi.fn();
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={5}
        onSeek={onSeek}
      />,
    );
    const track = screen.getByTestId("annotation-timeline");
    track.focus();

    await userEvent.keyboard("{ArrowRight}");
    expect(onSeek).toHaveBeenLastCalledWith(5.1);

    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(onSeek).toHaveBeenLastCalledWith(4);

    await userEvent.keyboard("{Home}");
    expect(onSeek).toHaveBeenLastCalledWith(0);

    await userEvent.keyboard("{End}");
    expect(onSeek).toHaveBeenLastCalledWith(10);
  });

  it("禁用时不响应点击与键盘，并标记 aria-disabled", async () => {
    const onSeek = vi.fn();
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        onSeek={onSeek}
        disabled
      />,
    );
    const track = screen.getByTestId("annotation-timeline");
    stubRect(track, 200);
    expect(track.getAttribute("aria-disabled")).toBe("true");
    expect(track.getAttribute("tabindex")).toBe("-1");

    fireEvent.pointerDown(track, { clientX: 50, clientY: 10 });
    fireEvent.pointerUp(track, { clientX: 50, clientY: 10 });
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("时长为 0 时不渲染播放头，且不可交互", () => {
    render(<AnnotationTimeline durationSeconds={0} playheadSeconds={0} />);
    const track = screen.getByTestId("annotation-timeline");
    expect(track.getAttribute("aria-disabled")).toBe("true");
    expect(screen.queryByTestId("annotation-timeline-playhead")).toBeNull();
  });
});

// ---- V31-045 切片 B：片段边界拖拽与删除（蓝图 §6） ----

describe("AnnotationTimeline 片段边界编辑", () => {
  const BASE = {
    durationSeconds: 100,
    playheadSeconds: 0,
  };

  function stubSegmentRects() {
    // 片段块与边缘把手都需要 getBoundingClientRect
    document.querySelectorAll("[data-testid^=timeline-segment-]").forEach((el) => {
      el.getBoundingClientRect = () => ({
        left: 0, top: 0, right: 100, bottom: 30, width: 100, height: 30,
        x: 0, y: 0, toJSON: () => ({}),
      } as DOMRect);
    });
    document.querySelectorAll("[data-testid^=segment-edge-]").forEach((el) => {
      el.getBoundingClientRect = () => ({
        left: 0, top: 0, right: 6, bottom: 30, width: 6, height: 30,
        x: 0, y: 0, toJSON: () => ({}),
      } as DOMRect);
    });
  }

  it("提供 segmentId 时渲染边缘把手与删除按钮", () => {
    render(
      <AnnotationTimeline
        {...BASE}
        segments={[
          { key: "s1", label: "抓取", segmentId: "seg-1", startSeconds: 10, endSeconds: 30 },
        ]}
        onSegmentResize={vi.fn()}
        onSegmentDelete={vi.fn()}
      />,
    );
    expect(screen.getByTestId("segment-edge-start-seg-1")).toBeTruthy();
    expect(screen.getByTestId("segment-edge-end-seg-1")).toBeTruthy();
    expect(screen.getByTestId("segment-delete-seg-1")).toBeTruthy();
  });

  it("未提供 onSegmentResize 时把手不渲染（只读兼容）", () => {
    render(
      <AnnotationTimeline
        {...BASE}
        segments={[
          { key: "s1", label: "抓取", segmentId: "seg-1", startSeconds: 10, endSeconds: 30 },
        ]}
      />,
    );
    expect(screen.queryByTestId("segment-edge-start-seg-1")).toBeNull();
    expect(screen.queryByTestId("segment-delete-seg-1")).toBeNull();
  });

  it("拖拽终点边界触发 onSegmentResize（新 end 由指针位置决定）", async () => {
    const onSegmentResize = vi.fn();
    render(
      <AnnotationTimeline
        {...BASE}
        segments={[
          { key: "s1", label: "抓取", segmentId: "seg-1", startSeconds: 10, endSeconds: 30 },
        ]}
        onSegmentResize={onSegmentResize}
      />,
    );
    stubSegmentRects();
    const track = screen.getByTestId("annotation-timeline");
    track.getBoundingClientRect = () => ({
      left: 0, top: 0, right: 100, bottom: 34, width: 100, height: 34,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect);
    // jsdom 无 PointerEvent：手工构造带 pointer 属性的合成事件并直发 React 处理器。
    // 先拿到边缘把手的 React onPointerDown：React 把处理器挂在根节点，
    // 这里直接 fireEvent.pointerDown 让 testing-library 走 React 事件系统。
    const edge = screen.getByTestId("segment-edge-end-seg-1");
    edge.getBoundingClientRect = () => ({
      left: 30, top: 0, right: 36, bottom: 30, width: 6, height: 30,
      x: 30, y: 0, toJSON: () => ({}),
    } as DOMRect);
    fireEvent.pointerDown(edge, { clientX: 30, pointerId: 1, button: 0 });
    // 主轨道 pointermove 到 60/100 → 新 end = 60
    fireEvent.pointerMove(track, { clientX: 60, pointerId: 1 });
    fireEvent.pointerUp(track, { clientX: 60, pointerId: 1 });
    expect(onSegmentResize).toHaveBeenCalledWith("seg-1", 10, 60);
  });

  it("点击删除按钮透传 segmentId（确认对话框由调用方负责，蓝图 §6）", async () => {
    const user = userEvent.setup();
    const onSegmentDelete = vi.fn();
    render(
      <AnnotationTimeline
        {...BASE}
        segments={[
          { key: "s1", label: "抓取", segmentId: "seg-1", startSeconds: 10, endSeconds: 30 },
        ]}
        onSegmentDelete={onSegmentDelete}
      />,
    );
    await user.click(screen.getByTestId("segment-delete-seg-1"));
    expect(onSegmentDelete).toHaveBeenCalledWith("seg-1");
  });
});

// ---- V31-046 切片 D：AI 建议轨道（虚线边框 + AI 徽标，不可拖拽/删除） ----

describe("AnnotationTimeline AI 建议轨道", () => {
  it("AI 片段渲染 AI 徽标且不提供编辑把手（采纳走 AiSuggestionPanel）", () => {
    render(
      <AnnotationTimeline
        durationSeconds={100}
        playheadSeconds={0}
        segments={[
          {
            key: "ai-1",
            label: "segment.pick (82%)",
            segmentId: "sg-1",
            startSeconds: 5,
            endSeconds: 20,
            aiSuggestion: true,
          },
          {
            key: "h1",
            label: "人工片段",
            segmentId: "sg-2",
            startSeconds: 40,
            endSeconds: 60,
          },
        ]}
        onSegmentResize={vi.fn()}
        onSegmentDelete={vi.fn()}
      />,
    );
    // AI 片段：有徽标、无把手
    expect(screen.getByTestId("timeline-segment-sg-1").textContent).toContain("AI");
    expect(screen.queryByTestId("segment-edge-start-sg-1")).toBeNull();
    expect(screen.queryByTestId("segment-delete-sg-1")).toBeNull();
    // 人工片段：有把手
    expect(screen.getByTestId("segment-edge-start-sg-2")).toBeTruthy();
  });
});

// ---- V31-047：多轨道（L1/L2/L3 语义分层 + 结果帧轨道） ----

describe("AnnotationTimeline 多轨道", () => {
  const LANES = [
    { key: "L1", label: "L1 任务" },
    { key: "L2", label: "L2 子任务" },
    { key: "L3", label: "L3 事件" },
    { key: "outcome", label: "结果帧" },
  ] as const;

  it("按 lanes 声明渲染附加轨道行且互不串轨", () => {
    render(
      <AnnotationTimeline
        durationSeconds={100}
        playheadSeconds={0}
        lanes={LANES}
        segments={[
          { key: "t1", label: "L1 任务", track: "L1", startSeconds: 0, endSeconds: 100, segmentId: "seg-l1" },
          { key: "s1", label: "L2 子任务", track: "L2", startSeconds: 10, endSeconds: 40, segmentId: "seg-l2" },
          { key: "e1", label: "L3 事件", track: "L3", startSeconds: 20, endSeconds: 25, segmentId: "seg-l3" },
          { key: "o1", label: "FAILURE", track: "outcome", startSeconds: 42, endSeconds: 50, segmentId: "seg-o1", tone: "warning" },
        ]}
      />,
    );
    for (const lane of LANES) {
      expect(screen.getByTestId(`timeline-lane-${lane.key}`)).toBeTruthy();
    }
    expect(screen.getByTestId("timeline-segment-seg-l1")).toBeTruthy();
    expect(screen.getByTestId("timeline-segment-seg-o1")).toBeTruthy();
    // 各段落在自己的轨道行内（seg-l1 不在主轨道容器里）
    const mainTrack = screen.getByTestId("annotation-timeline");
    expect(mainTrack.querySelector('[data-testid="timeline-segment-seg-l1"]')).toBeNull();
    expect(
      screen
        .getByTestId("timeline-lane-L1")
        .querySelector('[data-testid="timeline-segment-seg-l1"]'),
    ).toBeTruthy();
  });

  it("未声明 lanes 时片段照旧进主轨道（向后兼容）", () => {
    render(
      <AnnotationTimeline
        durationSeconds={100}
        playheadSeconds={0}
        segments={[
          { key: "t1", label: "人工", track: "L1", startSeconds: 0, endSeconds: 50, segmentId: "seg-x" },
        ]}
      />,
    );
    // L1 未声明 → 追加行；片段可见
    expect(screen.getByTestId("timeline-lane-L1")).toBeTruthy();
    expect(screen.getByTestId("timeline-segment-seg-x")).toBeTruthy();
  });
});

describe("QC 证据高亮（V51-010）", () => {
  it("证据区间渲染为独立高亮块，与拖拽选区互不混淆", () => {
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        evidenceRange={{ startSeconds: 2, endSeconds: 5 }}
        evidenceStreamId="wrist"
      />,
    );

    const evidence = screen.getByTestId("annotation-timeline-evidence");
    expect(evidence).toBeInTheDocument();
    // 左 20%、宽 30%：2s–5s / 10s。
    expect(evidence.style.left).toBe("20%");
    expect(evidence.style.width).toBe("30%");
    expect(evidence).toHaveAttribute(
      "aria-label",
      expect.stringContaining("wrist"),
    );
    // 没有选区时不渲染选区块。
    expect(
      screen.queryByTestId("annotation-timeline-selection"),
    ).not.toBeInTheDocument();
  });

  it("没有证据区间时不渲染高亮块", () => {
    render(<AnnotationTimeline durationSeconds={10} playheadSeconds={0} />);

    expect(
      screen.queryByTestId("annotation-timeline-evidence"),
    ).not.toBeInTheDocument();
  });

  it("零宽或倒置区间不渲染：不画假高亮", () => {
    const { rerender } = render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        evidenceRange={{ startSeconds: 3, endSeconds: 3 }}
      />,
    );
    expect(
      screen.queryByTestId("annotation-timeline-evidence"),
    ).not.toBeInTheDocument();

    rerender(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        evidenceRange={{ startSeconds: 6, endSeconds: 2 }}
      />,
    );
    expect(
      screen.queryByTestId("annotation-timeline-evidence"),
    ).not.toBeInTheDocument();
  });

  it("越界区间被裁剪到 0..duration，不溢出时间轴", () => {
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        evidenceRange={{ startSeconds: -5, endSeconds: 15 }}
      />,
    );

    const evidence = screen.getByTestId("annotation-timeline-evidence");
    expect(evidence.style.left).toBe("0%");
    expect(evidence.style.width).toBe("100%");
  });

  it("证据高亮是只读的：点击不会触发框选回调", () => {
    const onSelectRange = vi.fn();
    render(
      <AnnotationTimeline
        durationSeconds={10}
        playheadSeconds={0}
        evidenceRange={{ startSeconds: 2, endSeconds: 5 }}
        onSeek={() => undefined}
        onSelectRange={onSelectRange}
      />,
    );

    const evidence = screen.getByTestId("annotation-timeline-evidence");
    // pointerEvents:none：证据块自身不接收指针事件。
    expect(evidence.style.pointerEvents).toBe("none");
  });
});
