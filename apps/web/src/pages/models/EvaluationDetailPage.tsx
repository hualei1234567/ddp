import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Divider, Space, Table, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/modelops/ActionBar";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { modelOpsApi } from "../../services/modelops";
import {
  presentEvaluationStatus,
  presentFailureCategory,
  presentSeverity,
  presentVerdict,
} from "../../services/modelopsPresentation";
import { readThresholdNumber } from "../../services/modelopsParsers";
import { evaluationListPath } from "../../services/modelopsUrl";
import { formatDateTime, formatIdentifier } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { Evaluation, FailureCase } from "../../types/modelops";

interface MetricRow {
  key: string;
  value: number | null;
  threshold: string;
}

function formatScalar(value: number | string | null): string {
  return value === null ? "—" : String(value);
}

function failureEpisode(item: FailureCase): string {
  if (item.episode_key) {
    return item.episode_key;
  }
  return item.episode_id ? formatIdentifier(item.episode_id) : "—";
}

function failureMetric(item: FailureCase): string {
  const comparison = `${formatScalar(item.observed)} → ${formatScalar(item.expected)}`;
  return item.metric ? `${item.metric}: ${comparison}` : comparison;
}

function failureEvidence(item: FailureCase): string {
  if (item.evidence === null) {
    return "—";
  }
  return typeof item.evidence === "string"
    ? item.evidence
    : JSON.stringify(item.evidence);
}

function metricRows(evaluation: Evaluation): MetricRow[] {
  const names = new Set([
    ...Object.keys(evaluation.metrics),
    ...Object.keys(evaluation.thresholds),
  ]);
  return [...names].sort().map((name) => ({
    key: name,
    value: evaluation.metrics[name] ?? null,
    threshold: readThresholdNumber(evaluation.thresholds[name]),
  }));
}

function EvaluationDetailContent({
  projectId,
  evaluationId,
}: {
  projectId: string;
  evaluationId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => modelOpsApi.getEvaluation(projectId, evaluationId, { signal }),
    [projectId, evaluationId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, payload: { reason: string | null }) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await modelOpsApi.actEvaluation(projectId, evaluationId, action, {
        version: data.version,
        reason: payload.reason,
      });
      reload();
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载评测" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const evaluation: Evaluation = data;
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
        <Descriptions
          size="small"
          column={2}
          items={[
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={evaluation.status} presentation={presentEvaluationStatus} />
              ),
            },
            {
              key: "verdict",
              label: "结论",
              children: <StatusTag value={evaluation.verdict} presentation={presentVerdict} />,
            },
            {
              key: "model",
              label: "模型版本",
              children: <Typography.Text copyable>{evaluation.model_version_id}</Typography.Text>,
            },
            {
              key: "dataset",
              label: "数据集版本",
              children: (
                <Typography.Text copyable>{evaluation.dataset_version_id}</Typography.Text>
              ),
            },
            {
              key: "baseline",
              label: "基线评测",
              children: evaluation.baseline_evaluation_id
                ? formatIdentifier(evaluation.baseline_evaluation_id)
                : "—",
            },
            {
              key: "error",
              label: "错误",
              children:
                evaluation.error_code || evaluation.error_summary
                  ? `${evaluation.error_code ?? ""} ${evaluation.error_summary ?? ""}`
                  : "—",
            },
            {
              key: "started",
              label: "开始时间",
              children: formatDateTime(evaluation.started_at),
            },
            {
              key: "finished",
              label: "完成时间",
              children: formatDateTime(evaluation.finished_at),
            },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          指标与阈值
        </Typography.Title>
        <Table<MetricRow>
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={metricRows(evaluation)}
          locale={{ emptyText: "尚无指标；评测完成前只展示阈值。" }}
          columns={[
            { title: "指标", dataIndex: "key" },
            {
              title: "实际值",
              dataIndex: "value",
              width: 140,
              render: (value: number | null) => (value === null ? "—" : String(value)),
            },
            { title: "阈值", dataIndex: "threshold", width: 160 },
          ]}
        />
        <Typography.Text type="secondary">
          阈值缺失的指标不会被判定为通过；评测完成时服务端按“任一失败即 FAIL，全通过才 PASS”计算结论。
        </Typography.Text>
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            失败用例
          </Typography.Title>
          <Typography.Text type="secondary">
            失败用例由适配器回调上报，平台只记录事实；空列表表示本次未报告失败用例，不代表零失败。
          </Typography.Text>
        </Space>
        <Table<FailureCase>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={evaluation.failure_cases}
          locale={{ emptyText: "本次评测未报告失败用例。" }}
          columns={[
            {
              title: "类别",
              dataIndex: "category",
              width: 100,
              render: (value: string) => presentFailureCategory(value),
            },
            {
              title: "严重度",
              dataIndex: "severity",
              width: 100,
              render: (value: string) => (
                <StatusTag value={value} presentation={presentSeverity} />
              ),
            },
            {
              title: "Episode",
              key: "episode",
              width: 220,
              render: (_: unknown, item: FailureCase) => failureEpisode(item),
            },
            {
              title: "指标 / 观测 → 期望",
              key: "metric",
              width: 200,
              render: (_: unknown, item: FailureCase) => failureMetric(item),
            },
            {
              title: "证据",
              key: "evidence",
              ellipsis: true,
              render: (_: unknown, item: FailureCase) => failureEvidence(item),
            },
            {
              title: "缺口线索",
              dataIndex: "suggested_gap",
              width: 160,
              ellipsis: true,
              render: (value: string | null) => value ?? "—",
            },
          ]}
        />
        {evaluation.report_ref ? (
          <>
            <Typography.Title level={5} style={{ margin: 0 }}>
              评测报告引用
            </Typography.Title>
            <Typography.Paragraph>
              <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                {JSON.stringify(evaluation.report_ref, null, 2)}
              </pre>
            </Typography.Paragraph>
          </>
        ) : null}
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            评测动作
          </Typography.Title>
          <ActionBar actions={evaluation.allowed_actions} busy={busy} onAction={act} />
        </Space>
      </Space>
    );
  }

  return (
    <PageContainer
      title="评测详情"
      description="评测结果由适配器回调写入；已完成评测不可修改。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(evaluationListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function EvaluationDetailPage() {
  const projectId = useProjectId();
  const { evaluationId } = useParams<{ evaluationId: string }>();
  if (!projectId || !evaluationId) {
    return (
      <PageContainer title="评测详情" description="评测必须按项目隔离展示。">
        <ProjectScopeRequired scope="评测详情" />
      </PageContainer>
    );
  }
  return (
    <EvaluationDetailContent
      key={`${projectId}:${evaluationId}`}
      projectId={projectId}
      evaluationId={evaluationId}
    />
  );
}
