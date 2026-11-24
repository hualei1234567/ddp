/**
 * 周期工作量展示体（V31-026，只读）。
 *
 * 数据来自服务端 `GET /projects/{project_id}/annotation-workload`。本组件
 * 只做展示与状态覆盖，不推导容量、不计算利用率：`capacity_minutes` 为
 * `null` 表示部署未配置容量，**不是 0**，因此渲染成「未配置」而不是「0 分钟」。
 *
 * 状态覆盖：Loading / Empty / Error / Stale / Partial（口径不完整）。
 */
import { Alert, Descriptions, Space, Tag, Typography } from "antd";

import { formatDateTime } from "../../services/format";
import { formatMinutes, formatRatio } from "../../services/workloadPresentation";
import type { ApiError } from "../../services/api";
import type {
  AnnotationWorkload,
  MissingSemantics,
  WorkloadStatus,
} from "../../types/annotationWorkload";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";

const STATUS_PRESENTATION: Record<
  WorkloadStatus,
  { label: string; color?: string }
> = {
  NORMAL: { label: "正常", color: "green" },
  AT_CAPACITY: { label: "达到容量", color: "orange" },
  OVER_CAPACITY: { label: "超出容量", color: "red" },
  STALE: { label: "数据过期", color: "gold" },
  PARTIAL: { label: "数据不完整", color: "gold" },
  UNKNOWN: { label: "状态未知" },
};

const MISSING_LABELS: Record<Exclude<MissingSemantics, "NONE">, string> = {
  "N/A": "容量或工时口径不适用",
  UNKNOWN: "统计口径未知",
  STALE: "部分事件已过期",
  PARTIAL: "部分事件缺失",
};

export interface WorkloadSummaryBodyProps {
  data: AnnotationWorkload | null;
  loading: boolean;
  stale: boolean;
  error: ApiError | null;
  onRetry: () => void;
  /** 服务端成员目录里的显示名；缺失时退回 user_id。 */
  assigneeLabel: string;
}

export function WorkloadSummaryBody({
  data,
  loading,
  stale,
  error,
  onRetry,
  assigneeLabel,
}: WorkloadSummaryBodyProps) {
  if (loading && data === null) {
    return (
      <PageState
        compact
        variant="loading"
        title="正在加载工作量"
        description="正在按周期聚合分配事件。"
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
        description="暂无工作量数据，请选择人员与周期后重试。"
        onRetry={onRetry}
      />
    );
  }

  const status = STATUS_PRESENTATION[data.status];

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，当前展示的是上次成功获取的工作量。"
          onRetry={onRetry}
        />
      ) : null}
      {data.missing_semantics !== "NONE" ? (
        <Alert
          type="warning"
          showIcon
          message={`统计口径不完整：${MISSING_LABELS[data.missing_semantics]}`}
          description="服务端标记该周期的聚合并不覆盖全部事件；请勿据此直接判断超载。"
        />
      ) : null}
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="人员">{assigneeLabel}</Descriptions.Item>
        <Descriptions.Item label="周期">
          {formatDateTime(data.period_start)} — {formatDateTime(data.period_end)}
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={status.color}>{status.label}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="分配项数">
          {data.assigned_item_count}
        </Descriptions.Item>
        <Descriptions.Item label="已领取">
          {data.claimed_item_count}
        </Descriptions.Item>
        <Descriptions.Item label="已提交">
          {data.submitted_item_count}
        </Descriptions.Item>
        <Descriptions.Item label="已通过">
          {data.approved_item_count}
        </Descriptions.Item>
        <Descriptions.Item label="返工">
          {data.rework_item_count}
        </Descriptions.Item>
        <Descriptions.Item label="实际工时">
          {formatMinutes(data.total_minutes)}
        </Descriptions.Item>
        <Descriptions.Item label="周期容量">
          {data.capacity_minutes === null
            ? "未配置"
            : formatMinutes(data.capacity_minutes)}
        </Descriptions.Item>
        <Descriptions.Item label="利用率">
          {formatRatio(data.utilization_ratio, "未配置容量，不计算利用率")}
        </Descriptions.Item>
        <Descriptions.Item label="聚合口径">
          <Typography.Text code>{data.source_version}</Typography.Text>
        </Descriptions.Item>
      </Descriptions>
      <Alert
        type="info"
        showIcon
        message="工作量是派生事实"
        description="本视图只读：不会改派、关闭任务或修改任何状态；周期为半开区间 [开始, 结束)。"
      />
    </Space>
  );
}
