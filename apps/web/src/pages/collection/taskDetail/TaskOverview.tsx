import {
  Alert,
  Descriptions,
  Progress,
  Space,
  Typography,
} from "antd";

import CollectionStatusTag from "../../../components/collection/CollectionStatusTag";
import IdentifierValue from "../../../components/ui/IdentifierValue";
import {
  buildCountProgress,
  formatDurationSeconds,
  getTaskStatus,
} from "../../../services/collectionPresentation";
import { formatDateTime } from "../../../services/format";
import type { CollectionTask } from "../../../types/collection";

function formatQualitySummary(
  summary: Record<string, unknown> | null,
): string {
  if (!summary) {
    return "—";
  }
  const keys = Object.keys(summary);
  return keys.length === 0 ? "—" : keys.join("、");
}

/** 任务详情概览。仅展示服务端字段，不推断状态或权限。 */
export default function TaskOverview({ task }: { task: CollectionTask }) {
  const episodeProgress = buildCountProgress(
    task.accepted_episode_count,
    task.target_episode_count,
    "条",
  );
  const submittedProgress = buildCountProgress(
    task.submitted_episode_count,
    task.target_episode_count,
    "条",
  );

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      <Alert
        type="info"
        showIcon
        message="状态与动作以服务端为准"
        description={`当前状态：${getTaskStatus(task.status).label}（${task.status}）。页面不直接修改状态；动作按钮仅在服务端下发 allowed_actions 时可用。`}
      />
      <Descriptions column={{ xs: 1, sm: 1, md: 2 }} size="middle" bordered>
        <Descriptions.Item label="任务名称">{task.name}</Descriptions.Item>
        <Descriptions.Item label="任务编码">{task.code}</Descriptions.Item>
        <Descriptions.Item label="任务 ID">
          <IdentifierValue value={task.task_id} label="任务 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <CollectionStatusTag kind="task" value={task.status} />
        </Descriptions.Item>
        <Descriptions.Item label="优先级">
          <CollectionStatusTag kind="priority" value={task.priority} />
        </Descriptions.Item>
        <Descriptions.Item label="版本">v{task.version}</Descriptions.Item>
        <Descriptions.Item label="负责人">
          <IdentifierValue value={task.assigned_to} label="负责人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="机器人">
          <IdentifierValue value={task.assigned_robot_id} label="机器人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="设备">
          <Space direction="vertical" size={2}>
            {task.assigned_device_ids.length === 0 ? (
              <Typography.Text type="secondary">—</Typography.Text>
            ) : (
              task.assigned_device_ids.map((deviceId) => (
                <IdentifierValue
                  key={deviceId}
                  value={deviceId}
                  label="设备 ID"
                />
              ))
            )}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="传感器">
          <Space direction="vertical" size={2}>
            {task.assigned_sensor_ids.length === 0 ? (
              <Typography.Text type="secondary">—</Typography.Text>
            ) : (
              task.assigned_sensor_ids.map((sensorId) => (
                <IdentifierValue
                  key={sensorId}
                  value={sensorId}
                  label="传感器 ID"
                />
              ))
            )}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="场地">
          <IdentifierValue value={task.location_id} label="场地 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="SOP 版本">
          {task.sop_version ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="计划时间">
          {formatDateTime(task.scheduled_start)} 至{" "}
          {formatDateTime(task.scheduled_end)}
        </Descriptions.Item>
        <Descriptions.Item label="实际时间">
          {formatDateTime(task.actual_start)} 至{" "}
          {formatDateTime(task.actual_end)}
        </Descriptions.Item>
        <Descriptions.Item label="目标 Episode">
          {task.target_episode_count}
        </Descriptions.Item>
        <Descriptions.Item label="目标时长">
          {formatDurationSeconds(task.target_duration_sec)}
        </Descriptions.Item>
        <Descriptions.Item label="验收进度">
          <Space direction="vertical" size={4} className="ddp-stack">
            <Typography.Text>{episodeProgress.text}</Typography.Text>
            <Progress
              percent={episodeProgress.percent ?? 0}
              showInfo={episodeProgress.percent !== null}
              status={episodeProgress.percent === null ? "normal" : "active"}
            />
            {episodeProgress.percent === null ? (
              <Typography.Text type="secondary">
                目标数量为 0 或缺失，进度显示 N/A，不伪造百分比。
              </Typography.Text>
            ) : null}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="提交进度">
          {submittedProgress.text}
        </Descriptions.Item>
        <Descriptions.Item label="质量摘要">
          {formatQualitySummary(task.quality_summary)}
        </Descriptions.Item>
        <Descriptions.Item label="状态原因">
          {task.status_reason ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="阻塞原因">
          {task.blocked_reason ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">
          {formatDateTime(task.created_at)}
        </Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {formatDateTime(task.updated_at)}
        </Descriptions.Item>
        <Descriptions.Item label="创建人">
          <IdentifierValue value={task.created_by} label="创建人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="更新人">
          <IdentifierValue value={task.updated_by} label="更新人 ID" />
        </Descriptions.Item>
      </Descriptions>
    </Space>
  );
}
