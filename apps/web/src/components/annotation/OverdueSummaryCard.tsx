/**
 * 标注逾期汇总卡片（N6-002 收尾，只读）。
 *
 * 数据来自服务端 `GET /projects/{project_id}/annotation-overdue-summary`：
 * 总数、按优先级/状态分布、最早期逾期时间与 SLA 分桶。分桶边界由服务端回传，
 * 前端只展示、不推导；卡片不提供任何写操作。
 *
 * 状态覆盖：Loading / Empty / Error / Forbidden / Stale。空数据是"当前项目
 * 没有逾期任务"的真实结论，不渲染成 0 条异常。
 */
import { Alert, Card, Descriptions, Space, Tag, Typography } from "antd";
import { useCallback } from "react";

import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { presentAnnotationStatus } from "../../services/annotationPresentation";
import { formatDateTime } from "../../services/format";
import type { AnnotationOverdueSummary } from "../../types/annotationOverdue";
import type { ApiError } from "../../services/api";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

const TONE_COLOR = {
  default: undefined,
  processing: "blue",
  success: "green",
  warning: "orange",
  error: "red",
} as const;

const BUCKET_LABELS: Record<string, string> = {
  LT_24H: "24 小时以内",
  D1_TO_D3: "1–3 天",
  GT_3D: "超过 3 天",
};

function bucketLabel(bucket: string): string {
  return BUCKET_LABELS[bucket] ?? bucket;
}

function bucketRange(lower: number, upper: number | null): string {
  return upper === null ? `≥ ${lower} 小时` : `${lower}–${upper} 小时`;
}

export interface OverdueSummaryBodyProps {
  data: AnnotationOverdueSummary | null;
  loading: boolean;
  stale: boolean;
  error: ApiError | null;
  onRetry: () => void;
}

/** 纯展示体：便于用静态渲染覆盖各状态。 */
export function OverdueSummaryBody({
  data,
  loading,
  stale,
  error,
  onRetry,
}: OverdueSummaryBodyProps) {
  if (loading && data === null) {
    return (
      <PageState
        compact
        variant="loading"
        title="正在加载逾期汇总"
        description="正在按当前项目统计逾期任务。"
      />
    );
  }
  if (error && data === null) {
    return <ApiErrorState error={error} onRetry={onRetry} compact />;
  }
  if (data === null) {
    return (
      <PageState
        compact
        variant="empty"
        description="暂无逾期汇总数据。"
        onRetry={onRetry}
      />
    );
  }
  if (data.total_overdue === 0) {
    return (
      <PageState
        compact
        variant="empty"
        title="当前项目没有逾期任务"
        description={`按服务端 ${formatDateTime(data.evaluated_at)} 的统计口径，所有任务均未超过 SLA 截止时间。`}
      />
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，当前展示的是上次成功获取的逾期汇总。"
          onRetry={onRetry}
        />
      ) : null}
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="逾期任务数">
          <Tag color="red">{data.total_overdue}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="最早期逾期">
          {formatDateTime(data.earliest_due_at)}
        </Descriptions.Item>
        <Descriptions.Item label="统计时间">
          {formatDateTime(data.evaluated_at)}
        </Descriptions.Item>
      </Descriptions>
      <Space direction="vertical" size={4}>
        <Typography.Text strong>SLA 分桶</Typography.Text>
        <Space wrap size={[8, 8]}>
          {data.buckets.map((item) => (
            <Tag
              key={item.bucket}
              color={item.count > 0 ? "red" : undefined}
              title={bucketRange(item.lower_bound_hours, item.upper_bound_hours)}
            >
              {bucketLabel(item.bucket)}：{item.count}
            </Tag>
          ))}
        </Space>
      </Space>
      <Space direction="vertical" size={4}>
        <Typography.Text strong>按优先级</Typography.Text>
        <Space wrap size={[8, 8]}>
          {data.by_priority.map((item) => (
            <Tag key={item.priority} color={item.count > 0 ? "orange" : undefined}>
              {item.priority}：{item.count}
            </Tag>
          ))}
        </Space>
      </Space>
      <Space direction="vertical" size={4}>
        <Typography.Text strong>按状态</Typography.Text>
        {data.by_status.length === 0 ? (
          <Typography.Text type="secondary">没有逾期任务处于任何状态。</Typography.Text>
        ) : (
          <Space wrap size={[8, 8]}>
            {data.by_status.map((item) => {
              const presentation = presentAnnotationStatus(item.status);
              return (
                <Tag key={item.status} color={TONE_COLOR[presentation.tone]}>
                  {presentation.label}：{item.count}
                </Tag>
              );
            })}
          </Space>
        )}
      </Space>
      <Alert
        type="info"
        showIcon
        message="逾期是派生事实"
        description="本卡片只读：不会改派、关闭任务或修改任何状态。分桶边界由服务端领域常量给出。"
      />
    </Space>
  );
}

interface OverdueSummaryCardProps {
  projectId: string;
}

export default function OverdueSummaryCard({ projectId }: OverdueSummaryCardProps) {
  const load = useCallback(
    (signal: AbortSignal) => annotationApi.getOverdueSummary(projectId, { signal }),
    [projectId],
  );
  const request = useRequest(load);

  return (
    <Card
      size="small"
      title="逾期汇总"
      extra={
        <Typography.Text type="secondary">
          只读 · 服务端强制项目隔离
        </Typography.Text>
      }
    >
      <OverdueSummaryBody
        data={request.data}
        loading={request.loading}
        stale={request.stale}
        error={request.error}
        onRetry={request.reload}
      />
    </Card>
  );
}
