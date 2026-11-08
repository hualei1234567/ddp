import { Button, Input, Space, Typography } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";

import { buildAnnotationTaskListPath } from "../../services/annotationUrl";
import PageState from "../ui/PageState";

interface TaskScopedPromptProps {
  projectId: string;
  scope: string;
  initialTaskId?: string | null;
  onApply: (taskId: string) => void;
}

/**
 * 任务范围提示：IAA/抽检/复核/仲裁的后端列表都要求 task_id，
 * 未指定时明确提示输入，而不是请求全量或伪造数据。
 */
export default function TaskScopedPrompt({
  projectId,
  scope,
  initialTaskId,
  onApply,
}: TaskScopedPromptProps) {
  const [taskId, setTaskId] = useState(initialTaskId ?? "");
  return (
    <PageState
      variant="empty"
      title={`请选择任务以查看${scope}`}
      description={
        <Space direction="vertical" size={8}>
          <Typography.Text>
            {scope}接口按任务聚合，需要 URL 参数 task_id 或在此输入任务 ID。
          </Typography.Text>
          <Space wrap>
            <Input
              placeholder="标注任务 UUID"
              value={taskId}
              onChange={(event) => setTaskId(event.target.value)}
              style={{ width: 340 }}
            />
            <Button
              type="primary"
              disabled={taskId.trim() === ""}
              onClick={() => onApply(taskId.trim())}
            >
              加载
            </Button>
            <Link to={buildAnnotationTaskListPath(projectId)}>
              <Button>从任务列表选择</Button>
            </Link>
          </Space>
        </Space>
      }
    />
  );
}
