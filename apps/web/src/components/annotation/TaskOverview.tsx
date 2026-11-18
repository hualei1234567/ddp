import { Alert, Descriptions, Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import {
  describeDueDistance,
  presentMissingSemantics,
  presentProgress,
} from "../../services/annotationPresentation";
import {
  buildAnnotationArbitrationQueuePath,
  buildAnnotationIAAListPath,
  buildAnnotationQualityListPath,
  buildAnnotationReviewQueuePath,
  buildAnnotationSampleSetDetailPath,
  buildAnnotationSchemaDetailPath,
} from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import type { AnnotationTask } from "../../types/annotation";
import IdentifierValue from "../ui/IdentifierValue";
import AnnotationStatusTag from "./AnnotationStatusTag";

interface TaskOverviewProps {
  projectId: string;
  task: AnnotationTask;
}

export default function TaskOverview({ projectId, task }: TaskOverviewProps) {
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="任务 ID">
          <IdentifierValue value={task.task_id} label="任务 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="编码">{task.code}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <AnnotationStatusTag value={task.status} />
        </Descriptions.Item>
        <Descriptions.Item label="优先级">
          <Tag>{task.priority}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="分配模式">
          {task.assignment_mode}
        </Descriptions.Item>
        <Descriptions.Item label="复核模式">
          <Tag color={task.review_mode === "DOUBLE" ? "purple" : undefined}>
            {task.review_mode === "DOUBLE" ? "双标（DOUBLE）" : "单人（SINGLE）"}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Schema">
          <Space direction="vertical" size={0}>
            <Link
              to={buildAnnotationSchemaDetailPath(projectId, task.schema_id)}
            >
              v{task.schema_version}
            </Link>
            <Typography.Text type="secondary">
              {task.schema_sha256.slice(0, 18)}…
            </Typography.Text>
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="样本集">
          <Link
            to={buildAnnotationSampleSetDetailPath(projectId, task.sample_set_id)}
          >
            <IdentifierValue value={task.sample_set_id} label="样本集 ID" />
          </Link>
        </Descriptions.Item>
        <Descriptions.Item label="目标项 / Episode">
          {task.target_item_count} / {task.target_episode_count}
        </Descriptions.Item>
        <Descriptions.Item label="截止时间">
          <Space direction="vertical" size={0}>
            <Space size={4} wrap>
              <span>{formatDateTime(task.due_at)}</span>
              {task.is_overdue ? <Tag color="red">已逾期</Tag> : null}
            </Space>
            {describeDueDistance(task.due_at, task.is_overdue) ? (
              <Typography.Text type={task.is_overdue ? "danger" : "secondary"}>
                {describeDueDistance(task.due_at, task.is_overdue)}
              </Typography.Text>
            ) : null}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="已提交">
          {presentProgress(task.progress, "submitted_items")}
        </Descriptions.Item>
        <Descriptions.Item label="已通过">
          {presentProgress(task.progress, "approved_items")}
        </Descriptions.Item>
        <Descriptions.Item label="返工 / 仲裁">
          {presentProgress(task.progress, "rework_items")} /{" "}
          {presentProgress(task.progress, "arbitration_items")}
        </Descriptions.Item>
        <Descriptions.Item label="缺失语义">
          {presentMissingSemantics(task.progress.missing_semantics)}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {formatDateTime(task.created_at)}
        </Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {formatDateTime(task.updated_at)}
        </Descriptions.Item>
      </Descriptions>
      {task.description ? (
        <Typography.Paragraph type="secondary">
          {task.description}
        </Typography.Paragraph>
      ) : null}
      {task.status_reason ? (
        <Alert type="warning" showIcon message="状态说明" description={task.status_reason} />
      ) : null}
      {task.blocked_reason ? (
        <Alert type="error" showIcon message="阻塞原因" description={task.blocked_reason} />
      ) : null}
      <Space wrap>
        <Link to={buildAnnotationReviewQueuePath(projectId, task.task_id)}>
          <Tag color="blue">复核队列</Tag>
        </Link>
        <Link to={buildAnnotationArbitrationQueuePath(projectId, task.task_id)}>
          <Tag color="orange">仲裁队列</Tag>
        </Link>
        <Link to={buildAnnotationIAAListPath(projectId, task.task_id)}>
          <Tag>IAA 报告</Tag>
        </Link>
        <Link to={buildAnnotationQualityListPath(projectId, task.task_id)}>
          <Tag>质检抽检</Tag>
        </Link>
      </Space>
    </Space>
  );
}
