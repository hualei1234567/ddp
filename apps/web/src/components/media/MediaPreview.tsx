import { Button, Card, Descriptions, Space, Typography } from "antd";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { ApiError } from "../../services/api";
import { rawAssetsApi } from "../../services/rawAssets";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import {
  asMediaError,
  durationFromNs,
  formatMediaSize,
  formatPlaybackClock,
  isPlayableMedia,
  type MediaAssetRef,
} from "../../services/media";

export type { MediaAssetRef } from "../../services/media";

/** 倍速集合固定（0.25x–2x，蓝图 §5）。 */
const PLAYBACK_RATES = [0.25, 0.5, 1, 1.5, 2] as const;
/** 帧步进按 1/25s（与后端 length 换算口径一致；无真实 fps 事实时不虚构更精确步长）。 */
const FRAME_STEP_SECONDS = 1 / 25;

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

  // V31-045 切片 A：标注工作台需要的播放控制（蓝图 §5）。
  // 倍速集合固定（0.25x–2x）；帧步进按 1/25s（与后端 length 换算口径一致，
  // 无真实 fps 事实时不虚构更精确的步长）；快捷键绑定在容器 div 上，
  // 仅当视频可播放时生效，避免与页面滚动冲突。
  const [rate, setRate] = useState<number>(1);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [videoDuration, setVideoDuration] = useState<number | null>(null);

  const applyRate = useCallback(
    (next: number) => {
      setRate(next);
      const video = videoRef.current;
      if (video) {
        video.playbackRate = next;
      }
    },
    [],
  );

  const seekBy = useCallback(
    (deltaSeconds: number) => {
      const video = videoRef.current;
      if (!video) {
        return;
      }
      const bound = videoDuration ?? durationSeconds ?? 0;
      const next = Math.min(Math.max(0, video.currentTime + deltaSeconds), bound);
      video.currentTime = next;
      setCurrentTime(next);
    },
    [durationSeconds, videoDuration],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (!signed) {
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        seekBy(event.shiftKey ? -1 : -FRAME_STEP_SECONDS);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        seekBy(event.shiftKey ? 1 : FRAME_STEP_SECONDS);
      } else if (event.key === ",") {
        event.preventDefault();
        seekBy(-FRAME_STEP_SECONDS);
      } else if (event.key === ".") {
        event.preventDefault();
        seekBy(FRAME_STEP_SECONDS);
      }
    },
    [seekBy, signed],
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
          <div onKeyDown={handleKeyDown}>
            <video
              ref={videoRef}
              data-testid="media-preview-video"
              src={signed.url}
              controls
              preload="metadata"
              playsInline
              aria-label={`Episode 媒体：${asset.originalFilename}`}
              style={{ width: "100%", maxHeight: 420, background: "#000" }}
              onRateChange={(event) => setRate(event.currentTarget.playbackRate)}
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              onLoadedMetadata={(event) => {
                const video = event.currentTarget;
                video.playbackRate = rate;
                setVideoDuration(Number.isFinite(video.duration) ? video.duration : null);
              }}
            />
            <Space wrap size={6} style={{ marginTop: 8 }}>
              <Typography.Text type="secondary">倍速</Typography.Text>
              {PLAYBACK_RATES.map((value) => (
                <Button
                  key={value}
                  size="small"
                  type={rate === value ? "primary" : "default"}
                  onClick={() => applyRate(value)}
                  data-testid={`media-rate-${value}`}
                >
                  {value}x
                </Button>
              ))}
              <Button size="small" onClick={() => seekBy(-FRAME_STEP_SECONDS)}>
                ‹ 帧
              </Button>
              <Button size="small" onClick={() => seekBy(FRAME_STEP_SECONDS)}>
                帧 ›
              </Button>
              <Typography.Text data-testid="media-clock" type="secondary">
                {formatPlaybackClock(currentTime)} /{" "}
                {videoDuration !== null
                  ? formatPlaybackClock(videoDuration)
                  : durationSeconds !== null
                    ? formatPlaybackClock(durationSeconds)
                    : "--:--"}
              </Typography.Text>
              <Typography.Text type="secondary" mark>
                快捷键：←/→ ±1s · ,/. 逐帧
              </Typography.Text>
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
          </div>
        ) : (
          <PageState compact variant="empty" title="尚无可播放地址" />
        )}
      </Space>
    </Card>
  );
}
