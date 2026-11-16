import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import LocationCreateModal from "../../components/locations/LocationCreateModal";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { locationApi } from "../../services/locations";
import { resolvePagePhase } from "../../services/pagePhase";
import { LOCATION_KIND_VALUES, type LocationKind } from "../../types/location";
import LocationListBody from "./LocationListBody";

/** URL 中的未知类型直接忽略，不把非法值发给服务端（服务端会 422）。 */
function readKindFilter(params: URLSearchParams): LocationKind | undefined {
  const value = (params.get("kind") ?? "").trim();
  return LOCATION_KIND_VALUES.find((item) => item === value);
}

function LocationListContent({
  projectId,
  kind,
}: {
  projectId: string;
  kind: LocationKind | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      locationApi.list(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        kind,
      }),
    [projectId, kind],
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
  } = useCursorPage(load, (item) => item.location_id);

  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const handleKindChange = (value: LocationKind | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("kind", value);
    } else {
      params.delete("kind");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <LocationListBody
      kind={kind}
      phase={phase}
      items={items}
      loading={loading}
      refreshing={refreshing}
      loadingMore={loadingMore}
      hasMore={hasMore}
      error={error}
      loadMoreError={loadMoreError}
      stale={stale}
      onKindChange={handleKindChange}
      onReload={reload}
      onLoadMore={loadMore}
      onCreate={() => setCreateOpen(true)}
      createModal={
        <LocationCreateModal
          projectId={projectId}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreated={reload}
        />
      }
    />
  );
}

export default function LocationListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  const kind = readKindFilter(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="场地管理" description="场地必须按项目隔离展示。">
        <ProjectScopeRequired scope="场地列表" />
      </PageContainer>
    );
  }

  return (
    <LocationListContent
      key={`${projectId}:${kind ?? ""}`}
      projectId={projectId}
      kind={kind}
    />
  );
}
