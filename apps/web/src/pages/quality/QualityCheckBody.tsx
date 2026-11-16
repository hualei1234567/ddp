import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useMemo } from "react";

import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import QualityFindingTable from "../../components/quality/QualityFindingTable";
import type { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  isWholeRunInconclusive,
  presentCheckStatus,
  summarizeFindings,
} from "../../services/qualityPresentation";
import type { QualityCheckRun } from "../../types/quality";

export interface QualityCheckBodyProps {
  submissionId: string;
  phase: PagePhase;
  runs: QualityCheckRun[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  stale: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  rerunning: boolean;
  rerunError: ApiError | null;
  onReload: () => void;
  onLoadMore: () => void;
  onRerun: () => void;
}

const STATUS_TONE: Record<"error" | "neutral", string | undefined> = {
  neutral: "default",
  error: "red",
};

function runColumns(): TableColumnsType<QualityCheckRun> {
  return [
    {
      title: "检查记录",
      dataIndex: "run_id",
      width: 220,
      render: (value: string) => (
        <IdentifierValue value={value} label="质量检查 run ID" />
      ),
    },
    {
      title: "整体结论",
      dataIndex: "status",
      width: 190,
      render: (value: QualityCheckRun["status"]) => {
        const presentation = presentCheckStatus(value);
        return (
          <Space direction="vertical" size={0}>
            <Tag color={STATUS_TONE[presentation.tone === "error" ? "error" : "neutral"]}>
              {presentation.label}
            </Tag>
            <Typography.Text type="secondary">status={value}</Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "违规计数（ERROR / WARN）",
      key: "counts",
      width: 200,
      render: (_value, run) =>
        `${run.error_count} / ${run.warn_count}`,
    },
    {
      title: "无结论",
      key: "inconclusive",
      width: 100,
      render: (_value, run) =>
        summarizeFindings(run.findings).inconclusiveCount,
    },
    {
      title: "规则集版本",
      dataIndex: "rule_set_version",
      width: 220,
      render: (value: string) => (
        <Typography.Text code ellipsis style={{ maxWidth: 210 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      width: 180,
      render: (value: string) => formatDateTime(value),
    },
  ];
}

/**
 * 提交质量检查的展示层（不发请求，便于静态渲染测试）。
 *
 * 三个必须如实呈现的事实：
 * 1. 每次检查是一条只追加记录，历史不会被后续检查覆盖；
 * 2. 整体 `status` 只由 ERROR 级违规数决定，`PASSED` 不等于"所有规则都通过"；
 * 3. 空历史 ≠ 合格：提交时的自动检查可能失败或未执行。
 */
export default function QualityCheckBody({
  submissionId,
  phase,
  runs,
  selectedRunId,
  onSelectRun,
  loading,
  refreshing,
  loadingMore,
  hasMore,
  stale,
  error,
  loadMoreError,
  rerunning,
  rerunError,
  onReload,
  onLoadMore,
  onRerun,
}: QualityCheckBodyProps) {
  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  );

  const rerunButton = (
    <Button
      type="primary"
      icon={<SyncOutlined />}
      loading={rerunning}
      onClick={onRerun}
    >
      重新执行检查
    </Button>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={onReload} /> : null;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载质量检查"
        description="读取该提交的检查历史与逐条规则结论。"
      />
    );
  } else if (phase === "error") {
    body = error ? <ApiErrorState error={error} onRetry={onReload} /> : null;
  } else if (runs.length === 0) {
    body = (
      <PageState
        variant="empty"
        title="该提交没有质量检查记录"
        description="提交时会自动执行一次质量检查；没有记录通常意味着自动检查未执行或执行失败。空记录不代表合格，可手动重跑一次。"
        action={rerunButton}
      />
    );
  } else {
    const summary = selectedRun ? summarizeFindings(selectedRun.findings) : null;
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的检查历史。"
            onRetry={onReload}
          />
        ) : null}
        {rerunError ? (
          <ApiErrorState error={rerunError} compact onRetry={onRerun} />
        ) : null}
        <PageState
          compact
          variant="partial"
          title="结论口径"
          description="整体结论只由 ERROR 级违规数决定：有任何 ERROR 级违规即 FAILED，否则 PASSED。"
        />
        <Table<QualityCheckRun>
          rowKey="run_id"
          size="small"
          loading={loading}
          columns={runColumns()}
          dataSource={runs}
          pagination={false}
          scroll={{ x: 1100 }}
          rowClassName={(run) =>
            run.run_id === selectedRun?.run_id ? "ddp-row-selected" : ""
          }
          onRow={(run) => ({
            onClick: () => onSelectRun(run.run_id),
            style: { cursor: "pointer" },
          })}
          locale={{ emptyText: "暂无检查记录" }}
        />
        {hasMore ? (
          <Button block loading={loadingMore} onClick={onLoadMore}>
            加载更早的检查记录
          </Button>
        ) : null}
        {loadMoreError ? (
          <PageState
            compact
            variant="partial"
            description={`加载更多失败：${loadMoreError.message}；已展示的检查记录仍然有效。`}
            onRetry={onLoadMore}
          />
        ) : null}
        {selectedRun && summary ? (
          <Space direction="vertical" size={12} className="ddp-stack">
            <Typography.Title level={5} style={{ margin: 0 }}>
              检查详情
            </Typography.Title>
            {isWholeRunInconclusive(summary) ? (
              <PageState
                compact
                variant="partial"
                title="本次检查没有任何规则给出结论"
                description={`${summary.total} 条规则全部为「无结论」。整体结论 PASSED 只表示没有 ERROR 级违规，不代表标注质量合格。`}
              />
            ) : null}
            <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
              <Descriptions.Item label="run ID">
                <IdentifierValue value={selectedRun.run_id} label="质量检查 run ID" />
              </Descriptions.Item>
              <Descriptions.Item label="提交">
                <IdentifierValue
                  value={selectedRun.submission_id}
                  label="提交 ID"
                />
              </Descriptions.Item>
              <Descriptions.Item label="整体结论">
                <Tag
                  color={
                    STATUS_TONE[
                      presentCheckStatus(selectedRun.status).tone === "error"
                        ? "error"
                        : "neutral"
                    ]
                  }
                >
                  {presentCheckStatus(selectedRun.status).label}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="违规计数">
                ERROR {selectedRun.error_count} · WARN {selectedRun.warn_count}
              </Descriptions.Item>
              <Descriptions.Item label="规则集版本">
                <Typography.Text code>{selectedRun.rule_set_version}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="执行时间">
                {formatDateTime(selectedRun.started_at)} →{" "}
                {formatDateTime(selectedRun.finished_at)}
              </Descriptions.Item>
            </Descriptions>
            <QualityFindingTable
              findings={selectedRun.findings}
              onRetry={onReload}
            />
          </Space>
        ) : null}
        <Typography.Text type="secondary">
          每次检查都是只追加的新记录；重跑不会覆盖历史结论。规则判定以服务端为准，
          本页不重新计算。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="标注质量检查"
      description={`提交 ${submissionId} · 提交时自动执行，可手动重跑；三态判定中的「无结论」不等于通过。`}
      extra={
        <Space wrap>
          <Button
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={onReload}
          >
            刷新
          </Button>
          {phase === "ready" && runs.length > 0 ? rerunButton : null}
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}
