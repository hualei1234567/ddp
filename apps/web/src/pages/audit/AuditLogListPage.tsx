import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { auditLogApi } from "../../services/auditLogs";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  AUDIT_RESULT_VALUES,
  type AuditLog,
  type AuditLogListFilters,
} from "../../types/auditLog";
import AuditLogListBody from "./AuditLogListBody";

/** 过滤条件与 URL 参数的映射；时间区间在 URL 里用更短的 from / to。 */
const FILTER_QUERY_KEYS: Record<keyof AuditLogListFilters, string> = {
  resource_type: "resource_type",
  resource_id: "resource_id",
  action: "action",
  actor_user_id: "actor_user_id",
  result: "result",
  occurred_from: "from",
  occurred_to: "to",
};

function readFilters(params: URLSearchParams): AuditLogListFilters {
  const rawResult = (params.get("result") ?? "").trim();
  return {
    resource_type: params.get("resource_type")?.trim() || undefined,
    resource_id: params.get("resource_id")?.trim() || undefined,
    action: params.get("action")?.trim() || undefined,
    actor_user_id: params.get("actor_user_id")?.trim() || undefined,
    // 非法结果值直接忽略，不把未知值发给服务端（服务端会 422）。
    result: AUDIT_RESULT_VALUES.find((value) => value === rawResult),
    occurred_from: params.get("from") ?? undefined,
    occurred_to: params.get("to") ?? undefined,
  };
}

function serializeFilters(filters: AuditLogListFilters): string {
  return Object.entries(FILTER_QUERY_KEYS)
    .map(([key, queryKey]) => `${queryKey}=${filters[key as keyof AuditLogListFilters] ?? ""}`)
    .join("&");
}

function AuditLogListContent({
  projectId,
  filters,
  onFiltersChange,
  onClearFilters,
}: {
  projectId: string;
  filters: AuditLogListFilters;
  onFiltersChange: (patch: Partial<AuditLogListFilters>) => void;
  onClearFilters: () => void;
}) {
  const [selected, setSelected] = useState<AuditLog | null>(null);

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      auditLogApi.list(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        filters,
      }),
    // filters 由调用方按序列化后的 key 重新挂载，这里只需依赖项目与条件对象。
    [projectId, filters],
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
  } = useCursorPage(load, (item) => item.audit_id);

  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  return (
    <AuditLogListBody
      filters={filters}
      phase={phase}
      items={items}
      loading={loading}
      refreshing={refreshing}
      loadingMore={loadingMore}
      hasMore={hasMore}
      error={error}
      loadMoreError={loadMoreError}
      stale={stale}
      selected={selected}
      onFiltersChange={onFiltersChange}
      onClearFilters={onClearFilters}
      onReload={reload}
      onLoadMore={loadMore}
      onSelect={setSelected}
      onFocusResource={(item) =>
        onFiltersChange({
          resource_type: item.resource_type,
          resource_id: item.resource_id,
        })
      }
    />
  );
}

/**
 * 审计日志页（平台治理分区）。
 *
 * 过滤条件全部写进 URL：任何一次追溯都能复制链接给同事或写进事故报告；
 * 只有服务端判定权限（项目负责人 / 平台管理员），前端不做权威校验。
 */
export default function AuditLogListPage() {
  const projectId = useProjectId();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = readFilters(searchParams);

  const handleFiltersChange = (patch: Partial<AuditLogListFilters>) => {
    const params = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      const queryKey = FILTER_QUERY_KEYS[key as keyof AuditLogListFilters];
      if (value === undefined || value === "") {
        params.delete(queryKey);
      } else {
        params.set(queryKey, String(value));
      }
    }
    setSearchParams(params, { replace: true });
  };

  const handleClearFilters = () => {
    const params = new URLSearchParams(searchParams);
    for (const queryKey of Object.values(FILTER_QUERY_KEYS)) {
      params.delete(queryKey);
    }
    setSearchParams(params, { replace: true });
  };

  if (!projectId) {
    return (
      <PageContainer title="审计日志" description="审计日志必须按项目隔离展示。">
        <ProjectScopeRequired scope="审计日志" />
      </PageContainer>
    );
  }

  return (
    <AuditLogListContent
      key={`${projectId}:${serializeFilters(filters)}`}
      projectId={projectId}
      filters={filters}
      onFiltersChange={handleFiltersChange}
      onClearFilters={handleClearFilters}
    />
  );
}
