import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
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
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { dataSourceApi } from "../../services/datasources";
import { presentCamera } from "../../services/datasourceParsers";
import { formatDateTime } from "../../services/format";
import type { MediaAsset } from "../../types/datasource";

/**
 * 视频审看页。
 *
 * 技术选型见 ADR-0017：原生 `<video>`，不自研播放内核，不引入 HLS 库
 * （本数据集是 MP4，无需 HLS）。时间轴与机位切换是业务组件，自己实现。
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

function Player({
  projectId,
  asset,
}: {
  projectId: string;
  asset: MediaAsset;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
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

  // 片段定位：元数据给了 excerpt_start_seconds，打开即跳到该时间点。
  const seekToExcerpt = () => {
    const video = videoRef.current;
    if (video && asset.excerpt_start_seconds !== null) {
      video.currentTime = asset.excerpt_start_seconds;
    }
  };

  if (error) {
    return (
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
    );
  }

  if (loading || !url) {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <Spin tip="正在获取播放地址…" />
      </div>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="small">
      <video
        ref={videoRef}
        src={url}
        controls
        preload="metadata"
        onLoadedMetadata={seekToExcerpt}
        style={{ width: "100%", maxHeight: 520, background: "#000" }}
      >
        {/* 原生 video 无字幕轨时也需要可访问名称 */}
        <track kind="captions" />
      </video>
      <Space wrap size="small">
        <Button size="small" onClick={seekToExcerpt}>
          跳至片段 {asset.excerpt_start_seconds ?? 0}s
        </Button>
        <Typography.Text type="secondary">
          {formatCameraMeta(asset)}
        </Typography.Text>
        <Button size="small" icon={<ReloadOutlined />} onClick={() => void loadUrl()}>
          刷新地址
        </Button>
      </Space>
    </Space>
  );
}

function formatCameraMeta(asset: MediaAsset): string {
  const fps = asset.source_fps === null ? "" : ` · ${asset.source_fps}fps`;
  return `${presentCamera(asset.camera)} · ${formatBytes(asset.size_bytes)}${fps}`;
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
  const [camera, setCamera] = useState<string | null>(null);

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
  const currentCamera = camera ?? cameras[0] ?? null;
  const current = episodeAssets.find((item) => item.camera === currentCamera);

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <Spin tip="正在加载媒体资产…" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="加载失败"
        description={error.message}
        action={
          <Button size="small" onClick={() => void load()}>
            重试
          </Button>
        }
      />
    );
  }

  if (assets.length === 0) {
    return (
      <Empty description="该导入没有产出媒体资产；请确认导入任务已完成" />
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Typography.Text>片段</Typography.Text>
        <Segmented
          value={currentEpisode ?? undefined}
          options={episodes.map((key) => ({ value: key, label: key }))}
          onChange={(value) => {
            setEpisode(String(value));
            setCamera(null);
          }}
        />
      </Space>
      <Space wrap>
        <Typography.Text>机位</Typography.Text>
        <Segmented
          value={currentCamera ?? undefined}
          options={cameras.map((key) => ({
            value: key,
            label: presentCamera(key),
          }))}
          onChange={(value) => setCamera(String(value))}
        />
      </Space>
      {current ? (
        <>
          <Space wrap size="small">
            <Tag color="blue">{current.task_name}</Tag>
            <Typography.Text type="secondary">
              片段时长 {formatDuration(current.excerpt_seconds)}
            </Typography.Text>
            <Typography.Text type="secondary">
              导入于 {formatDateTime(current.created_at)}
            </Typography.Text>
          </Space>
          <Player
            key={current.asset_id}
            projectId={projectId}
            asset={current}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            校验和 {current.sha256.slice(0, 24)}… · 存储键 {current.object_key}
          </Typography.Text>
        </>
      ) : (
        <Empty description="该机位暂无视频" />
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
