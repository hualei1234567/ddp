import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from "antd";
import { useCallback } from "react";
import { Link } from "react-router-dom";

import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { buildAnnotationArbitrationQueuePath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import type { DoubleAnnotationConsistency } from "../../types/annotation";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

function statusColor(status: string): string {
  if (status === "PASSED") {
    return "green";
  }
  if (status === "FAILED") {
    return "red";
  }
  if (status === "PARTIAL" || status === "PENDING_SECOND_ANNOTATOR") {
    return "gold";
  }
  return "default";
}

function presentRate(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function ConsistencyContent({
  projectId,
  taskId,
  consistency,
}: {
  projectId: string;
  taskId: string;
  consistency: DoubleAnnotationConsistency;
}) {
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="一致性结论">
          <Space size={4}>
            <Tag color={statusColor(consistency.status)}>
              {consistency.status}
            </Tag>
            <Typography.Text type="secondary">
              {consistency.message ?? "—"}
            </Typography.Text>
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="判定指标">
          {consistency.metric ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="指标值 / 阈值">
          {presentRate(consistency.value)} / {presentRate(consistency.threshold)}
        </Descriptions.Item>
        <Descriptions.Item label="标注者数">
          {consistency.annotator_count}
        </Descriptions.Item>
        <Descriptions.Item label="双标 Episode">
          {consistency.double_episode_count} / {consistency.episode_count}
        </Descriptions.Item>
        <Descriptions.Item label="样本对">
          {consistency.sample_size}
        </Descriptions.Item>
        <Descriptions.Item label="自动通过">
          {consistency.auto_approve_enabled
            ? consistency.auto_approved
              ? "已自动通过"
              : "已开启，未执行"
            : "未开启"}
        </Descriptions.Item>
        <Descriptions.Item label="仲裁">
          {consistency.arbitration_required ? (
            <Space size={4}>
              <Tag color="red">需要仲裁</Tag>
              <span>{consistency.arbitration_ids.length} 项</span>
            </Space>
          ) : (
            "不需要"
          )}
        </Descriptions.Item>
        <Descriptions.Item label="评估时间">
          {formatDateTime(consistency.evaluated_at)}
        </Descriptions.Item>
      </Descriptions>
      {consistency.missing_episode_ids.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="以下 Episode 仍缺第二名标注者"
          description={consistency.missing_episode_ids.join("、")}
        />
      ) : null}
      {consistency.arbitration_ids.length > 0 ? (
        <Link to={buildAnnotationArbitrationQueuePath(projectId, taskId)}>
          <Tag color="orange">查看仲裁队列</Tag>
        </Link>
      ) : null}
    </Space>
  );
}

function DoubleAnnotationConsistencyContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      annotationApi.getTaskConsistency(projectId, taskId, { signal }),
    [projectId, taskId],
  );
  const { data, error, loading, reload } = useRequest(load);

  let content;
  if (loading && data === null) {
    content = (
      <PageState
        compact
        variant="loading"
        description="正在计算双标一致性结论。"
      />
    );
  } else if (error && data === null) {
    content = <ApiErrorState error={error} onRetry={reload} />;
  } else if (data === null) {
    content = (
      <PageState compact variant="empty" description="暂无双标一致性结论。" />
    );
  } else {
    content = (
      <ConsistencyContent
        projectId={projectId}
        taskId={taskId}
        consistency={data}
      />
    );
  }

  return (
    <Card
      size="small"
      title="双标一致性"
      extra={
        <Button size="small" onClick={reload} loading={loading}>
          刷新
        </Button>
      }
    >
      {content}
    </Card>
  );
}

export default function TaskConsistencyPanel({
  projectId,
  taskId,
  reviewMode,
}: {
  projectId: string;
  taskId: string;
  reviewMode: string;
}) {
  if (reviewMode !== "DOUBLE") {
    return null;
  }
  return (
    <DoubleAnnotationConsistencyContent projectId={projectId} taskId={taskId} />
  );
}
