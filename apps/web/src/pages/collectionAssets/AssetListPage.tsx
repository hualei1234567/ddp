import { useCallback, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import AssetCreateModal from "../../components/collectionAssets/AssetCreateModal";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import {
  assetId,
  assetKindLabel,
} from "../../services/collectionAssetPresentation";
import { readAssetStatusFilter } from "../../services/collectionAssetUrl";
import {
  collectionAssetApi,
  isCollectionAssetKind,
} from "../../services/collectionAssets";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  CollectionAsset,
  CollectionAssetKind,
  CollectionAssetStatus,
} from "../../types/collectionAsset";
import AssetListBody from "./AssetListBody";

function AssetListContent({
  kind,
  projectId,
  status,
}: {
  kind: CollectionAssetKind;
  projectId: string;
  status: CollectionAssetStatus | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [busyAssetId, setBusyAssetId] = useState<string | null>(null);

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      collectionAssetApi.list(kind, projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [kind, projectId, status],
  );

  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } = useCursorPage(load, assetId);

  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const handleStatusChange = (value: CollectionAssetStatus | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("status", value);
    } else {
      params.delete("status");
    }
    setSearchParams(params, { replace: true });
  };

  const handleAction = async (
    asset: CollectionAsset,
    action: string,
    reason: string | null,
  ) => {
    setBusyAssetId(assetId(asset));
    try {
      await collectionAssetApi.act(kind, projectId, assetId(asset), action, {
        version: asset.version,
        reason,
      });
      reload();
    } finally {
      setBusyAssetId(null);
    }
  };

  return (
    <AssetListBody
      kind={kind}
      status={status}
      phase={phase}
      items={items}
      loading={loading}
      refreshing={refreshing}
      loadingMore={loadingMore}
      hasMore={hasMore}
      error={error}
      loadMoreError={loadMoreError}
      stale={stale}
      busyAssetId={busyAssetId}
      onStatusChange={handleStatusChange}
      onReload={reload}
      onLoadMore={loadMore}
      onCreate={() => setCreateOpen(true)}
      onAction={handleAction}
      createModal={
        <AssetCreateModal
          kind={kind}
          projectId={projectId}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreated={reload}
        />
      }
    />
  );
}

export default function CollectionAssetListPage() {
  const { assetType } = useParams<{ assetType: string }>();
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  const status = readAssetStatusFilter(searchParams);

  if (!assetType || !isCollectionAssetKind(assetType)) {
    return (
      <PageContainer title="采集素材" description="未知的素材类型。">
        <PageState
          variant="empty"
          title="未知的素材类型"
          description="可用类型：采集方案（plans）、SOP（sops）、场景（scenarios）、动作库（action-specs）。"
        />
      </PageContainer>
    );
  }

  if (!projectId) {
    return (
      <PageContainer
        title={`采集素材 · ${assetKindLabel(assetType)}`}
        description="采集素材必须按项目隔离展示。"
      >
        <ProjectScopeRequired scope="采集素材列表" />
      </PageContainer>
    );
  }

  return (
    <AssetListContent
      key={`${projectId}:${assetType}:${status ?? ""}`}
      kind={assetType}
      projectId={projectId}
      status={status}
    />
  );
}
