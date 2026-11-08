import { Alert, Descriptions, Space } from "antd";

import CollectionStatusTag from "../../../components/collection/CollectionStatusTag";
import SopPanel from "../../../components/collection/SopPanel";
import IdentifierValue from "../../../components/ui/IdentifierValue";
import { buildCountProgress } from "../../../services/collectionPresentation";
import { formatDateTime } from "../../../services/format";
import type { CollectionTask } from "../../../types/collection";

/** 工作台第 1 步：核对任务与版本快照。 */
export default function TaskConfirmStep({ task }: { task: CollectionTask }) {
  const progress = buildCountProgress(
    task.accepted_episode_count,
    task.target_episode_count,
    "条",
  );
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Alert
        type="info"
        showIcon
        message="先核对任务与版本，再开始开工检查"
        description="任务状态、分配与 SOP 版本均以服务端返回为准；现场不得自行切换 SOP 版本。"
      />
      <Descriptions column={{ xs: 1, sm: 1, md: 2 }} size="middle" bordered>
        <Descriptions.Item label="任务">{task.name}</Descriptions.Item>
        <Descriptions.Item label="编码">{task.code}</Descriptions.Item>
        <Descriptions.Item label="任务 ID">
          <IdentifierValue value={task.task_id} label="任务 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <CollectionStatusTag kind="task" value={task.status} />
        </Descriptions.Item>
        <Descriptions.Item label="优先级">
          <CollectionStatusTag kind="priority" value={task.priority} />
        </Descriptions.Item>
        <Descriptions.Item label="SOP 版本">
          {task.sop_version ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="机器人">
          <IdentifierValue value={task.assigned_robot_id} label="机器人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="负责人">
          <IdentifierValue value={task.assigned_to} label="负责人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="计划时间">
          {formatDateTime(task.scheduled_start)} 至{" "}
          {formatDateTime(task.scheduled_end)}
        </Descriptions.Item>
        <Descriptions.Item label="目标进度">
          {progress.text}
        </Descriptions.Item>
      </Descriptions>
      <SopPanel sopId={task.sop_id} sopVersion={task.sop_version} compact />
    </Space>
  );
}
