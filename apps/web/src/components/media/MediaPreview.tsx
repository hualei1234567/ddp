import { Button, Card, Descriptions, Space, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiError } from "../../services/api";
import { rawAssetsApi } from "../../services/rawAssets";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import {
  asMediaError,
  durationFromNs,
  formatMediaSize,
  isPlayableMedia,
  type MediaAssetRef,
} from "../../services/media";

export type { MediaAssetRef } from "../../services/media";

export interface MediaPreviewProps {
  projectId: string;
  asset: MediaAssetRef;
  /** Episode 起止，用于展示真实时长与回放定位边界。 */
  startTimeNs?: string | number | null;
  endTimeNs?: string | number | null;
  /** 需要跳转到的相对秒数（例如从审核问题定位）。 */
  seekToSeconds?: number | null;
  title?: string;
}

interface SignedUrl {
  url: string;
  expiresAt: string | null;
}

export default function MediaPreview({
  projectId,
  asset,
  startTimeNs = null,
  endTimeNs = null,
  seekToSeconds = null,
  title = "媒体审看",
}: MediaPreviewProps) {
  const playable = isPlayableMedia(asset.contentType);
  const [signed, setSigned] = useState<SignedUrl | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(playable);
  const [attempt, setAttempt] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const durationSeconds = durationFromNs(startTimeNs, endTimeNs);

  const load = useCallback(() => {
    if (!playable) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    rawAssetsApi
      .getDownloadUrl(projectId, asset.assetId)
      .then((result) => {
        if (!cancelled) {
          setSigned({ url: result.url, expiresAt: result.expires_at ?? null });
          setLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setSigned(null);
          setError(asMediaError(caught));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [asset.assetId, playable, projectId]);

  useEffect(() => load(), [load, attempt]);

  useEffect(() => {
    if (seekToSeconds == null || !Number.isFinite(seekToSeconds)) {
      return;
    }
    const bounded =
      durationSeconds === null
        ? Math.max(0, seekToSeconds)
        : Math.min(Math.max(0, seekToSeconds), durationSeconds);
    const video = videoRef.current;
    if (video) {
      video.currentTime = bounded;
    }
  }, [durationSeconds, seekToSeconds, signed]);

  const header = (
    <Space wrap>
      <span>{title}</span>
      <Typography.Text
        type="secondary"
        copyable={{ text: asset.originalFilename }}
      >
        {asset.originalFilename}
      </Typography.Text>
    </Space>
  );

  return (
    <Card size="small" title={header}>
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Descriptions
          size="small"
          column={3}
          items={[
            {
              key: "type",
              label: "类型",
              children: asset.contentType ?? "未知",
            },
            {
              key: "size",
              label: "大小",
              children: formatMediaSize(asset.sizeBytes),
            },
            {
              key: "duration",
              label: "时长",
              children:
                durationSeconds === null
                  ? "未声明"
                  : `${durationSeconds.toFixed(1)} s`,
            },
          ]}
        />

        {!playable ? (
          <PageState
            compact
            variant="disabled"
            title="该资产不可直接播放"
            description={`内容类型为 ${asset.contentType ?? "未知"}；可下载后在本地查看，或等待转换产物。`}
            action={
              <Button
                type="link"
                onClick={() => setAttempt((value) => value + 1)}
                disabled={!signed && !loading}
              >
                重新检查
              </Button>
            }
          />
        ) : loading ? (
          <PageState compact variant="loading" title="正在获取媒体地址" />
        ) : error ? (
          <div>
            <Typography.Text type="danger" strong>
              媒体地址获取失败
            </Typography.Text>
            <ApiErrorState
              compact
              error={error}
              onRetry={() => setAttempt((value) => value + 1)}
            />
          </div>
        ) : signed ? (
          <>
            <video
              ref={videoRef}
              data-testid="media-preview-video"
              src={signed.url}
              controls
              preload="metadata"
              playsInline
              aria-label={`Episode 媒体：${asset.originalFilename}`}
              style={{ width: "100%", maxHeight: 420, background: "#000" }}
            />
            <Space wrap size={12}>
              {signed.expiresAt ? (
                <Typography.Text type="secondary">
                  地址有效至 {new Date(signed.expiresAt).toLocaleTimeString()}
                </Typography.Text>
              ) : null}
              <Button
                size="small"
                onClick={() => setAttempt((value) => value + 1)}
              >
                刷新播放地址
              </Button>
            </Space>
          </>
        ) : (
          <PageState compact variant="empty" title="尚无可播放地址" />
        )}
      </Space>
    </Card>
  );
}
