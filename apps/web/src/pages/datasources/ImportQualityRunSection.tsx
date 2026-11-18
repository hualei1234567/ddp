import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import QualityRunReport from "../../components/quality/QualityRunReport";
import type { QualityFindingsState } from "../../components/quality/QualityRunReport";
import { useCursorPage } from "../../hooks/useCursorPage";
import { useRequest } from "../../hooks/useRequest";
import { ApiError } from "../../services/api";
import { resolvePagePhase } from "../../services/pagePhase";
import { qualityRunApi } from "../../services/qualityRuns";
import type {
  QualityRuleCategory,
  QualityRun,
  QualityRunFinding,
  QualitySeverity,
} from "../../types/qualityRun";

/** URL 上的运行 ID：支持刷新/分享后回看同一次运行。 */
export const QUALITY_RUN_ID_PARAM = "quality_run_id";

const EMPTY_FINDINGS: QualityFindingsState = {
  items: [],
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
};

function toApiError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({
        kind: "network",
        message: "质量检查请求失败，请检查网络或服务状态。",
        cause: caught,
      });
}

function buildAssetsHref(importId: string): string {
  return `/data-source-imports/${encodeURIComponent(importId)}/assets`;
}

function readRunId(searchParams: URLSearchParams): string | null {
  const value = searchParams.get(QUALITY_RUN_ID_PARAM)?.trim();
  return value ? value : null;
}

/**
 * 结论明细的分页状态。
 *
 * 类别筛选由服务端执行（契约支持 `category`）；切换类别时重新从第一页拉取，
 * 而不是在已加载条目上做二次筛选——否则会把"只检查了已加载部分"说成结论。
 */
function useQualityFindings(
  projectId: string,
  runId: string,
  category: QualityRuleCategory | null,
): {
  state: QualityFindingsState;
  reload: () => void;
  loadMore: () => void;
} {
  const {
    items,
    loading,
    refreshing,
    loadingMore,
    hasMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } =
    useCursorPage<QualityRunFinding>(
      (options) =>
        qualityRunApi.listFindings(projectId, runId, {
          category,
          cursor: options.cursor,
          signal: options.signal,
        }),
      (finding) => finding.finding_id,
    );

  return {
    state: {
      items,
      loading,
      refreshing,
      loadingMore,
      hasMore,
      error,
      loadMoreError,
      stale,
    },
    reload,
    loadMore,
  };
}

function LoadedQualityRun({
  projectId,
  importId,
  runId,
  running,
  runError,
  category,
  severity,
  onCategoryChange,
  onSeverityChange,
  onRun,
}: {
  projectId: string;
  importId: string;
  runId: string;
  running: boolean;
  runError: ApiError | null;
  category: QualityRuleCategory | null;
  severity: QualitySeverity | null;
  onCategoryChange: (value: QualityRuleCategory | null) => void;
  onSeverityChange: (value: QualitySeverity | null) => void;
  onRun: () => void;
}) {
  const loadRun = useCallback(
    (signal: AbortSignal) =>
      qualityRunApi.getRun(projectId, runId, { signal }),
    [projectId, runId],
  );
  const runState = useRequest<QualityRun>(loadRun);
  const findings = useQualityFindings(projectId, runId, category);

  const phase = resolvePagePhase({
    hasData: runState.data !== null,
    loading: runState.loading,
    error: runState.error,
  });

  const run = runState.data;
  let disabledReason: string | null = null;
  if (run !== null && run.subject_type !== "DATASOURCE_IMPORT") {
    disabledReason =
      "该运行不是导入级检查，无法在本页下钻导入媒体资产；请回到对应对象页面查看。";
  } else if (run !== null && run.subject_id !== importId) {
    disabledReason =
      "该运行属于其他导入对象，本页仅按运行 ID 回看其结论，请勿当作当前导入的质检结论。";
  }

  return (
    <QualityRunReport
      phase={phase}
      run={runState.data}
      refreshing={runState.refreshing}
      stale={runState.stale}
      error={runState.error}
      running={running}
      runError={runError}
      findings={findings.state}
      category={category}
      severity={severity}
      onCategoryChange={onCategoryChange}
      onSeverityChange={onSeverityChange}
      onReload={runState.reload}
      onRun={onRun}
      onLoadMoreFindings={findings.loadMore}
      assetsHref={buildAssetsHref(importId)}
      disabledReason={disabledReason}
    />
  );
}

/**
 * 导入页的统一质量运行区块（容器）。
 *
 * - 页面通过 `?quality_run_id=` 回看指定运行（刷新/分享不丢失）；
 * - 没有运行 ID 时按导入对象查询最近一次运行并自动填入 URL；
 *   查询为空才显示"尚未质检"，由用户显式触发一次 POST；
 * - 触发成功后把新运行 ID 写回 URL，不覆盖任何历史记录。
 */
export default function ImportQualityRunSection({
  projectId,
  importId,
}: {
  projectId: string;
  importId: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = readRunId(searchParams);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<ApiError | null>(null);
  const [category, setCategory] = useState<QualityRuleCategory | null>(null);
  const [severity, setSeverity] = useState<QualitySeverity | null>(null);

  // 没有 URL 运行 ID 时自动发现该导入最近一次运行。
  // 用 `listRuns` 而不是猜测 ID：服务端按创建时间倒序返回，第一页首条即最近运行。
  const latest = useRequest<QualityRun | null>(
    useCallback(
      async (signal) => {
        if (runId !== null) {
          return null;
        }
        const page = await qualityRunApi.listRuns(projectId, {
          subject_type: "DATASOURCE_IMPORT",
          subject_id: importId,
          limit: 1,
          signal,
        });
        return page.items[0] ?? null;
      },
      [importId, projectId, runId],
    ),
  );

  useEffect(() => {
    const discovered = latest.data;
    if (runId !== null || discovered === null) {
      return;
    }
    const next = new URLSearchParams(searchParams);
    next.set(QUALITY_RUN_ID_PARAM, discovered.run_id);
    setSearchParams(next, { replace: true });
  }, [latest.data, runId, searchParams, setSearchParams]);

  const trigger = useCallback(async () => {
    setRunning(true);
    setRunError(null);
    try {
      const run = await qualityRunApi.createRun(projectId, {
        subject_type: "DATASOURCE_IMPORT",
        subject_id: importId,
      });
      const next = new URLSearchParams(searchParams);
      next.set(QUALITY_RUN_ID_PARAM, run.run_id);
      setSearchParams(next, { replace: true });
    } catch (caught) {
      setRunError(toApiError(caught));
    } finally {
      setRunning(false);
    }
  }, [importId, projectId, searchParams, setSearchParams]);

  if (runId === null) {
    // 发现期间不要先显示"尚未质检"：那会在请求返回前给出没有依据的结论。
    const discovering = latest.loading || latest.data !== null;
    return (
      <QualityRunReport
        phase={discovering ? "loading" : latest.error ? "error" : "ready"}
        run={null}
        refreshing={false}
        stale={false}
        error={latest.error}
        running={running}
        runError={runError}
        findings={EMPTY_FINDINGS}
        category={category}
        severity={severity}
        onCategoryChange={setCategory}
        onSeverityChange={setSeverity}
        onReload={latest.reload}
        onRun={() => void trigger()}
        onLoadMoreFindings={() => undefined}
        assetsHref={buildAssetsHref(importId)}
      />
    );
  }

  return (
    <LoadedQualityRun
      // 类别筛选由服务端执行；按类别重挂载可清空旧筛选下的已加载条目，
      // 避免刷新失败时把上一类别的结论显示成新筛选结果。
      key={`${runId}:${category ?? "all"}`}
      projectId={projectId}
      importId={importId}
      runId={runId}
      running={running}
      runError={runError}
      category={category}
      severity={severity}
      onCategoryChange={setCategory}
      onSeverityChange={setSeverity}
      onRun={() => void trigger()}
    />
  );
}
