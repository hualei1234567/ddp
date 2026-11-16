import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, message } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AssetEditModal from "../../components/collectionAssets/AssetEditModal";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import {
  assetId,
  assetKindLabel,
} from "../../services/collectionAssetPresentation";
import { assetListPath } from "../../services/collectionAssetUrl";
import {
  collectionAssetApi,
  isCollectionAssetKind,
} from "../../services/collectionAssets";
import { resolvePagePhase } from "../../services/pagePhase";
import type { CollectionAssetKind } from "../../types/collectionAsset";
import AssetDetailBody from "./AssetDetailBody";

function AssetDetailContent({
  kind,
  projectId,
  assetIdValue,
}: {
  kind: CollectionAssetKind;
  projectId: string;
  assetIdValue: string;
}) {
  const navigate = useNavigate();
  const [editOpen, setEditOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const label = assetKindLabel(kind);

  const load = useCallback(
    (signal: AbortSignal) =>
      collectionAssetApi.get(kind, projectId, assetIdValue, { signal }),
    [kind, projectId, assetIdValue],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const act = async (action: string, reason: string | null) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await collectionAssetApi.act(kind, projectId, assetId(data), action, {
        version: data.version,
        reason,
      });
      reload();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (data === null) {
      return;
    }
    setDeleting(true);
    try {
      await collectionAssetApi.remove(kind, projectId, assetId(data));
      messageApi.success(`${label}已删除`);
      navigate(assetListPath(kind), { replace: true });
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "删除失败，请稍后重试。",
      );
    } finally {
      setDeleting(false);
    }
  };

  let body;
  if (phase === "loading") {
    body = <PageState variant="loading" title={`正在加载${label}`} />;
  } else if (phase === "forbidden" || phase === "not-found" || phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <AssetDetailBody
          kind={kind}
          asset={data}
          busy={busy}
          deleting={deleting}
          onAction={act}
          onEdit={() => setEditOpen(true)}
          onDelete={() => void remove()}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={`${label}详情`}
      description="状态与可用动作以服务端返回为准；本页不做权威权限判断。"
      extra={
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(assetListPath(kind))}
          >
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {contextHolder}
      {body}
      {data !== null ? (
        <AssetEditModal
          kind={kind}
          projectId={projectId}
          asset={data}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onSaved={reload}
        />
      ) : null}
    </PageContainer>
  );
}

export default function CollectionAssetDetailPage() {
  const { assetType, assetId: routeAssetId } = useParams<{
    assetType: string;
    assetId: string;
  }>();
  const projectId = useProjectId();

  if (!assetType || !isCollectionAssetKind(assetType) || !routeAssetId) {
    return (
      <PageContainer title="采集素材" description="未知的素材或缺失标识。">
        <PageState
          variant="empty"
          title="未知的素材"
          description="请从采集素材列表进入详情页。"
        />
      </PageContainer>
    );
  }

  if (!projectId) {
    return (
      <PageContainer
        title={`${assetKindLabel(assetType)}详情`}
        description="采集素材必须按项目隔离展示。"
      >
        <ProjectScopeRequired scope="素材详情" />
      </PageContainer>
    );
  }

  return (
    <AssetDetailContent
      key={`${projectId}:${assetType}:${routeAssetId}`}
      kind={assetType}
      projectId={projectId}
      assetIdValue={routeAssetId}
    />
  );
}
