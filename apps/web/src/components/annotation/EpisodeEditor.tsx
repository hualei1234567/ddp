import { Alert, Button, Card, Select, Space, Tag, Typography } from "antd";
import { useCallback, useMemo, useState } from "react";

import type {
  DraftFieldValue,
  EpisodeFormValues,
  TimeRangeInput,
} from "../../services/annotationValidation";
import { formatPlaybackClock, nsToRelativeSeconds } from "../../services/media";
import type { AnnotationLevel, LabelDefinition } from "../../types/annotation";
import type { Episode } from "../../types/collection";
import IdentifierValue from "../ui/IdentifierValue";
import type { AiSuggestion } from "../../services/annotationAiParsers";
import type { TimelineSegment } from "./AnnotationTimeline";
import EpisodeMediaPanel from "./EpisodeMediaPanel";
import SchemaForm from "./SchemaForm";

interface EpisodeEditorProps {
  projectId: string;
  episode: Episode;
  definitions: LabelDefinition[];
  values: EpisodeFormValues;
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (labelKey: string, value: DraftFieldValue) => void;
  /** 当前 Episode 的 AI 建议（时间轴叠加显示；采纳走 AiSuggestionPanel）。 */
  aiSuggestions?: readonly AiSuggestion[];
}

const TEMPORAL_TYPES = new Set(["TIME_RANGE", "ACTION_SEGMENT"]);

/** 时间片段标签的轨道归属（V31-047）；非语义层级/结果帧返回 null 进主轨道。 */
function laneNameForLevel(definition: LabelDefinition): string | null {
  if (definition.label_type === "OUTCOME_SPAN") {
    return "outcome";
  }
  const semanticLane: Partial<Record<AnnotationLevel, string>> = {
    SEMANTIC_TASK: "L1",
    SEMANTIC_SUBTASK: "L2",
    SEMANTIC_EVENT: "L3",
  };
  return semanticLane[definition.level] ?? null;
}

/** 把草稿里的时间片段转成时间轴片段；非法值直接跳过，不伪造。 */
function timelineSegments(
  definitions: readonly LabelDefinition[],
  values: EpisodeFormValues,
): TimelineSegment[] {
  const segments: TimelineSegment[] = [];
  for (const definition of definitions) {
    if (!TEMPORAL_TYPES.has(definition.label_type)) {
      continue;
    }
    const value = values[definition.label_key];
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      continue;
    }
    const range = value as TimeRangeInput;
    const start = Number(range.start);
    const end = Number(range.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      continue;
    }
    segments.push({
      key: definition.label_key,
      label: definition.label_key,
      startSeconds: start,
      endSeconds: end,
      tone: definition.label_type === "ACTION_SEGMENT" ? "warning" : "primary",
      track: laneNameForLevel(definition) ?? undefined,
    });
  }
  return segments;
}

/**
 * Episode 标注编辑器：时间轴 + 媒体审看 + Schema 表单。
 *
 * 时间轴上的选区只写本地草稿（纳秒字符串），提交时的类型、时长与
 * time_domain 校验仍由服务端与 `annotationValidation` 共同把关。
 */
export default function EpisodeEditor({
  projectId,
  episode,
  definitions,
  values,
  errors,
  disabled,
  onChange,
  aiSuggestions = [],
}: EpisodeEditorProps) {
  const [playheadSeconds, setPlayheadSeconds] = useState(0);
  const [pendingRange, setPendingRange] = useState<{
    startNs: number;
    endNs: number;
  } | null>(null);
  const [targetLabel, setTargetLabel] = useState<string | null>(null);

  const temporalDefinitions = useMemo(
    () => definitions.filter((item) => TEMPORAL_TYPES.has(item.label_type)),
    [definitions],
  );
  const segments = useMemo(
    () => timelineSegments(definitions, values),
    [definitions, values],
  );

  // V31-047：按标签 level 生成的轨道声明——L1/L2/L3 各一轨，时间片段标签
  // 在 timelineSegments 里按定义 level 写入 track；结果帧标签单独一轨。
  const semanticLanes = useMemo(() => {
    const lanes: { key: string; label: string }[] = [];
    const laneNames: Record<string, string> = {
      SEMANTIC_TASK: "L1 任务",
      SEMANTIC_SUBTASK: "L2 子任务",
      SEMANTIC_EVENT: "L3 事件",
    };
    for (const definition of definitions) {
      const laneKey = laneNameForLevel(definition);
      if (laneKey && !lanes.some((lane) => lane.key === laneKey)) {
        lanes.push({ key: laneKey, label: laneNames[laneKey] ?? laneKey });
      }
    }
    return lanes;
  }, [definitions]);

  const lanesWithOutcome = useMemo(() => {
    const hasOutcome = definitions.some(
      (item) => item.label_type === "OUTCOME_SPAN",
    );
    return hasOutcome
      ? [...semanticLanes, { key: "outcome", label: "结果帧（成功/失败/错误）" }]
      : semanticLanes;
  }, [definitions, semanticLanes]);

  // V31-046 切片 D：AI 建议叠加轨道。只有 kind=temporal_segment 且 span 可
  // 定位的建议才进时间轴；非法/非片段建议直接跳过，不伪造位置。采纳流程
  // 不在这里——AiSuggestionPanel 是唯一确认入口，时间轴只做可视化。
  const aiSegments = useMemo(() => {
    const result: TimelineSegment[] = [];
    for (const suggestion of aiSuggestions) {
      const value = suggestion.suggested_value as
        | { kind?: string; span?: { start_time_ns?: number; end_time_ns?: number } }
        | null;
      if (
        !value ||
        value.kind !== "temporal_segment" ||
        typeof value.span?.start_time_ns !== "number" ||
        typeof value.span?.end_time_ns !== "number"
      ) {
        continue;
      }
      const start = nsToRelativeSeconds(
        value.span.start_time_ns,
        episode.start_time_ns,
      );
      const end = nsToRelativeSeconds(
        value.span.end_time_ns,
        episode.start_time_ns,
      );
      if (start === null || end === null || end <= start) {
        continue;
      }
      result.push({
        key: `ai-${suggestion.suggestion_id}`,
        label: `${suggestion.label_key} (${Math.round(suggestion.confidence * 100)}%)`,
        startSeconds: start,
        endSeconds: end,
        aiSuggestion: true,
      });
    }
    return result;
  }, [aiSuggestions, episode.start_time_ns]);

  const handleSeek = useCallback((seconds: number) => {
    setPlayheadSeconds(Math.max(0, seconds));
  }, []);

  const handleSelectRange = useCallback(
    (startNs: number, endNs: number) => {
      setPendingRange({ startNs, endNs });
      const startSeconds = nsToRelativeSeconds(startNs, episode.start_time_ns);
      if (startSeconds !== null) {
        setPlayheadSeconds(Math.max(0, startSeconds));
      }
    },
    [episode.start_time_ns],
  );

  const applyRange = useCallback(() => {
    if (!pendingRange || !targetLabel) {
      return;
    }
    onChange(targetLabel, {
      start: String(pendingRange.startNs),
      end: String(pendingRange.endNs),
    });
    setPendingRange(null);
  }, [onChange, pendingRange, targetLabel]);

  return (
    <Card
      size="small"
      data-testid="episode-editor"
      title={
        <Space wrap>
          <span>{episode.code}</span>
          <Typography.Text type="secondary">
            Episode v{episode.episode_version}
          </Typography.Text>
        </Space>
      }
      extra={<IdentifierValue value={episode.episode_id} label="Episode ID" />}
    >
      <Space
        direction="vertical"
        size={12}
        className="ddp-stack"
        style={{ width: "100%" }}
      >
        <Typography.Text type="secondary">
          时间范围：{episode.start_time_ns} – {episode.end_time_ns} ns · 时域{" "}
          {episode.time_domain}
        </Typography.Text>

        {episode.calibration_snapshot.length === 0 ? (
          <Alert
            type="warning"
            showIcon
            message="该 Episode 未登记标定快照"
            description="标注仍可进行；若本任务要求标定后的传感器数据，请先补标定再提交。"
          />
        ) : null}

        <EpisodeMediaPanel
          projectId={projectId}
          episode={episode}
          playheadSeconds={playheadSeconds}
          onSeek={handleSeek}
          segments={[...segments, ...aiSegments]}
          lanes={lanesWithOutcome}
          canSelectRange={!disabled && temporalDefinitions.length > 0}
          onSelectRangeNs={handleSelectRange}
        />

        {temporalDefinitions.length > 0 ? (
          <Card size="small" type="inner" title="时间片段标注">
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Space wrap size={8}>
                <Typography.Text type="secondary">目标标签</Typography.Text>
                <Select
                  aria-label="时间片段目标标签"
                  style={{ minWidth: 220 }}
                  placeholder="选择 TIME_RANGE / ACTION_SEGMENT 标签"
                  value={targetLabel ?? undefined}
                  options={temporalDefinitions.map((item) => ({
                    value: item.label_key,
                    label: `${item.label_key}（${item.label_type}）`,
                  }))}
                  disabled={disabled}
                  onChange={setTargetLabel}
                />
                <Button
                  type="primary"
                  size="small"
                  disabled={
                    disabled || pendingRange === null || targetLabel === null
                  }
                  onClick={applyRange}
                >
                  把选区填入标签
                </Button>
              </Space>
              {pendingRange ? (
                <Space wrap size={8}>
                  <Tag color="blue">已框选</Tag>
                  <Typography.Text type="secondary">
                    {formatPlaybackClock(
                      nsToRelativeSeconds(
                        pendingRange.startNs,
                        episode.start_time_ns,
                      ) ?? 0,
                    )}{" "}
                    –{" "}
                    {formatPlaybackClock(
                      nsToRelativeSeconds(
                        pendingRange.endNs,
                        episode.start_time_ns,
                      ) ?? 0,
                    )}{" "}
                    ·{" "}
                    {(
                      (pendingRange.endNs - pendingRange.startNs) /
                      1e9
                    ).toFixed(1)}{" "}
                    s
                  </Typography.Text>
                  <Button size="small" onClick={() => setPendingRange(null)}>
                    取消选区
                  </Button>
                </Space>
              ) : (
                <Typography.Text type="secondary">
                  在上方时间轴拖拽框选一个片段，再选择目标标签填入。也可直接编辑下方表单的起止纳秒。
                </Typography.Text>
              )}
            </Space>
          </Card>
        ) : null}

        <SchemaForm
          definitions={definitions}
          values={values}
          errors={errors}
          disabled={disabled}
          onChange={onChange}
          pendingRangeNs={pendingRange}
        />
      </Space>
    </Card>
  );
}
