import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Typography,
} from "antd";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import EpisodeActionPanel from "../../components/episodes/EpisodeActionPanel";
import EpisodeRevisionPanel from "../../components/episodes/EpisodeRevisionPanel";
import EpisodeQcEvidenceSection from "../../components/quality/EpisodeQcEvidenceSection";
import EpisodeQualityPanel from "../../components/quality/EpisodeQualityPanel";
import LineagePanel from "../../components/episodes/LineagePanel";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { qualityRunApi } from "../../services/qualityRuns";
import { ApiError } from "../../services/api";
import { collectionsApi } from "../../services/collections";
import {
  buildEpisodeDetailPath,
  buildEpisodeListPath,
  buildRawAssetDetailPath,
  buildTaskDetailPath,
} from "../../services/collectionUrl";
import { readIdQuery } from "../../services/annotationUrl";
import {
  formatNanoseconds,
  getEpisodeStatus,
} from "../../services/collectionPresentation";
import {
  describeEpisodeWriteFailure,
  type EpisodeActionFeedback,
} from "../../services/episodeActions";
import { formatDateTime, formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  Episode,
  EpisodeAction,
  EpisodeActionInput,
} from "../../types/collection";

function EpisodeDetailBody({
  projectId,
  episode,
  online,
  qcRunId,
}: {
  projectId: string;
  episode: Episode;
  online: boolean;
  /** URL `qc_run_id`：从质量运行详情/导入质检深链过来的证据运行。 */
  qcRunId: string | null;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"action" | "revision" | null>(null);
  const [actionFeedback, setActionFeedback] =
    useState<EpisodeActionFeedback | null>(null);
  const [revisionFeedback, setRevisionFeedback] =
    useState<EpisodeActionFeedback | null>(null);

  const runAction = useCallback(
    async (action: EpisodeAction, input: EpisodeActionInput) => {
      setBusy("action");
      setActionFeedback(null);
      try {
        const updated = await collectionsApi.actOnEpisode(
          projectId,
          episode.episode_id,
          action,
          input,
          { version: episode.version },
        );
        setActionFeedback({
          kind: "success",
          title: "服务端已接受动作",
          description: `Episode 当前状态：${getEpisodeStatus(updated.status).label}；版本 v${updated.version}。`,
        });
      } catch (caught) {
        setActionFeedback(describeEpisodeWriteFailure(caught, "动作未生效"));
      } finally {
        setBusy(null);
      }
    },
    [episode.episode_id, episode.version, projectId],
  );

  const runRevision = useCallback(
    async (code: string) => {
      setBusy("revision");
      setRevisionFeedback(null);
      try {
        const revised = await collectionsApi.reviseEpisode(
          projectId,
          episode.episode_id,
          { code },
          { version: episode.version },
        );
        navigate(
          buildEpisodeDetailPath(projectId, revised.episode_id),
          { replace: false },
        );
      } catch (caught) {
        setRevisionFeedback(
          describeEpisodeWriteFailure(caught, "修订未创建"),
        );
      } finally {
        setBusy(null);
      }
    },
    [episode.episode_id, episode.version, navigate, projectId],
  );

  const manifestMissing = episode.manifest_key === null;
  const notChecked = episode.quality_status === "NOT_CHECKED";

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {notChecked ? (
        <PageState
          variant="partial"
          compact
          title="该 Episode 尚未完成质检"
          description="质量状态为 NOT_CHECKED；即使已 READY，也不得视为可交付的 ACCEPTED 数据。"
        />
      ) : null}
      {manifestMissing ? (
        <PageState
          variant="partial"
          compact
          title="Manifest 尚未生成"
          description="服务端尚未返回 manifest_key/manifest_sha256；要求 ready 前请确认解析 Worker 已产出 Manifest。"
        />
      ) : null}
      {episode.success === false ? (
        <Alert
          type="error"
          showIcon
          message="该 Episode 标记为采集失败"
          description={episode.failure_reason ?? "服务端未返回失败原因，请刷新或联系采集负责人。"}
        />
      ) : null}
      {episode.supersedes_id ? (
        <Alert
          type="info"
          showIcon
          message="该版本是修订产物"
          description={
            <Space wrap>
              <span>取代版本：</span>
              <IdentifierValue
                value={episode.supersedes_id}
                label="被取代 Episode ID"
                to={buildEpisodeDetailPath(projectId, episode.supersedes_id)}
              />
            </Space>
          }
        />
      ) : null}
      {episode.raw_asset_ids.length > 0 ? (
        <Alert
          type="info"
          showIcon
          message="原始资产输入"
          description={
            <Space wrap>
              {episode.raw_asset_ids.map((assetId) => (
                <IdentifierValue
                  key={assetId}
                  value={assetId}
                  label="原始资产 ID"
                  to={buildRawAssetDetailPath(projectId, assetId)}
                />
              ))}
            </Space>
          }
        />
      ) : (
        <Alert
          type="info"
          showIcon
          message="原始资产关系请查看反向血缘"
          description="当前 Episode 响应未内联 raw_asset_ids；上游原始资产通过 reverse 血缘查询，页面不推断关系。"
        />
      )}

      <Card>
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 1, md: 2, lg: 3 }}
          items={[
            {
              key: "episode_id",
              label: "Episode ID",
              children: (
                <IdentifierValue value={episode.episode_id} label="Episode ID" />
              ),
            },
            {
              key: "lineage_id",
              label: "谱系 ID",
              children: (
                <IdentifierValue
                  value={episode.episode_lineage_id}
                  label="谱系 ID"
                />
              ),
            },
            {
              key: "version",
              label: "版本",
              children: `v${episode.version} / 切片版本 ${episode.episode_version}`,
            },
            {
              key: "task",
              label: "采集任务",
              children: episode.task_id ? (
                <Link to={buildTaskDetailPath(projectId, episode.task_id)}>
                  查看任务
                </Link>
              ) : (
                "—"
              ),
            },
            {
              key: "batch",
              label: "批次",
              children: (
                <IdentifierValue value={episode.batch_id} label="批次 ID" />
              ),
            },
            {
              key: "robot",
              label: "机器人",
              children: (
                <IdentifierValue value={episode.robot_id} label="机器人 ID" />
              ),
            },
            {
              key: "devices",
              label: "设备",
              children:
                episode.device_ids.length > 0
                  ? episode.device_ids.join("、")
                  : "—",
            },
            {
              key: "sensors",
              label: "传感器",
              children:
                episode.sensor_ids.length > 0
                  ? episode.sensor_ids.join("、")
                  : "—",
            },
            {
              key: "frame_count",
              label: "帧数",
              children: episode.frame_count.toLocaleString("zh-CN"),
            },
            {
              key: "start",
              label: "开始时间",
              children: formatNanoseconds(episode.start_time_ns),
            },
            {
              key: "end",
              label: "结束时间",
              children: formatNanoseconds(episode.end_time_ns),
            },
            {
              key: "duration",
              label: "时长",
              children: formatNanoseconds(episode.duration_ns),
            },
            {
              key: "time_base",
              label: "时间基准",
              children: `${episode.time_base.clock_source} / ${episode.time_base.sync_method} / ${episode.time_base.sync_status}`,
            },
            {
              key: "pipeline",
              label: "Pipeline",
              children: `${episode.pipeline_id}@${episode.pipeline_version}`,
            },
            {
              key: "canonical",
              label: "Canonical Schema",
              children: episode.canonical_schema_version,
            },
            {
              key: "segmentation",
              label: "切片规则",
              children: episode.segmentation_rule_version,
            },
            {
              key: "quality",
              label: "质检",
              children: (
                <CollectionStatusTag
                  kind="quality"
                  value={episode.quality_status}
                />
              ),
            },
            {
              key: "annotation",
              label: "标注",
              children: (
                <CollectionStatusTag
                  kind="annotation"
                  value={episode.annotation_status}
                />
              ),
            },
            {
              key: "manifest_key",
              label: "Manifest Key",
              children: episode.manifest_key ?? "—",
            },
            {
              key: "manifest_sha256",
              label: "Manifest SHA256",
              children: episode.manifest_sha256 ?? "—",
            },
            {
              key: "labels",
              label: "场景标签",
              children:
                episode.scene_labels.length > 0
                  ? episode.scene_labels.join("、")
                  : "—",
            },
            {
              key: "instruction",
              label: "语言指令",
              children: episode.language_instruction ?? "—",
            },
            {
              key: "attempt",
              label: "处理尝试",
              children:
                episode.attempt !== undefined
                  ? `${episode.attempt} / ${episode.max_attempts ?? "—"}`
                  : "—",
            },
            {
              key: "created",
              label: "创建时间",
              children: formatDateTime(episode.created_at),
            },
            {
              key: "updated",
              label: "更新时间",
              children: formatDateTime(episode.updated_at),
            },
          ]}
        />
      </Card>

      <EpisodeQualityPanel
        projectId={projectId}
        episodeId={episode.episode_id}
      />

      {qcRunId !== null ? (
        <QcEvidenceLoader
          projectId={projectId}
          episode={episode}
          runId={qcRunId}
        />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <EpisodeActionPanel
            episode={episode}
            online={online}
            busy={busy === "action"}
            feedback={actionFeedback}
            onSubmit={(action, input) => void runAction(action, input)}
          />
        </Col>
        <Col xs={24} xl={12}>
          <EpisodeRevisionPanel
            episode={episode}
            online={online}
            busy={busy === "revision"}
            feedback={revisionFeedback}
            onSubmit={(code) => void runRevision(code)}
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <LineagePanel
            projectId={projectId}
            episodeId={episode.episode_id}
            direction="forward"
            online={online}
          />
        </Col>
        <Col xs={24} xl={12}>
          <LineagePanel
            projectId={projectId}
            episodeId={episode.episode_id}
            direction="reverse"
            online={online}
          />
        </Col>
      </Row>
    </Space>
  );
}

/**
 * 读取 URL 指定的质量运行，再交给证据定位区。
 *
 * 为什么要先取 run 再取 findings：findings 只给结论，**不给** `rule_set_version`
 * 这类解释"本次用的是哪版规则"的元数据；缺了它，证据面板无法回答
 * "这条结论是旧规则判的还是新规则判的"。两个请求都走同一 project 作用域。
 */
function QcEvidenceLoader({
  projectId,
  episode,
  runId,
}: {
  projectId: string;
  episode: Episode;
  runId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      qualityRunApi.getRun(projectId, runId, { signal }),
    [projectId, runId],
  );
  const { data, error, loading, reload } = useRequest(load);

  if (loading) {
    return (
      <Card title="QC 证据定位（时间轴 / 流 / 字段）">
        <PageState
          compact
          variant="loading"
          title="正在读取质量运行"
          description="读取运行元数据与结论明细。"
        />
      </Card>
    );
  }
  if (error !== null && data === null) {
    return (
      <Card title="QC 证据定位（时间轴 / 流 / 字段）">
        <ApiErrorState
          compact
          error={
            error instanceof ApiError
              ? error
              : new ApiError({
                  kind: "network",
                  message: "质量运行读取失败。",
                  cause: error,
                })
          }
          onRetry={reload}
        />
      </Card>
    );
  }
  if (data === null) {
    return null;
  }
  return (
    <EpisodeQcEvidenceSection
      projectId={projectId}
      episode={episode}
      run={data}
    />
  );
}

function EpisodeDetailContent({
  projectId,
  episodeId,
  qcRunId,
}: {
  projectId: string;
  episodeId: string;
  qcRunId: string | null;
}) {
  const online = useOnlineStatus();
  const load = useCallback(
    (signal: AbortSignal) =>
      collectionsApi.getEpisode(projectId, episodeId, { signal }),
    [projectId, episodeId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Link to={buildEpisodeListPath(projectId)}>
        <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
      </Link>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body: ReactNode;
  if (phase === "forbidden") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "not-found") {
    body = (
      <PageState
        variant="empty"
        title="Episode 不存在或无权查看"
        description="服务端未找到该 Episode；跨项目资源统一不可见。"
        action={
          <Link to={buildEpisodeListPath(projectId)}>
            <Button>返回 Episode 列表</Button>
          </Link>
        }
      />
    );
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载 Episode 详情"
        description="正在读取版本、Manifest、动作与血缘信息。"
      />
    );
  } else if (phase === "error") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (!data) {
    body = <PageState variant="empty" title="暂无 Episode 数据" />;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功获取的 Episode 数据；状态与版本可能已变化。"
            onRetry={reload}
          />
        ) : null}
        <EpisodeDetailBody
          projectId={projectId}
          episode={data}
          online={online}
          qcRunId={qcRunId}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={
        data ? (
          <Space wrap>
            <span>Episode {data.code}</span>
            <CollectionStatusTag kind="episode" value={data.status} />
          </Space>
        ) : (
          "Episode 详情"
        )
      }
      description="展示服务端 Episode 版本、时间语义、动作、修订与正反向血缘；权限与状态转换以服务端为准。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function EpisodeDetailPage() {
  const projectId = useProjectId();
  const { episodeId } = useParams<{ episodeId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  // 只读深链参数：空串按"未提供"处理，避免把 `?qc_run_id=` 当成一个 ID。
  const qcRunId = readIdQuery(searchParams, "qc_run_id");

  if (!projectId) {
    return (
      <PageContainer title="Episode 详情" description="Episode 必须按项目隔离展示。">
        <PageState
          variant="empty"
          title="未选择项目"
          description="请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。"
          action={
            <Button type="primary" onClick={() => navigate("/")}>
              返回工作台
            </Button>
          }
        />
      </PageContainer>
    );
  }

  if (!episodeId) {
    return (
      <PageContainer title="Episode 详情" description="请选择要查看的 Episode。">
        <PageState
          variant="empty"
          title="缺少 Episode ID"
          description="URL 中未包含 episode_id。"
          action={
            <Link to={buildEpisodeListPath(projectId)}>
              <Button>返回列表</Button>
            </Link>
          }
        />
      </PageContainer>
    );
  }

  return (
    <EpisodeDetailContent
      key={`${projectId}:${episodeId}:${qcRunId ?? ""}`}
      projectId={projectId}
      episodeId={episodeId}
      qcRunId={qcRunId}
    />
  );
}
