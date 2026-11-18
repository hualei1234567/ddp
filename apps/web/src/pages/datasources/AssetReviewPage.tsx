import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { dataSourceApi } from "../../services/datasources";
import { presentCamera } from "../../services/datasourceParsers";
import { formatDateTime } from "../../services/format";
import type { MediaAsset } from "../../types/datasource";
import {
  MAX_SIDE_BY_SIDE,
  PLAYBACK_RATES,
  clampSeek,
  defaultCameraSelection,
  formatClock,
  normalizeCameraSelection,
  toggleCameraSelection,
} from "./playback";

/**
 * 视频审看页。
 *
 * 技术选型见 ADR-0017：原生 `<video>`，不自研播放内核，不引入 HLS 库
 * （本数据集是 MP4，无需 HLS）。时间轴、倍速与机位布局是业务组件，自己实现。
 *
 * 多机位是**独立播放**：每个 `<video>` 各自持有预签名 URL、时间轴与倍速，
 * 不做强制音画同步——强行同步在预签名串流下会带来抖动与额外控制器，
 * 而审看只需要并排比对。
 *
 * 播放地址由服务端签发短时预签名 URL；前端不拼存储路径，也不长期缓存 URL。
 */

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—";
  }
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.round(bytes / 1024)} KB`;
}

function formatCameraMeta(asset: MediaAsset): string {
  const fps = asset.source_fps === null ? "" : ` · ${asset.source_fps}fps`;
  return `${presentCamera(asset.camera)} · ${formatBytes(asset.size_bytes)}${fps}`;
}

export interface PlaybackControlsProps {
  cameraLabel: string;
  rate: number;
  currentTime: number;
  duration: number;
  excerptStartSeconds: number | null;
  onRateChange: (rate: number) => void;
  onSeek: (seconds: number) => void;
  onJumpToExcerpt: () => void;
}

/**
 * 播放控制条：倍速、当前/总时长、时间轴与片段锚点。
 *
 * 原生 range 自带键盘操作（方向键/Home/End），配合 aria-label 可被读屏识别；
 * 不引入第三方播放器封装。
 */
export function PlaybackControls({
  cameraLabel,
  rate,
  currentTime,
  duration,
  excerptStartSeconds,
  onRateChange,
  onSeek,
  onJumpToExcerpt,
}: PlaybackControlsProps) {
  const upper = Number.isFinite(duration) && duration > 0 ? duration : 0;
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Space wrap size="small" align="center">
        <div role="group" aria-label={`${cameraLabel}播放倍速`}>
          <Segmented
            value={rate}
            options={PLAYBACK_RATES.map((value) => ({
              value,
              label: `${value}×`,
            }))}
            onChange={(value) => onRateChange(Number(value))}
          />
        </div>
        <Typography.Text type="secondary">
          {formatClock(currentTime)} / {formatClock(upper)}
        </Typography.Text>
        <Button
          size="small"
          onClick={onJumpToExcerpt}
          aria-label={`${cameraLabel}跳至片段`}
        >
          跳至片段 {excerptStartSeconds ?? 0}s
        </Button>
      </Space>
      <input
        type="range"
        min={0}
        max={upper}
        step={0.1}
        value={clampSeek(currentTime, upper)}
        onChange={(event) => onSeek(Number(event.target.value))}
        aria-label={`${cameraLabel}时间轴`}
        aria-valuetext={`${formatClock(currentTime)} / ${formatClock(upper)}`}
        style={{ width: "100%" }}
      />
    </Space>
  );
}

export interface CameraPickerProps {
  cameras: readonly string[];
  value: readonly string[];
  onChange: (next: string[]) => void;
}

/** 机位选择：≤2 路直接并排；>2 路最多勾选两路同屏。 */
export function CameraPicker({ cameras, value, onChange }: CameraPickerProps) {
  if (cameras.length <= MAX_SIDE_BY_SIDE) {
    return (
      <div role="group" aria-label="当前同屏机位">
        <Space wrap size="small">
          <Typography.Text>机位</Typography.Text>
          <Typography.Text type="secondary">
            {value.map((camera) => presentCamera(camera)).join(" + ") || "—"}
          </Typography.Text>
        </Space>
      </div>
    );
  }
  return (
    <div role="group" aria-label="选择同屏机位">
      <Space wrap size="small">
        <Typography.Text>同屏机位（最多 2 个）</Typography.Text>
        {cameras.map((camera) => (
          <Checkbox
            key={camera}
            checked={value.includes(camera)}
            onChange={(event) =>
              onChange(
                toggleCameraSelection(
                  value,
                  camera,
                  event.target.checked,
                  cameras,
                ),
              )
            }
          >
            {presentCamera(camera)}
          </Checkbox>
        ))}
      </Space>
    </div>
  );
}

function Player({
  projectId,
  asset,
}: {
  projectId: string;
  asset: MediaAsset;
}) {
  const cameraLabel = presentCamera(asset.camera);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [rate, setRate] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // 播放 URL 每 15 分钟过期；换机位或手动刷新时重新签发。
  const loadUrl = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const playback = await dataSourceApi.playback(projectId, asset.asset_id, {
          signal,
        });
        setUrl(playback.url);
      } catch (caught) {
        if (caught instanceof Error) {
          setError(caught.message);
        }
      } finally {
        setLoading(false);
      }
    },
    [projectId, asset.asset_id],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadUrl(controller.signal);
    return () => controller.abort();
  }, [loadUrl]);

  // 倍速是播放器状态，不依赖 URL 加载；元数据就绪后再次应用以防被重置。
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.playbackRate = rate;
    }
  }, [rate, url]);

  useEffect(() => {
    setDuration(0);
    setCurrentTime(0);
  }, [url]);

  const seekTo = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    const upper =
      Number.isFinite(video.duration) && video.duration > 0
        ? video.duration
        : seconds;
    const target = clampSeek(seconds, upper);
    video.currentTime = target;
    setCurrentTime(target);
  }, []);

  // 片段定位：元数据给了 excerpt_start_seconds，打开即跳到该时间点。
  const jumpToExcerpt = useCallback(() => {
    if (asset.excerpt_start_seconds !== null) {
      seekTo(asset.excerpt_start_seconds);
    }
  }, [asset.excerpt_start_seconds, seekTo]);

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    setDuration(Number.isFinite(video.duration) ? video.duration : 0);
    video.playbackRate = rate;
    if (asset.excerpt_start_seconds !== null) {
      video.currentTime = asset.excerpt_start_seconds;
      setCurrentTime(asset.excerpt_start_seconds);
    }
  }, [asset.excerpt_start_seconds, rate]);

  const content = error ? (
    <Alert
      type="error"
      showIcon
      message="无法获取播放地址"
      description={error}
      action={
        <Button size="small" onClick={() => void loadUrl()}>
          重试
        </Button>
      }
    />
  ) : loading || !url ? (
    <div style={{ padding: 48, textAlign: "center" }}>
      <Spin tip="正在获取播放地址…" />
    </div>
  ) : (
    <Space direction="vertical" style={{ width: "100%" }} size="small">
      <video
        ref={videoRef}
        src={url}
        controls
        preload="metadata"
        aria-label={`${cameraLabel}视频`}
        onLoadedMetadata={handleLoadedMetadata}
        onTimeUpdate={(event) =>
          setCurrentTime(event.currentTarget.currentTime)
        }
        onDurationChange={(event) =>
          setDuration(
            Number.isFinite(event.currentTarget.duration)
              ? event.currentTarget.duration
              : 0,
          )
        }
        style={{ width: "100%", maxHeight: 360, background: "#000" }}
      >
        {/* 原生 video 无字幕轨时也需要可访问名称 */}
        <track kind="captions" />
      </video>
      <PlaybackControls
        cameraLabel={cameraLabel}
        rate={rate}
        currentTime={currentTime}
        duration={duration}
        excerptStartSeconds={asset.excerpt_start_seconds}
        onRateChange={setRate}
        onSeek={seekTo}
        onJumpToExcerpt={jumpToExcerpt}
      />
      <Space wrap size="small">
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatCameraMeta(asset)}
        </Typography.Text>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => void loadUrl()}
        >
          刷新地址
        </Button>
      </Space>
    </Space>
  );

  return (
    <Card
      size="small"
      title={cameraLabel}
      extra={<Tag>独立播放</Tag>}
      style={{ height: "100%" }}
    >
      {content}
    </Card>
  );
}

function ReviewContent({
  projectId,
  importId,
}: {
  projectId: string;
  importId: string;
}) {
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [episode, setEpisode] = useState<string | null>(null);
  const [selectedCameras, setSelectedCameras] = useState<string[] | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const items = await dataSourceApi.listImportAssets(projectId, importId, {
          signal,
          limit: 1000,
        });
        setAssets(items);
      } catch (caught) {
        if (caught instanceof Error) {
          setError(caught);
        }
      } finally {
        setLoading(false);
      }
    },
    [projectId, importId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const episodes = useMemo(
    () => Array.from(new Set(assets.map((item) => item.episode_key))),
    [assets],
  );
  const currentEpisode = episode ?? episodes[0] ?? null;
  const episodeAssets = useMemo(
    () => assets.filter((item) => item.episode_key === currentEpisode),
    [assets, currentEpisode],
  );
  const cameras = useMemo(
    () => Array.from(new Set(episodeAssets.map((item) => item.camera))),
    [episodeAssets],
  );

  const visibleCameras = useMemo(() => {
    if (selectedCameras === null) {
      return defaultCameraSelection(cameras);
    }
    const normalized = normalizeCameraSelection(selectedCameras, cameras);
    return normalized.length > 0 ? normalized : defaultCameraSelection(cameras);
  }, [cameras, selectedCameras]);

  const visibleAssets = visibleCameras
    .map((camera) => episodeAssets.find((item) => item.camera === camera))
    .filter((item): item is MediaAsset => item !== undefined);

  // 统一状态组件：与全站其余 60+ 页面一致，避免本页出现裸写
  // Spin/Alert/Empty 导致六态覆盖不一致（DEV-004 审计点名项）。
  if (loading) {
    return <PageState variant="loading" title="正在加载媒体资产…" />;
  }

  if (error) {
    return (
      <PageState
        variant="error"
        title="加载失败"
        description={error.message}
        onRetry={() => void load()}
      />
    );
  }

  if (assets.length === 0) {
    return (
      <PageState
        variant="empty"
        title="该导入没有产出媒体资产"
        description="请确认导入任务已完成"
      />
    );
  }

  const primary = visibleAssets[0];

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Typography.Text>片段</Typography.Text>
        <Segmented
          aria-label="选择片段"
          value={currentEpisode ?? undefined}
          options={episodes.map((key) => ({ value: key, label: key }))}
          onChange={(value) => {
            setEpisode(String(value));
            setSelectedCameras(null);
          }}
        />
      </Space>
      <CameraPicker
        cameras={cameras}
        value={visibleCameras}
        onChange={setSelectedCameras}
      />
      {primary ? (
        <>
          <Space wrap size="small">
            <Tag color="blue">{primary.task_name}</Tag>
            <Typography.Text type="secondary">
              片段时长 {formatDuration(primary.excerpt_seconds)}
            </Typography.Text>
            <Typography.Text type="secondary">
              导入于 {formatDateTime(primary.created_at)}
            </Typography.Text>
          </Space>
          <div
            style={{
              display: "flex",
              gap: 12,
              alignItems: "stretch",
              flexWrap: "wrap",
            }}
          >
            {visibleAssets.map((asset) => (
              <div
                key={asset.asset_id}
                style={{ flex: "1 1 320px", minWidth: 280 }}
              >
                <Player projectId={projectId} asset={asset} />
              </div>
            ))}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            各机位独立播放：可分别暂停、拖动时间轴与调整倍速，不做强制音画同步。
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            校验和 {primary.sha256.slice(0, 24)}… · 存储键 {primary.object_key}
          </Typography.Text>
        </>
      ) : (
        <Empty description="该片段暂无机位视频" />
      )}
    </Space>
  );
}

export default function AssetReviewPage() {
  const projectId = useProjectId();
  const { importId } = useParams<{ importId: string }>();
  const [searchParams] = useSearchParams();
  const resolvedImportId = importId ?? searchParams.get("import_id") ?? "";

  if (!projectId) {
    return (
      <PageContainer title="视频审看" description="媒体资产必须按项目隔离展示。">
        <ProjectScopeRequired scope="视频审看" />
      </PageContainer>
    );
  }
  if (!resolvedImportId) {
    return (
      <PageContainer title="视频审看">
        <Empty description="缺少导入任务 ID；请从导入任务列表进入" />
      </PageContainer>
    );
  }
  return (
    <PageContainer
      title="视频审看"
      description="按片段与机位逐条查看导入的真实数据；播放地址由服务端短时签发。"
    >
      <ReviewContent projectId={projectId} importId={resolvedImportId} />
    </PageContainer>
  );
}
