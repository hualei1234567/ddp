import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import { qualityRunApi } from "../../services/qualityRuns";
import { presentRunStatus } from "../../services/qualityRunPresentation";
import type {
  QualityRun,
  QualityRunDiffReport,
} from "../../types/qualityRun";
import QualityBatchRerunModal from "./QualityBatchRerunModal";
import QualityRunDiffReportPanel from "./QualityRunDiffReportPanel";
import QualityToneTag from "./QualityToneTag";

/**
 * Episode 详情页的质检面板（FR-026 / FR-027 / FR-028）。
 *
 * 数据全部来自服务端，前端不重新判定：
 * - 运行历史：`GET /quality-runs?subject_type=EPISODE&subject_id=…`，
 *   服务端按创建时间倒序，第一条（最近）作为差异对比的基线；
 * - FR-027：每个非基线行提供「与基线对比」，渲染四类差异分组；
 * - FR-026：单对象重跑入口（batch API，排队回执不是质检结论）；
 * - FR-028：最近一次运行结论为 FAILED 时预选「只重试失败项」。
 */
export default function EpisodeQualityPanel({
  projectId,
  episodeId,
}: {
  projectId: string;
  episodeId: string;
}) {
  const [history, setHistory] = useState<QualityRun[] | null>(null);
  const [historyError, setHistoryError] = useState<ApiError | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyVersion, setHistoryVersion] = useState(0);

  const [rerunModalOpen, setRerunModalOpen] = useState(false);
  const [queuedCount, setQueuedCount] = useState<number | null>(null);

  const [diff, setDiff] = useState<QualityRunDiffReport | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<ApiError | null>(null);
  const [diffCandidate, setDiffCandidate] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError(null);
    qualityRunApi
      .listRuns(projectId, {
        subject_type: "EPISODE",
        subject_id: episodeId,
        signal: controller.signal,
      })
      .then((page) => {
        if (controller.signal.aborted) {
          return;
        }
        setHistory(page.items);
        setHistoryLoading(false);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setHistoryError(
          caught instanceof ApiError
            ? caught
            : new ApiError({
                kind: "network",
                message: "运行历史加载失败，请检查网络或服务状态。",
                cause: caught,
              }),
        );
        setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [projectId, episodeId, historyVersion]);

  const baseline = history?.[0] ?? null;

  const compare = useCallback(
    async (candidateRunId: string) => {
      if (baseline === null) {
        return;
      }
      setDiffCandidate(candidateRunId);
      setDiffLoading(true);
      setDiffError(null);
      try {
        const report = await qualityRunApi.getRunDiff(
          projectId,
          baseline.run_id,
          candidateRunId,
        );
        setDiff(report);
      } catch (caught) {
        setDiff(null);
        setDiffError(
          caught instanceof ApiError
            ? caught
            : new ApiError({
                kind: "network",
                message: "差异报告加载失败。",
                cause: caught,
              }),
        );
      } finally {
        setDiffLoading(false);
      }
    },
    [baseline, projectId],
  );

  const rerun = useCallback(
    async (subjectIds: string[]) => {
      const queued = await qualityRunApi.batchCreateRuns(projectId, {
        subject_type: "EPISODE",
        subject_ids: subjectIds,
      });
      setQueuedCount(queued.length);
      return queued;
    },
    [projectId],
  );

  const columns: TableColumnsType<QualityRun> = [
    {
      title: "运行 ID",
      dataIndex: "run_id",
      width: 300,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "整体结论",
      dataIndex: "status",
      width: 200,
      render: (value: QualityRun["status"]) => (
        <QualityToneTag presentation={presentRunStatus(value)} />
      ),
    },
    {
      title: "阻断 / 提醒",
      key: "counts",
      width: 130,
      render: (_value, item) => `${item.error_count} / ${item.warn_count}`,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 170,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_value, item) =>
        baseline !== null && item.run_id !== baseline.run_id ? (
          <Button
            size="small"
            onClick={() => void compare(item.run_id)}
            loading={diffLoading && diffCandidate === item.run_id}
          >
            与基线对比
          </Button>
        ) : baseline !== null ? (
          <Tag>基线</Tag>
        ) : null,
    },
  ];

  const rerunSubjects = useMemoSubjects(baseline, episodeId);

  let body: React.ReactNode;
  if (historyLoading) {
    body = <Card size="small" loading />;
  } else if (historyError !== null) {
    body = (
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Alert
          type="error"
          showIcon
          message="运行历史加载失败"
          description={historyError.message}
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => setHistoryVersion((version) => version + 1)}
        >
          重试
        </Button>
      </Space>
    );
  } else if (history === null || history.length === 0) {
    body = (
      <Alert
        type="info"
        showIcon
        message="尚未质检"
        description="该 Episode 还没有质量运行记录；可在列表页选择后批量重跑，或等待提交后自动质检。"
      />
    );
  } else {
    body = (
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {queuedCount !== null ? (
          <Alert
            type="warning"
            showIcon
            message={`已入队 ${queuedCount} 条质检（排队回执不是质检结论）`}
            description="由质检 Worker 领取执行；完成后运行历史会出现新的运行记录。"
          />
        ) : null}
        <Space wrap>
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={() => {
              setQueuedCount(null);
              setRerunModalOpen(true);
            }}
          >
            重新质检
          </Button>
          <Typography.Text type="secondary">
            最近一次运行：{baseline?.status ?? "无"}；整体结论由服务端判定。
          </Typography.Text>
        </Space>
        <Table<QualityRun>
          rowKey="run_id"
          size="small"
          columns={columns}
          dataSource={history}
          pagination={false}
          scroll={{ x: 940 }}
        />
        {diff !== null || diffLoading || diffError !== null ? (
          <QualityRunDiffReportPanel
            report={diff}
            loading={diffLoading}
            error={diffError}
            hasBaseline={baseline !== null}
            onCompare={(candidateRunId) => void compare(candidateRunId)}
            onRetry={() =>
              diffCandidate !== null ? void compare(diffCandidate) : undefined
            }
          />
        ) : null}
      </Space>
    );
  }

  return (
    <Card title="质检运行（Episode）">
      {body}
      {rerunModalOpen ? (
        <QualityBatchRerunModal
          open
          subjects={rerunSubjects}
          scopeLabel="当前 Episode"
          rerun={rerun}
          onClose={() => setRerunModalOpen(false)}
          defaultOnlyFailed={baseline?.status === "FAILED"}
        />
      ) : null}
    </Card>
  );
}

/** FR-028：详情页单对象场景——重试范围就是本 Episode（含最近结论）。 */
function useMemoSubjects(
  baseline: QualityRun | null,
  episodeId: string,
): QualityRunSubjects {
  return [{ subjectId: episodeId, lastStatus: baseline?.status ?? null }];
}

type QualityRunSubjects = {
  subjectId: string;
  lastStatus: QualityRun["status"] | null;
}[];
