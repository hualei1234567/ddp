import { useCallback, useMemo, useState } from "react";

import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import {
  filterUsageRows,
  mergeQuotaVersions,
} from "../../services/analyticsPresentation";
import { governanceApi } from "../../services/governance";
import { resolvePagePhase } from "../../services/pagePhase";
import type { QuotaStatus, ResourceKind } from "../../types/governance";
import QuotaEditModal from "./QuotaEditModal";
import UsageQuotaTable from "./UsageQuotaTable";

/**
 * 经营分析数据装配。
 *
 * 两个独立数据源：
 * - `GET /usage-report`：用量 / 上限 / 剩余 / 状态 / 统计截止（展示口径权威）；
 * - `GET /quotas`：已配置上限及其 `version`，修改时作为 `If-Match`。
 *
 * 二者分别有独立的 Loading / Error 状态：报表失败时页面是 Error/Stale，
 * 配额配置失败时报表照常展示，只在「修改上限」能力上降级为 Partial。
 */
function AnalyticsContent({ projectId }: { projectId: string }) {
  const online = useOnlineStatus();
  const [statusFilter, setStatusFilter] = useState<QuotaStatus | undefined>();
  const [editingKind, setEditingKind] = useState<ResourceKind | null>(null);

  const loadReport = useCallback(
    (signal: AbortSignal) => governanceApi.getUsageReport(projectId, { signal }),
    [projectId],
  );
  const loadQuotas = useCallback(
    (signal: AbortSignal) => governanceApi.listQuotas(projectId, { signal }),
    [projectId],
  );

  const report = useRequest(loadReport);
  const quotas = useRequest(loadQuotas);

  const allRows = useMemo(
    () =>
      report.data === null
        ? []
        : mergeQuotaVersions(report.data.entries, quotas.data ?? []),
    [report.data, quotas.data],
  );
  const rows = useMemo(
    // 契约没有为报表声明过滤参数，状态筛选在已取回的 5 条上本地完成。
    () => filterUsageRows(allRows, statusFilter),
    [allRows, statusFilter],
  );

  const phase = resolvePagePhase({
    hasData: report.data !== null,
    loading: report.loading,
    error: report.error,
  });

  const reloadReport = report.reload;
  const reloadQuotas = quotas.reload;
  const refreshAll = useCallback(() => {
    reloadReport();
    reloadQuotas();
  }, [reloadReport, reloadQuotas]);

  const editingRow =
    allRows.find((row) => row.entry.resource_kind === editingKind) ?? null;

  /**
   * 只有「从未取到配额配置」才降级为 Partial 并禁用修改：缺少版本号时
   * 修改既有配额必然 428/409。若只是刷新失败但手里还有上次成功的版本，
   * 仍允许尝试修改，由服务端的 If-Match 裁决（409 会提示刷新重试）。
   */
  const quotaConfigError = quotas.data === null ? quotas.error : null;

  return (
    <>
      <UsageQuotaTable
        phase={phase}
        rows={rows}
        totalRows={allRows.length}
        statusFilter={statusFilter}
        generatedAt={report.data?.generated_at ?? null}
        refreshing={report.refreshing || quotas.refreshing}
        stale={report.stale || quotas.stale}
        error={report.error}
        quotaConfigError={quotaConfigError}
        online={online}
        onStatusFilterChange={setStatusFilter}
        onRefresh={refreshAll}
        onRetryQuotaConfig={quotas.reload}
        onEdit={(row) => setEditingKind(row.entry.resource_kind)}
      />
      <QuotaEditModal
        projectId={projectId}
        kind={editingKind}
        row={editingRow}
        online={online}
        onClose={() => setEditingKind(null)}
        onRefreshAfterWrite={refreshAll}
      />
    </>
  );
}

/**
 * 经营分析页（平台治理分区）。
 *
 * 三态口径是本页的核心：`used = null` 显示「未测量」而不是 0，
 * `limit = null` 显示「未配置上限」而不是 0，`UNMEASURED` 不与 `OK` 合并。
 */
export default function AnalyticsPage() {
  const projectId = useProjectId();

  if (!projectId) {
    return (
      <PageContainer title="经营分析" description="用量与配额必须按项目隔离展示。">
        <ProjectScopeRequired scope="经营分析" />
      </PageContainer>
    );
  }

  return <AnalyticsContent key={projectId} projectId={projectId} />;
}
