import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { useCallback, useState } from "react";

import { useRequest } from "../../hooks/useRequest";
import { loadEpisodeMediaEvidence } from "../../services/episodeMediaEvidence";
import { durationFromNs, type MediaAssetRef } from "../../services/media";
import { lineageApi } from "../../services/lineage";
import { rawAssetsApi } from "../../services/rawAssets";
import { resolvePagePhase } from "../../services/pagePhase";
import type { Episode } from "../../types/collection";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import MediaPreview from "../media/MediaPreview";
import AnnotationTimeline, { type TimelineSegment } from "./AnnotationTimeline";

interface EpisodeMediaPanelProps {
  projectId: string;
  episode: Episode;
  /** 当前播放位置（相对秒）；由父级持有，便于与标签片段联动。 */
  playheadSeconds: number;
  onSeek: (seconds: number) => void;
  segments?: readonly TimelineSegment[];
  /** 从时间轴框选产生的绝对纳秒区间。 */
  onSelectRangeNs?: (startTimeNs: number, endTimeNs: number) => void;
  /** 轨道声明（V31-047）：L1/L2/L3 语义层 + 结果帧轨，透传给时间轴。 */
  lanes?: readonly { key: string; label: string }[];
  /** 是否允许框选（只读角色或已提交时关闭）。 */
  canSelectRange: boolean;
  /**
   * QC 证据高亮区间（V51-010）：相对 Episode 起点的秒。
   * 只做只读高亮，不改变框选/播放交互。
   */
  evidenceRange?: { startSeconds: number; endSeconds: number } | null;
  /** 证据所属媒体流；用于在时间轴上标注，并优先选中同机位的可播放资产。 */
  evidenceStreamId?: string | null;
  /** 时间轴上方要展示的证据说明（例如规则与阈值）；未提供时不渲染。 */
  evidenceSummary?: React.ReactNode;
}

const deps = { lineage: lineageApi, assets: rawAssetsApi };

/**
 * Episode 媒体审看区：视频播放 + 时间轴 + 上游资产清单。
 *
 * 数据来源全部是服务端权威接口——反向血缘给出「哪些原始资产切出了这个
 * Episode」，RawAsset 详情给出真实内容类型，签名地址按需短时签发。没有
 * 来源边时如实说明，不伪造播放器占位。
 */
export default function EpisodeMediaPanel({
  projectId,
  episode,
  playheadSeconds,
  onSeek,
  segments,
  onSelectRangeNs,
  lanes,
  canSelectRange,
  evidenceRange = null,
  evidenceStreamId = null,
  evidenceSummary = null,
}: EpisodeMediaPanelProps) {
  const [activeAssetId, setActiveAssetId] = useState<string | null>(null);

  const load = useCallback(
    (signal: AbortSignal) =>
      loadEpisodeMediaEvidence(deps, projectId, episode, signal),
    [episode, projectId],
  );
  const { data, error, loading, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });
  const durationSeconds = durationFromNs(
    episode.start_time_ns,
    episode.end_time_ns,
  );

  if (phase === "loading") {
    return (
      <Card size="small" title="媒体审看">
        <PageState
          compact
          variant="loading"
          title="正在读取上游媒体"
          description="通过反向血缘定位切分出该 Episode 的原始资产。"
        />
      </Card>
    );
  }
  if (phase === "error") {
    return (
      <Card size="small" title="媒体审看">
        {error ? (
          <ApiErrorState compact error={error} onRetry={reload} />
        ) : null}
      </Card>
    );
  }
  if (!data) {
    return null;
  }

  const active =
    data.playable.find((item) => item.asset_id === activeAssetId) ??
    data.playable[0] ??
    null;

  const activeRef: MediaAssetRef | null = active
    ? {
        assetId: active.asset_id,
        originalFilename: active.original_filename,
        contentType: active.content_type,
        sizeBytes: active.size_bytes,
      }
    : null;

  return (
    <Card
      size="small"
      title="媒体审看"
      extra={
        <Button size="small" onClick={reload}>
          重新读取
        </Button>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功读取的媒体清单。"
            onRetry={reload}
          />
        ) : null}

        {data.failures.length > 0 ? (
          <PageState
            compact
            variant="partial"
            title={`${data.failures.length} 个上游资产读取失败`}
            description={data.failures
              .map(
                (item) =>
                  `${item.assetId.slice(0, 8)}（${item.error.code ?? "unknown"}）`,
              )
              .join("；")}
            onRetry={reload}
          />
        ) : null}

        {data.sourceAssetCount === 0 ? (
          <PageState
            compact
            variant="empty"
            title="尚未关联上游媒体"
            description="反向血缘中没有 RAW_ASSET --SEGMENTED_INTO--> 本 Episode 的边。可能仍在解析中，或该 Episode 由导入直接生成。"
          />
        ) : null}

        {evidenceSummary}

        {durationSeconds !== null && durationSeconds > 0 ? (
          <AnnotationTimeline
            durationSeconds={durationSeconds}
            playheadSeconds={playheadSeconds}
            segments={segments}
            onSeek={onSeek}
            lanes={lanes}
            evidenceRange={evidenceRange}
            evidenceStreamId={evidenceStreamId}
            onSelectRange={
              canSelectRange && onSelectRangeNs
                ? (start, end) =>
                    onSelectRangeNs(
                      Math.round(Number(episode.start_time_ns) + start * 1e9),
                      Math.round(Number(episode.start_time_ns) + end * 1e9),
                    )
                : undefined
            }
          />
        ) : (
          <Typography.Text type="secondary">
            服务端未给出有效的 Episode 起止时间，无法渲染时间轴。
          </Typography.Text>
        )}

        {activeRef ? (
          <MediaPreview
            projectId={projectId}
            asset={activeRef}
            startTimeNs={episode.start_time_ns}
            endTimeNs={episode.end_time_ns}
            seekToSeconds={playheadSeconds}
          />
        ) : data.sourceAssetCount > 0 ? (
          <Alert
            type="info"
            showIcon
            message="没有可直接播放的上游媒体"
            description="已关联的资产都不是视频内容类型；标注时请依据时间轴与下载后的原始文件，勿凭印象填写时间片段。"
          />
        ) : null}

        {data.notPlayable.length > 0 ? (
          <Space direction="vertical" size={4}>
            <Typography.Text type="secondary">
              不可直接播放的上游资产
            </Typography.Text>
            <Space wrap size={6}>
              {data.notPlayable.map((asset) => (
                <Tag key={asset.asset_id}>
                  {asset.original_filename} · {asset.content_type}
                </Tag>
              ))}
            </Space>
          </Space>
        ) : null}

        {data.playable.length > 1 ? (
          <Space wrap size={6}>
            <Typography.Text type="secondary">可播放机位</Typography.Text>
            {data.playable.map((asset) => (
              <Button
                key={asset.asset_id}
                size="small"
                type={
                  asset.asset_id === activeRef?.assetId ? "primary" : "default"
                }
                onClick={() => setActiveAssetId(asset.asset_id)}
              >
                {asset.original_filename}
              </Button>
            ))}
          </Space>
        ) : null}
      </Space>
    </Card>
  );
}
