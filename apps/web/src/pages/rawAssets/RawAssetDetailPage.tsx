import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Tag,
  Typography,
} from "antd";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import DownloadButton from "../../components/rawAssets/DownloadButton";
import RawAssetActionBar from "../../components/rawAssets/RawAssetActionBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import {
  buildEpisodeListPath,
  buildTaskDetailPath,
} from "../../services/collectionUrl";
import { getRawAssetStatus } from "../../services/collectionPresentation";
import { formatBytes } from "../../services/uploadQueue";
import { formatDateTime, formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import { rawAssetsApi } from "../../services/rawAssets";
import {
  RAW_ASSET_ACTION_VALUES,
  type RawAsset,
  type RawAssetAction,
} from "../../types/collection";
import type { RawAssetActionPayload } from "../../components/rawAssets/RawAssetActionBar";

function statusAlert(asset: RawAsset): ReactNode {
  switch (asset.status) {
    case "VALIDATING":
      return (
        <PageState
          variant="partial"
          compact
          title="资产仍在服务端校验中"
          description="整文件 Hash、格式签名与安全扫描完成前不可下载；页面不伪造校验通过。"
        />
      );
    case "QUARANTINED":
      return (
        <PageState
          variant="partial"
          compact
          title="资产已进入隔离区"
          description={`隔离原因：${asset.quarantine_reason ?? "服务端未返回原因"}；禁止下载，需走隔离放行流程。`}
        />
      );
    case "REJECTED":
      return (
        <PageState
          variant="partial"
          compact
          title="资产校验未通过"
          description="服务端拒绝登记为可用资产；仍保留元数据与血缘，可用于排障。"
        />
      );
    case "TOMBSTONED":
      return (
        <PageState
          variant="partial"
          compact
          title="资产已墓碑化"
          description={`墓碑时间：${formatDateTime(asset.tombstoned_at)}；原因：${asset.quarantine_reason ?? "服务端未返回原因"}。只保留元数据与血缘，下载被拒绝。`}
        />
      );
    default:
      return null;
  }
}

function RawAssetDetailBody({
  projectId,
  asset,
  online,
  onAction,
  busyAction,
}: {
  projectId: string;
  asset: RawAsset;
  online: boolean;
  onAction: (action: string, payload: RawAssetActionPayload) => Promise<void>;
  busyAction: string | null;
}) {
  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {statusAlert(asset)}
      {asset.duplicate_of_asset_id ? (
        <Alert
          type="info"
          showIcon
          message="该资产命中内容去重"
          description={
            <Space wrap>
              <span>复用既有资产：</span>
              <IdentifierValue
                value={asset.duplicate_of_asset_id}
                label="重复资产 ID"
              />
            </Space>
          }
        />
      ) : null}
      <Card title="下载">
        <DownloadButton
          projectId={projectId}
          assetId={asset.asset_id}
          status={asset.status}
          online={online}
        />
      </Card>
      <Card title="状态操作">
        {/* 始终渲染：终态资产也明确告诉用户"没有可执行动作"，
            而不是整块消失、让人以为页面出了问题。 */}
        <RawAssetActionBar
          actions={asset.allowed_actions}
          busyAction={busyAction}
          onAction={onAction}
        />
      </Card>
      <Card>
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 1, md: 2, lg: 3 }}
          items={[
            {
              key: "asset_id",
              label: "资产 ID",
              children: (
                <IdentifierValue value={asset.asset_id} label="原始资产 ID" />
              ),
            },
            {
              key: "code",
              label: "资产编号",
              children: asset.code,
            },
            {
              key: "status",
              label: "状态",
              children: (
                <Tag color={getRawAssetStatus(asset.status).color}>
                  {getRawAssetStatus(asset.status).label}
                </Tag>
              ),
            },
            {
              key: "filename",
              label: "原始文件名",
              children: asset.original_filename,
            },
            {
              key: "format",
              label: "格式",
              children: `${asset.format} / ${asset.content_type}`,
            },
            {
              key: "size",
              label: "大小",
              children: formatBytes(asset.size_bytes),
            },
            {
              key: "source",
              label: "来源",
              children: asset.source_type,
            },
            {
              key: "task",
              label: "采集任务",
              children: asset.collection_task_id ? (
                <Link to={buildTaskDetailPath(projectId, asset.collection_task_id)}>
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
                <IdentifierValue value={asset.batch_id} label="批次 ID" />
              ),
            },
            {
              key: "session",
              label: "上传会话",
              children: (
                <IdentifierValue
                  value={asset.upload_session_id}
                  label="上传会话 ID"
                />
              ),
            },
            {
              key: "sha256",
              label: "SHA256",
              children: asset.sha256,
            },
            {
              key: "object_key",
              label: "对象 Key",
              children: asset.object_key,
            },
            {
              key: "storage",
              label: "存储级别",
              children: asset.storage_class,
            },
            {
              key: "captured",
              label: "采集时间",
              children: formatDateTime(asset.captured_at),
            },
            {
              key: "ingested",
              label: "接入时间",
              children: formatDateTime(asset.ingested_at),
            },
            {
              key: "verified",
              label: "校验时间",
              children: formatDateTime(asset.verified_at),
            },
            {
              key: "archived",
              label: "归档时间",
              children: formatDateTime(asset.archived_at),
            },
            {
              key: "devices",
              label: "设备",
              children:
                asset.device_ids.length > 0 ? asset.device_ids.join("、") : "—",
            },
            {
              key: "sensors",
              label: "传感器",
              children:
                asset.sensor_ids.length > 0 ? asset.sensor_ids.join("、") : "—",
            },
            {
              key: "quarantine",
              // 同一列承载"最近一次终止原因"：隔离、驳回与墓碑都写在这里。
              label: "隔离 / 终态原因",
              children: asset.quarantine_reason ?? "—",
            },
            {
              key: "created",
              label: "创建时间",
              children: formatDateTime(asset.created_at),
            },
            {
              key: "updated",
              label: "更新时间",
              children: formatDateTime(asset.updated_at),
            },
          ]}
        />
      </Card>
    </Space>
  );
}

function RawAssetDetailContent({
  projectId,
  assetId,
}: {
  projectId: string;
  assetId: string;
}) {
  const online = useOnlineStatus();
  const load = useCallback(
    (signal: AbortSignal) =>
      rawAssetsApi.getAsset(projectId, assetId, { signal }),
    [projectId, assetId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const runAction = useCallback(
    async (action: string, payload: RawAssetActionPayload) => {
      if (!data) {
        return;
      }
      if (!(RAW_ASSET_ACTION_VALUES as readonly string[]).includes(action)) {
        throw new Error(`未知动作：${action}`);
      }
      setBusyAction(action);
      try {
        await rawAssetsApi.applyAction(projectId, assetId, action as RawAssetAction, {
          version: data.version,
          reason: payload.reason,
          quarantineCodes: payload.quarantineCodes,
        });
        reload();
      } finally {
        setBusyAction(null);
      }
    },
    [assetId, data, projectId, reload],
  );
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
        <Button icon={<ArrowLeftOutlined />}>返回 Episode 列表</Button>
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
        title="资产不存在或无权查看"
        description="服务端未找到该原始资产；跨项目资源统一不可见。"
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
        title="正在加载原始资产"
        description="正在读取资产元数据、校验状态与下载条件。"
      />
    );
  } else if (phase === "error") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (!data) {
    body = <PageState variant="empty" title="暂无原始资产数据" />;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功获取的资产数据；状态可能已变化。"
            onRetry={reload}
          />
        ) : null}
        <RawAssetDetailBody
          projectId={projectId}
          asset={data}
          online={online}
          onAction={runAction}
          busyAction={busyAction}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={
        data ? (
          <Space wrap>
            <span>原始资产 {data.code}</span>
            <Tag color={getRawAssetStatus(data.status).color}>
              {getRawAssetStatus(data.status).label}
            </Tag>
          </Space>
        ) : (
          "原始资产详情"
        )
      }
      description="只展示服务端元数据与状态；下载地址按需短期签发，权限与资产可用性以服务端为准。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function RawAssetDetailPage() {
  const projectId = useProjectId();
  const { assetId } = useParams<{ assetId: string }>();
  const navigate = useNavigate();

  if (!projectId) {
    return (
      <PageContainer title="原始资产详情" description="原始资产必须按项目隔离展示。">
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

  if (!assetId) {
    return (
      <PageContainer title="原始资产详情" description="请选择要查看的原始资产。">
        <PageState
          variant="empty"
          title="缺少资产 ID"
          description="URL 中未包含 asset_id。"
          action={
            <Link to={buildEpisodeListPath(projectId)}>
              <Button>返回 Episode 列表</Button>
            </Link>
          }
        />
      </PageContainer>
    );
  }

  return (
    <RawAssetDetailContent
      key={`${projectId}:${assetId}`}
      projectId={projectId}
      assetId={assetId}
    />
  );
}
