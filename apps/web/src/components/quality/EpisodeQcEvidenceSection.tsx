/**
 * Episode 详情页的 QC 证据定位区（V51-010 / FR-025）。
 *
 * ## 复用，不重做
 *
 * 播放器与时间轴复用既有的 `EpisodeMediaPanel` → `AnnotationTimeline` /
 * `MediaPreview`（蓝图 §1：不重做 Workbench 播放器）。本组件只做三件事：
 *
 * 1. 拉取指定质量运行的结论明细（`GET /quality-runs/{id}/findings`）；
 * 2. 把选中的结论换算成**相对秒**，驱动既有播放器跳转 + 时间轴高亮；
 * 3. 把 `require-rework` 的入口接上：只预填原因，仍走既有动作。
 *
 * ## 为什么不自动选中第一条
 *
 * 自动定位会让页面"一进来就跳到某个时刻"，用户无法判断这是自己点的还是
 * 页面猜的。默认不选中，首屏只列"哪些结论可定位"；点击才动播放器。
 *
 * ## URL 参数
 *
 * `?qc_run_id=<uuid>` 用于从其他页面（质量运行详情、导入质检）深链过来。
 * 只读，不做写回：定位状态是页面局部状态，把它写进 URL 会让刷新"复现上次
 * 点击"，而那条结论可能在新一次运行里已经不存在。
 */

import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Space, Typography } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import EpisodeMediaPanel from "../annotation/EpisodeMediaPanel";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import { ApiError } from "../../services/api";
import {
  buildEvidenceJumpTarget,
  type ReworkContext,
} from "../../services/qualityEvidence";
import { qualityRunApi } from "../../services/qualityRuns";
import { useRequest } from "../../hooks/useRequest";
import type { Episode } from "../../types/collection";
import type {
  QualityRun,
  QualityRunFinding,
} from "../../types/qualityRun";
import QualityEvidencePanel from "./QualityEvidencePanel";

const FINDINGS_PAGE_LIMIT = 100;

export interface EpisodeQcEvidenceSectionProps {
  projectId: string;
  episode: Episode;
  /** 要展示证据的质量运行；通常来自 URL `qc_run_id` 或最近一次运行。 */
  run: QualityRun;
  /**
   * 用 QC 证据发起返工。父级负责调用**既有** require-rework 入口
   * （提交上下文由父级提供）。未提供时面板如实置灰。
   */
  onCreateRework?: (context: ReworkContext) => void;
  reworkBusy?: boolean;
  reworkUnavailableReason?: string | null;
}

/** 结论明细分页上限有限：多页时如实标注，不做静默截断。 */
interface FindingsBundle {
  items: QualityRunFinding[];
  truncated: boolean;
}

export default function EpisodeQcEvidenceSection({
  projectId,
  episode,
  run,
  onCreateRework,
  reworkBusy = false,
  reworkUnavailableReason = null,
}: EpisodeQcEvidenceSectionProps) {
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(
    null,
  );
  const [playheadSeconds, setPlayheadSeconds] = useState(0);

  const load = useCallback(
    async (signal: AbortSignal): Promise<FindingsBundle> => {
      const page = await qualityRunApi.listFindings(projectId, run.run_id, {
        limit: FINDINGS_PAGE_LIMIT,
        signal,
      });
      return { items: page.items, truncated: page.next_cursor !== null };
    },
    [projectId, run.run_id],
  );
  const { data, error, loading, stale, reload } = useRequest(load);

  const selected = useMemo(
    () =>
      data?.items.find((finding) => finding.finding_id === selectedFindingId) ??
      null,
    [data, selectedFindingId],
  );

  const target = useMemo(
    () =>
      selected === null
        ? null
        : buildEvidenceJumpTarget(
            selected.evidence_location,
            episode.start_time_ns,
            episode.end_time_ns,
          ),
    [episode.end_time_ns, episode.start_time_ns, selected],
  );

  // 选中结论后把播放器移到证据起点。依赖只放原始值：target 每次渲染都是新对象。
  const seekSeconds = target?.seekSeconds ?? null;
  useEffect(() => {
    if (seekSeconds === null) {
      return;
    }
    setPlayheadSeconds(Math.max(0, seekSeconds));
  }, [seekSeconds]);

  const errorState =
    error instanceof ApiError
      ? error
      : error === null
        ? null
        : new ApiError({
            kind: "network",
            message: "质量结论明细加载失败。",
            cause: error,
          });

  let body: React.ReactNode;
  if (loading) {
    body = (
      <PageState
        compact
        variant="loading"
        title="正在读取质量结论"
        description="读取该运行的全部结论与证据定位。"
      />
    );
  } else if (errorState !== null) {
    body = <ApiErrorState compact error={errorState} onRetry={reload} />;
  } else if (data === null) {
    body = (
      <PageState compact variant="empty" title="尚未读到结论明细" />
    );
  } else {
    body = (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示上次成功读取的结论。"
            onRetry={reload}
          />
        ) : null}
        {data.truncated ? (
          <PageState
            compact
            variant="partial"
            title="结论明细未完整加载"
            description={`本页最多展示 ${FINDINGS_PAGE_LIMIT} 条；更早的结论未拉取，未通过项可能不全。`}
            onRetry={reload}
          />
        ) : null}

        <EpisodeMediaPanel
          projectId={projectId}
          episode={episode}
          playheadSeconds={playheadSeconds}
          onSeek={setPlayheadSeconds}
          segments={[]}
          canSelectRange={false}
          evidenceRange={target?.highlight ?? null}
          evidenceStreamId={target?.streamId ?? null}
          evidenceSummary={
            target !== null && target.locatable ? (
              <Alert
                type="warning"
                showIcon
                message="正在展示 QC 证据位置"
                description={`规则 ${selected?.rule_id ?? ""}@${selected?.rule_version ?? ""}：${
                  target.streamId === null
                    ? "无流维度"
                    : `媒体流 ${target.streamId}`
                }${
                  target.highlight === null
                    ? "；无时间维度（播放器不跳转）"
                    : "；时间轴已高亮证据区间"
                }`}
              />
            ) : null
          }
        />

        <QualityEvidencePanel
          run={run}
          findings={data.items}
          episodeStartNs={episode.start_time_ns}
          episodeEndNs={episode.end_time_ns}
          selectedFindingId={selectedFindingId}
          onSelectFinding={setSelectedFindingId}
          onCreateRework={onCreateRework}
          reworkUnavailableReason={reworkUnavailableReason}
          reworkBusy={reworkBusy}
        />
      </Space>
    );
  }

  return (
    <Card
      title="QC 证据定位（时间轴 / 流 / 字段）"
      extra={
        <Space wrap>
          <Typography.Text type="secondary">
            运行 {run.run_id.slice(0, 8)}…
          </Typography.Text>
          <Button
            icon={<ReloadOutlined />}
            size="small"
            loading={loading}
            onClick={reload}
          >
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </Card>
  );
}
