import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { buildAnnotationIAAListPath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { IAAMetricResult } from "../../types/annotation";

function metricValue(value: number | null): string {
  return value === null ? "N/A" : value.toFixed(6);
}

function metricColumns(): TableColumnsType<IAAMetricResult> {
  return [
    { title: "指标", dataIndex: "metric", width: 190 },
    { title: "标签", dataIndex: "label_key", width: 160 },
    {
      title: "值",
      dataIndex: "value",
      width: 140,
      render: (value: number | null, metric) =>
        metric.status === "OK" ? metricValue(value) : "N/A",
    },
    {
      title: "分子",
      dataIndex: "numerator",
      width: 120,
      render: (value: number | null) => metricValue(value),
    },
    {
      title: "分母",
      dataIndex: "denominator",
      width: 120,
      render: (value: number | null) => metricValue(value),
    },
    { title: "样本量", dataIndex: "sample_size", width: 100 },
    {
      title: "状态",
      dataIndex: "status",
      width: 160,
      render: (value: string) => (
        <Tag color={value === "OK" ? "green" : "orange"}>{value}</Tag>
      ),
    },
    {
      title: "配置",
      dataIndex: "config",
      render: (value: Record<string, unknown> | undefined) =>
        value ? (
          <Typography.Text code>{JSON.stringify(value)}</Typography.Text>
        ) : (
          "—"
        ),
    },
  ];
}

function IAADetailContent({
  projectId,
  reportId,
}: {
  projectId: string;
  reportId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getIAAReport(projectId, reportId, { signal }),
    [projectId, reportId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const extra = (
    <Space wrap>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() =>
          navigate(
            data
              ? buildAnnotationIAAListPath(projectId, data.task_id)
              : buildAnnotationIAAListPath(projectId),
          )
        }
      >
        返回列表
      </Button>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载 IAA 报告详情" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的 IAA 报告。"
            onRetry={reload}
          />
        ) : null}
        {data.status === "INSUFFICIENT_DATA" ? (
          <PageState
            compact
            variant="partial"
            title="IAA 样本不足"
            description="重叠样本不足或分母为 0 时，服务端返回 INSUFFICIENT_DATA；指标值显示 N/A，不显示 0。"
          />
        ) : null}
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="报告 ID">
            <IdentifierValue value={data.report_id} label="IAA 报告 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="任务">
            <IdentifierValue value={data.task_id} label="任务 ID" />
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <AnnotationStatusTag value={data.status} />
          </Descriptions.Item>
          <Descriptions.Item label="Schema">
            {data.schema_id} · v{data.schema_version}
          </Descriptions.Item>
          <Descriptions.Item label="样本 / 重叠 / 标注员">
            {data.sample_item_count} / {data.overlap_item_count} /{" "}
            {data.annotator_count}
          </Descriptions.Item>
          <Descriptions.Item label="指标口径版本">
            {data.metric_definitions_version}
          </Descriptions.Item>
          <Descriptions.Item label="计算时间">
            {formatDateTime(data.computed_at)}
          </Descriptions.Item>
        </Descriptions>
        <Table<IAAMetricResult>
          rowKey={(metric) => `${metric.metric}-${metric.label_key}`}
          size="small"
          columns={metricColumns()}
          dataSource={data.metrics}
          pagination={false}
          scroll={{ x: 1200 }}
        />
        {data.excluded_items.length > 0 ? (
          <Space direction="vertical" size={4}>
            <Typography.Text strong>排除项</Typography.Text>
            {data.excluded_items.map((item, index) => (
              <Typography.Text key={index} code>
                {JSON.stringify(item)}
              </Typography.Text>
            ))}
          </Space>
        ) : null}
        <Typography.Text type="secondary">
          IAA 不得用于惩罚性个人排名；黄金集准确率不等于 IAA。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title="IAA 报告详情"
      description="只读展示一致率、Kappa/Alpha、Span F1 与边界 MAE；缺失值显示 N/A。"
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationIAADetailPage() {
  const projectId = useProjectId();
  const { reportId } = useParams<{ reportId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="IAA 报告详情" description="IAA 必须按项目隔离展示。">
        <ProjectScopeRequired scope="IAA 报告详情" />
      </PageContainer>
    );
  }
  if (!reportId) {
    return (
      <PageContainer title="IAA 报告详情">
        <ProjectScopeRequired scope="IAA 报告详情" />
      </PageContainer>
    );
  }
  return (
    <IAADetailContent
      key={`${projectId}:${reportId}`}
      projectId={projectId}
      reportId={reportId}
    />
  );
}
