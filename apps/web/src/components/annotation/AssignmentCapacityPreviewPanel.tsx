/**
 * 分配前实时负载提示（FR-020）。
 *
 * 职责边界（写在这里是因为最容易被越界）：
 *
 * - **只提示，不阻断**。`will_exceed` 只影响颜色与文案；提交按钮的可用性由
 *   调用方决定，且最终是否放行由服务端既有 `CapacityPolicy` 硬阻断裁决。
 *   本组件不复制一套阻断逻辑，否则"提示"与"拒绝"会两套规则各说各话。
 * - **不是自动均衡**。只如实展示"这个人现在有多少活、再派会不会超"，不排序、
 *   不推荐、不承诺会把负载摊平。
 * - `max_*` 为 `null` = 部署未配置上限，渲染成「未配置」而不是 0；未配置时
 *   进度条不出现，"没配上限"与"容量为 0"必须一眼可辨。
 */
import { Alert, Space, Tag, Typography } from "antd";

import {
  formatMinutes,
  formatRatio,
} from "../../services/workloadPresentation";
import type {
  AssignmentCapacityPreview,
  CapacityPreviewStatus,
} from "../../types/annotationCapacityPreview";

const STATUS_PRESENTATION: Record<
  CapacityPreviewStatus,
  { label: string; color?: string }
> = {
  NORMAL: { label: "正常", color: "green" },
  AT_CAPACITY: { label: "正好达到上限", color: "orange" },
  OVER_CAPACITY: { label: "将超上限", color: "red" },
};

export interface AssignmentCapacitySummaryProps {
  preview: AssignmentCapacityPreview;
  /** 被分配人显示名；缺失时退回 user_id，不臆造姓名。 */
  assigneeLabel: string;
}

/**
 * 单人负载提示条：项数与工时各一行，超限时给出明确警示。
 *
 * 用 `Alert` 而非纯文本，是因为这块信息在抽屉里必须「一眼看到」——
 * FR-020 的原缺口就是操作者提交前根本不知道对方已经满负荷。
 */
export function AssignmentCapacitySummary({
  preview,
  assigneeLabel,
}: AssignmentCapacitySummaryProps) {
  const status = STATUS_PRESENTATION[preview.status];
  const tone =
    preview.status === "OVER_CAPACITY"
      ? "error"
      : preview.status === "AT_CAPACITY"
        ? "warning"
        : "info";

  return (
    <Alert
      type={tone}
      showIcon
      message={
        <Space wrap size={8}>
          <span>{assigneeLabel}</span>
          <Tag color={status.color}>{status.label}</Tag>
          {preview.will_exceed ? (
            <Typography.Text type="danger">
              本次分配后将超出服务端配置的容量上限
            </Typography.Text>
          ) : null}
        </Space>
      }
      description={
        <Space direction="vertical" size={2}>
          <span>
            当前进行中：{preview.open_item_count} 项 ·{" "}
            {formatMinutes(preview.open_planned_minutes)}
          </span>
          <span>
            本次分配：{preview.planned_item_count} 项 ·{" "}
            {formatMinutes(preview.planned_minutes)}，分配后{" "}
            {preview.projected_item_count} 项 ·{" "}
            {formatMinutes(preview.projected_planned_minutes)}
          </span>
          <span>
            上限：
            {preview.max_open_items === null
              ? "项数未配置"
              : `${preview.max_open_items} 项`}
            {" · "}
            {preview.max_planned_minutes === null
              ? "工时未配置"
              : formatMinutes(preview.max_planned_minutes)}
            {" · "}
            占比：
            {formatRatio(
              preview.item_capacity_ratio,
              "未配置上限，不计算占比",
            )}{" "}
            /{" "}
            {formatRatio(
              preview.minutes_capacity_ratio,
              "未配置上限，不计算占比",
            )}
          </span>
          {preview.exceeds_open_items ? (
            <Typography.Text type="danger">
              项数将超上限（{preview.projected_item_count} &gt;{" "}
              {preview.max_open_items}）
            </Typography.Text>
          ) : null}
          {preview.exceeds_planned_minutes ? (
            <Typography.Text type="danger">
              工时将超上限（
              {formatMinutes(preview.projected_planned_minutes)} &gt;{" "}
              {formatMinutes(preview.max_planned_minutes ?? 0)}）
            </Typography.Text>
          ) : null}
        </Space>
      }
    />
  );
}
