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
