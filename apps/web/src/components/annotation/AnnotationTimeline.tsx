import { Space, Typography } from "antd";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useCallback, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

import { formatPlaybackClock } from "../../services/media";

/** 时间轴上的一个片段（时间单位：相对 Episode 起点的秒）。 */
export interface TimelineSegment {
  key: string;
  label: string;
  startSeconds: number;
  endSeconds: number;
  /** 同一标签的多个片段用不同色调区分；不参与业务语义。 */
  tone?: "primary" | "warning" | "success";
  /** 片段业务标识：提供后可拖拽边界调整与删除（蓝图 §6）。 */
  segmentId?: string;
  /** AI 建议片段：虚线边框+「AI」徽标，与人工片段显式区分（蓝图 §7：预标注必须显式标识）。 */
  aiSuggestion?: boolean;
}

export interface AnnotationTimelineProps {
  durationSeconds: number;
  /** 当前播放位置（相对秒）；由外部播放器驱动。 */
  playheadSeconds: number;
  segments?: readonly TimelineSegment[];
  /** 点击或键盘移动播放位置。不传则时间轴只读。 */
  onSeek?: (seconds: number) => void;
  /** 拖拽选区回调：给出选区起止（相对秒，已排序）。 */
  onSelectRange?: (startSeconds: number, endSeconds: number) => void;
  /** 拖拽已有片段边界：segmentId + 新起止（相对秒）。蓝图 §6 片段边界编辑。 */
  onSegmentResize?: (
    segmentId: string,
    startSeconds: number,
    endSeconds: number,
  ) => void;
  /** 删除片段：由调用方弹出确认（蓝图 §6：删除需确认或可撤销）。 */
  onSegmentDelete?: (segmentId: string) => void;
  disabled?: boolean;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function ratioToSeconds(ratio: number, durationSeconds: number): number {
  return clamp(ratio, 0, 1) * durationSeconds;
}

const TONE_COLORS: Record<NonNullable<TimelineSegment["tone"]>, string> = {
  primary: "rgba(22, 119, 255, 0.35)",
  warning: "rgba(250, 173, 20, 0.45)",
  success: "rgba(82, 196, 26, 0.4)",
};

/** AI 建议轨道专用色：紫色系，与人工三色显式区分。 */
const AI_TONE = "rgba(114, 46, 209, 0.32)";

/**
 * Episode 时间轴：展示片段、播放位置，支持点击定位与拖拽选区。
 *
 * 所有时间以「相对 Episode 起点的秒」传递，纳秒换算在调用方完成——
 * 时间轴本身不持有业务时域语义，避免和 Schema 校验规则分叉。
 */
export default function AnnotationTimeline({
  durationSeconds,
  playheadSeconds,
  segments = [],
  onSegmentResize,
  onSegmentDelete,
  onSeek,
  onSelectRange,
  disabled = false,
}: AnnotationTimelineProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [anchorRatio, setAnchorRatio] = useState<number | null>(null);
  const [cursorRatio, setCursorRatio] = useState<number | null>(null);
  const interactive = !disabled && durationSeconds > 0;

  const ratioFromEvent = useCallback(
    (event: PointerEvent<Element>): number | null => {
      const element = trackRef.current;
      if (!element) {
        return null;
      }
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0) {
        return null;
      }
      return clamp((event.clientX - rect.left) / rect.width, 0, 1);
    },
    [],
  );

  const handlePointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!interactive) {
        return;
      }
      const ratio = ratioFromEvent(event);
      if (ratio === null) {
        return;
      }
      if (onSelectRange) {
        setAnchorRatio(ratio);
        setCursorRatio(ratio);
      }
      onSeek?.(ratioToSeconds(ratio, durationSeconds));
    },
    [durationSeconds, interactive, onSeek, onSelectRange, ratioFromEvent],
  );

  const handlePointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (anchorRatio === null) {
        return;
      }
      const ratio = ratioFromEvent(event);
      if (ratio !== null) {
        setCursorRatio(ratio);
      }
    },
    [anchorRatio, ratioFromEvent],
  );

  const handlePointerUp = useCallback(() => {
    if (anchorRatio === null || cursorRatio === null) {
      return;
    }
    const start = ratioToSeconds(
      Math.min(anchorRatio, cursorRatio),
      durationSeconds,
    );
    const end = ratioToSeconds(
      Math.max(anchorRatio, cursorRatio),
      durationSeconds,
    );
    setAnchorRatio(null);
    setCursorRatio(null);
    if (end - start > 0) {
      onSelectRange?.(start, end);
    }
  }, [anchorRatio, cursorRatio, durationSeconds, onSelectRange]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (!interactive || !onSeek) {
        return;
      }
      const step = event.shiftKey ? 1 : 0.1;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        onSeek(clamp(playheadSeconds - step, 0, durationSeconds));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        onSeek(clamp(playheadSeconds + step, 0, durationSeconds));
      } else if (event.key === "Home") {
        event.preventDefault();
        onSeek(0);
      } else if (event.key === "End") {
        event.preventDefault();
        onSeek(durationSeconds);
      }
    },
    [durationSeconds, interactive, onSeek, playheadSeconds],
  );

  // ---- V31-045 切片 B：片段边界拖拽 ----
  // resizeTarget 记录 (segmentId, 'start'|'end')；拖拽期间用临时值渲染。
  const [resizeTarget, setResizeTarget] = useState<{
    segmentId: string;
    edge: "start" | "end";
  } | null>(null);
  const [previewRange, setPreviewRange] = useState<{
    start: number;
    end: number;
  } | null>(null);

  const handleSegmentEdgeDown = useCallback(
    (
      event: ReactPointerEvent<Element>,
      segment: TimelineSegment,
      edge: "start" | "end",
    ) => {
      if (!interactive || !onSegmentResize || !segment.segmentId) {
        return;
      }
      event.stopPropagation();
      event.preventDefault();
      const ratio = ratioFromEvent(event);
      if (ratio === null) {
        return;
      }
      setResizeTarget({ segmentId: segment.segmentId, edge });
      setPreviewRange({ start: segment.startSeconds, end: segment.endSeconds });
      setAnchorRatio(ratio);
      setCursorRatio(ratio);
    },
    [interactive, onSegmentResize, ratioFromEvent],
  );

  // 拖拽期间 pointermove/pointerup 的段内拦截：主轨道的 handlePointerMove
  // 只更新 cursorRatio，这里在 pointerup 时判定是 resize 还是普通选区。
  const handleResizePointerUp = useCallback(() => {
    if (!resizeTarget || !previewRange || anchorRatio === null || cursorRatio === null) {
      return;
    }
    const grabbed = ratioToSeconds(cursorRatio, durationSeconds);
    const next =
      resizeTarget.edge === "start"
        ? { start: Math.min(grabbed, previewRange.end), end: previewRange.end }
        : { start: previewRange.start, end: Math.max(grabbed, previewRange.start) };
    if (next.end - next.start > 0) {
      onSegmentResize?.(resizeTarget.segmentId, next.start, next.end);
    }
    setResizeTarget(null);
    setPreviewRange(null);
    setAnchorRatio(null);
    setCursorRatio(null);
  }, [anchorRatio, cursorRatio, durationSeconds, onSegmentResize, previewRange, resizeTarget]);

  const handleSegmentDelete = useCallback(
    (segment: TimelineSegment) => {
      if (!interactive || !onSegmentDelete || !segment.segmentId) {
        return;
      }
      onSegmentDelete(segment.segmentId);
    },
    [interactive, onSegmentDelete],
  );

  const selection = useMemo(() => {
    if (anchorRatio === null || cursorRatio === null) {
      return null;
    }
    const left = Math.min(anchorRatio, cursorRatio);
    const right = Math.max(anchorRatio, cursorRatio);
    return { left: `${left * 100}%`, width: `${(right - left) * 100}%` };
  }, [anchorRatio, cursorRatio]);

  const playheadPercent =
    durationSeconds > 0
      ? clamp(playheadSeconds / durationSeconds, 0, 1) * 100
      : 0;

  return (
    <Space direction="vertical" size={6} className="ddp-stack">
      <div
        ref={trackRef}
        role="slider"
        aria-label="Episode 时间轴"
        aria-valuemin={0}
        aria-valuemax={Math.round(durationSeconds * 10) / 10}
        aria-valuenow={Math.round(playheadSeconds * 10) / 10}
        aria-valuetext={`当前 ${formatPlaybackClock(playheadSeconds)}，总长 ${formatPlaybackClock(durationSeconds)}`}
        aria-disabled={!interactive}
        tabIndex={interactive ? 0 : -1}
        data-testid="annotation-timeline"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={() => {
          handleResizePointerUp();
          handlePointerUp();
        }}
        onPointerCancel={() => {
          handleResizePointerUp();
          handlePointerUp();
        }}
        onKeyDown={handleKeyDown}
        style={{
          position: "relative",
          height: 34,
          borderRadius: 6,
          background: "#f0f0f0",
          cursor: interactive ? "crosshair" : "not-allowed",
          overflow: "hidden",
          touchAction: "none",
        }}
      >
        {durationSeconds > 0
          ? segments.map((segment) => {
              const left = clamp(segment.startSeconds / durationSeconds, 0, 1);
              const right = clamp(segment.endSeconds / durationSeconds, 0, 1);
              const resizable =
                interactive &&
                Boolean(onSegmentResize && segment.segmentId && !segment.aiSuggestion);
              const deletable =
                interactive && Boolean(onSegmentDelete && segment.segmentId && !segment.aiSuggestion);
              return (
                <div
                  key={segment.key}
                  data-testid={`timeline-segment-${segment.segmentId ?? segment.key}`}
                  title={`${segment.label} ${formatPlaybackClock(segment.startSeconds)} – ${formatPlaybackClock(segment.endSeconds)}`}
                  style={{
                    position: "absolute",
                    top: 4,
                    bottom: 4,
                    left: `${left * 100}%`,
                    width: `${Math.max(right - left, 0) * 100}%`,
                    background: segment.aiSuggestion
                      ? AI_TONE
                      : TONE_COLORS[segment.tone ?? "primary"],
                    borderRadius: 3,
                    border: segment.aiSuggestion ? "1.5px dashed rgba(114, 46, 209, 0.9)" : undefined,
                    cursor: resizable ? "grab" : undefined,
                  }}
                >
                  {segment.aiSuggestion ? (
                    <span
                      style={{
                        position: "absolute",
                        left: 2,
                        top: -8,
                        fontSize: 9,
                        background: "rgba(114, 46, 209, 0.92)",
                        color: "#fff",
                        borderRadius: 2,
                        padding: "0 3px",
                        lineHeight: "14px",
                      }}
                    >
                      AI
                    </span>
                  ) : null}
                  {resizable ? (
                    <>
                      <span
                        role="button"
                        aria-label={`调整片段起点：${segment.label}`}
                        data-testid={`segment-edge-start-${segment.segmentId}`}
                        onPointerDown={(event) =>
                          handleSegmentEdgeDown(event, segment, "start")
                        }
                        style={{
                          position: "absolute",
                          left: 0,
                          top: 0,
                          bottom: 0,
                          width: 6,
                          cursor: "ew-resize",
                          background: "rgba(255,255,255,0.65)",
                        }}
                      />
                      <span
                        role="button"
                        aria-label={`调整片段终点：${segment.label}`}
                        data-testid={`segment-edge-end-${segment.segmentId}`}
                        onPointerDown={(event) =>
                          handleSegmentEdgeDown(event, segment, "end")
                        }
                        style={{
                          position: "absolute",
                          right: 0,
                          top: 0,
                          bottom: 0,
                          width: 6,
                          cursor: "ew-resize",
                          background: "rgba(255,255,255,0.65)",
                        }}
                      />
                    </>
                  ) : null}
                  {deletable ? (
                    <button
                      type="button"
                      aria-label={`删除片段：${segment.label}`}
                      data-testid={`segment-delete-${segment.segmentId}`}
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        handleSegmentDelete(segment);
                      }}
                      style={{
                        position: "absolute",
                        right: resizable ? 8 : 2,
                        top: "50%",
                        transform: "translateY(-50%)",
                        border: "none",
                        background: "rgba(0,0,0,0.45)",
                        color: "#fff",
                        borderRadius: 3,
                        width: 14,
                        height: 14,
                        lineHeight: "12px",
                        fontSize: 10,
                        cursor: "pointer",
                      }}
                    >
                      ×
                    </button>
                  ) : null}
                </div>
              );
            })
          : null}
        {selection ? (
          <div
            data-testid="annotation-timeline-selection"
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: selection.left,
              width: selection.width,
              background: "rgba(22, 119, 255, 0.18)",
              borderLeft: "2px solid #1677ff",
              borderRight: "2px solid #1677ff",
            }}
          />
        ) : null}
        {durationSeconds > 0 ? (
          <div
            data-testid="annotation-timeline-playhead"
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: `calc(${playheadPercent}% - 1px)`,
              width: 2,
              background: "#fa541c",
            }}
          />
        ) : null}
      </div>
      <Space wrap size={12}>
        <Typography.Text type="secondary">
          {formatPlaybackClock(playheadSeconds)} /{" "}
          {formatPlaybackClock(durationSeconds)}
        </Typography.Text>
        <Typography.Text type="secondary">
          {onSelectRange
            ? "在时间轴上拖拽可框选片段；左右方向键微调播放位置（Shift 加速）。"
            : "点击或方向键可定位播放位置。"}
        </Typography.Text>
      </Space>
    </Space>
  );
}
