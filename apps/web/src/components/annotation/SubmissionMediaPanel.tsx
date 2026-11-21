import {
  Card,
  Descriptions,
  Empty,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useRequest } from "../../hooks/useRequest";
import { collectionsApi } from "../../services/collections";
import {
  loadEpisodeMediaEvidence,
  type EpisodeMediaEvidence,
} from "../../services/episodeMediaEvidence";
import { lineageApi } from "../../services/lineage";
import { durationFromNs, nsToRelativeSeconds } from "../../services/media";
import { rawAssetsApi } from "../../services/rawAssets";
import type { AnnotationRecord } from "../../types/annotation";
import type { Episode } from "../../types/collection";
import MediaPreview from "../media/MediaPreview";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import AnnotationTimeline, { type TimelineSegment } from "./AnnotationTimeline";

interface SubmissionMediaPanelProps {
  projectId: string;
  records: readonly AnnotationRecord[];
}

const deps = {
  lineage: lineageApi,
  assets: rawAssetsApi,
  collections: collectionsApi,
};

/** 记录内可定位的绝对时间片段（纳秒）；渲染阶段再换算相对秒。 */
interface AbsoluteSegment {
  key: string;
  label: string;
  startNs: number;
  endNs: number;
}

interface RecordEvidence {
  record: AnnotationRecord;
  episode: Episode | null;
  episodeError: string | null;
  segments: AbsoluteSegment[];
  /** null 表示该 Episode 的媒体尚未读取（读取失败或 Episode 读取失败）。 */
  media: EpisodeMediaEvidence | null;
  mediaError: string | null;
}

function recordTimeRange(record: AnnotationRecord): {
  startNs: number;
  endNs: number;
} | null {
  const range = record.time_range;
  if (!range) {
    return null;
  }
  const start = Number(range.start_time_ns);
  const end = Number(range.end_time_ns);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return null;
  }
  return { startNs: start, endNs: end };
}

function recordSegments(record: AnnotationRecord): AbsoluteSegment[] {
  const segments: AbsoluteSegment[] = [];
  for (const label of record.labels) {
    const value = label.value;
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      continue;
    }
    const spec = value as Record<string, unknown>;
    const start = Number(spec.start_time_ns);
    const end = Number(spec.end_time_ns);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      continue;
    }
    segments.push({
      key: `${record.annotation_id}:${label.label_key}`,
      label: label.label_key,
      startNs: start,
      endNs: end,
    });
  }
  return segments;
}

/**
 * 审核用媒体证据：把提交里的每条标注记录映射回 Episode 媒体与时间片段。
 *
 * 复核人不必离开页面去翻 Episode 详情：选中记录后，播放器直接定位到该
 * 记录的起点，时间轴上叠加本记录内的标签片段。数据全部来自服务端权威
 * 接口；Episode 或媒体读取失败逐条提示，不隐藏、不伪造。
 *
 * 所有 Episode 的媒体在一次聚合请求里并发读取并按 Episode 去重——`useRequest`
 * 只在挂载时执行一次，把媒体读取挂在「当前选中记录」上会导致切换记录后
 * 永远拿不到数据。
 */
export default function SubmissionMediaPanel({
  projectId,
  records,
}: SubmissionMediaPanelProps) {
  const [activeRecordId, setActiveRecordId] = useState<string | null>(
    records[0]?.annotation_id ?? null,
  );
  const [playheadSeconds, setPlayheadSeconds] = useState(0);

  const episodeIds = useMemo(() => {
    const seen = new Set<string>();
    for (const record of records) {
      seen.add(record.episode_id);
    }
    return [...seen];
  }, [records]);

  const load = useCallback(
    async (signal: AbortSignal): Promise<RecordEvidence[]> => {
      const episodes = new Map<string, Episode>();
      const episodeErrors = new Map<string, string>();
      const media = new Map<string, EpisodeMediaEvidence>();
      const mediaErrors = new Map<string, string>();

      await Promise.all(
        episodeIds.map(async (episodeId) => {
          try {
            const episode = await deps.collections.getEpisode(
              projectId,
              episodeId,
              { signal },
            );
            episodes.set(episodeId, episode);
          } catch (caught) {
            if (!signal.aborted) {
              episodeErrors.set(
                episodeId,
                caught instanceof Error ? caught.message : "Episode 加载失败",
              );
            }
            return;
          }
          try {
            const evidence = await loadEpisodeMediaEvidence(
              deps,
              projectId,
              episodes.get(episodeId)!,
              signal,
            );
            media.set(episodeId, evidence);
          } catch (caught) {
            if (!signal.aborted) {
              mediaErrors.set(
                episodeId,
                caught instanceof Error ? caught.message : "媒体证据读取失败",
              );
            }
          }
        }),
      );

      return records.map((record) => ({
        record,
        episode: episodes.get(record.episode_id) ?? null,
        episodeError: episodeErrors.get(record.episode_id) ?? null,
        segments: recordSegments(record),
        media: media.get(record.episode_id) ?? null,
        mediaError: mediaErrors.get(record.episode_id) ?? null,
      }));
    },
    [episodeIds, projectId, records],
  );

  const { data, error, loading, stale, reload } = useRequest(load);

  const active =
    data?.find((item) => item.record.annotation_id === activeRecordId) ??
    data?.[0] ??
    null;

  const recordRange = active ? recordTimeRange(active.record) : null;
  const activeEpisode = active?.episode ?? null;
  // 依赖只放原始值：recordRange 每次渲染都是新对象，放进去会无限触发。
  const recordStartNs = recordRange?.startNs ?? null;
  const recordEndNs = recordRange?.endNs ?? null;
  useEffect(() => {
    if (!activeEpisode || recordStartNs === null) {
      setPlayheadSeconds(0);
      return;
    }
    const relative = nsToRelativeSeconds(
      recordStartNs,
      activeEpisode.start_time_ns,
    );
    setPlayheadSeconds(relative === null ? 0 : Math.max(0, relative));
  }, [activeEpisode, recordStartNs, recordEndNs]);

  if (records.length === 0) {
    return (
      <Card size="small" title="媒体证据">
        <Empty description="该提交没有标注记录，无可审看的媒体。" />
      </Card>
    );
  }

  if (data === null) {
    return (
      <Card size="small" title="媒体证据">
        {error ? (
          <ApiErrorState compact error={error} onRetry={reload} />
        ) : (
          <PageState
            compact
            variant="loading"
            title={loading ? "正在读取媒体证据" : "尚未读取"}
          />
        )}
      </Card>
    );
  }

  const timelineSegments: TimelineSegment[] = [];
  if (activeEpisode && active) {
    const startNs = Number(activeEpisode.start_time_ns);
    for (const segment of active.segments) {
      const startSeconds = nsToRelativeSeconds(segment.startNs, startNs);
      const endSeconds = nsToRelativeSeconds(segment.endNs, startNs);
      if (
        startSeconds === null ||
        endSeconds === null ||
        endSeconds <= startSeconds
      ) {
        continue;
      }
      timelineSegments.push({
        key: segment.key,
        label: segment.label,
        tone: "warning",
        startSeconds: Math.max(0, startSeconds),
        endSeconds: Math.max(0, endSeconds),
      });
    }
  }

  const episodeDuration = activeEpisode
    ? durationFromNs(activeEpisode.start_time_ns, activeEpisode.end_time_ns)
    : null;
  const mediaAsset = active?.media?.playable[0] ?? null;

  return (
    <Card
      size="small"
      title="媒体证据"
      extra={
        <Select
          aria-label="选择标注记录"
          size="small"
          style={{ minWidth: 280 }}
          value={active?.record.annotation_id}
          options={records.map((record, index) => ({
            value: record.annotation_id,
            label: `记录 ${index + 1}（v${record.annotation_version}）`,
          }))}
          onChange={(value) => setActiveRecordId(value)}
        />
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功读取的媒体证据。"
            onRetry={reload}
          />
        ) : null}

        {activeEpisode ? (
          <Descriptions
            size="small"
            column={2}
            items={[
              {
                key: "episode",
                label: "Episode",
                children: activeEpisode.code,
              },
              {
                key: "range",
                label: "记录时间范围",
                children: recordRange
                  ? `${recordRange.startNs} – ${recordRange.endNs} ns`
                  : "记录级（未声明片段）",
              },
            ]}
          />
        ) : (
          <PageState
            compact
            variant="partial"
            title="Episode 读取失败"
            description={
              active?.episodeError ??
              "服务端未返回该记录对应的 Episode；无法定位媒体时间。"
            }
            onRetry={reload}
          />
        )}

        {activeEpisode && episodeDuration !== null && episodeDuration > 0 ? (
          <AnnotationTimeline
            durationSeconds={episodeDuration}
            playheadSeconds={playheadSeconds}
            segments={timelineSegments}
            onSeek={setPlayheadSeconds}
          />
        ) : null}

        {active?.mediaError ? (
          <PageState
            compact
            variant="partial"
            title="媒体证据读取失败"
            description={active.mediaError}
            onRetry={reload}
          />
        ) : active?.media && active.media.sourceAssetCount === 0 ? (
          <PageState
            compact
            variant="empty"
            title="该 Episode 尚未关联上游媒体"
            description="反向血缘中没有切分来源；复核时只能依据标注记录与下载后的原始文件。"
          />
        ) : mediaAsset && activeEpisode ? (
          <MediaPreview
            projectId={projectId}
            asset={{
              assetId: mediaAsset.asset_id,
              originalFilename: mediaAsset.original_filename,
              contentType: mediaAsset.content_type,
              sizeBytes: mediaAsset.size_bytes,
            }}
            startTimeNs={activeEpisode.start_time_ns}
            endTimeNs={activeEpisode.end_time_ns}
            seekToSeconds={playheadSeconds}
            title="记录媒体"
          />
        ) : active?.media ? (
          <PageState
            compact
            variant="disabled"
            title="没有可播放的媒体"
            description="已关联资产均不是视频内容类型。"
          />
        ) : null}

        {active?.media && active.media.failures.length > 0 ? (
          <PageState
            compact
            variant="partial"
            title={`${active.media.failures.length} 个上游资产读取失败`}
            description={active.media.failures
              .map((item) => item.assetId.slice(0, 8))
              .join("；")}
            onRetry={reload}
          />
        ) : null}

        {active ? (
          <Space wrap size={6}>
            <Typography.Text type="secondary">记录状态</Typography.Text>
            <Tag>{active.record.status}</Tag>
            <Typography.Text type="secondary">
              {active.record.labels.length} 个标签 ·{" "}
              {active.record.evidence_refs.length} 条证据
            </Typography.Text>
          </Space>
        ) : null}
      </Space>
    </Card>
  );
}
