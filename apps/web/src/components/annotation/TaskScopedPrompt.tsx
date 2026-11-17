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
  /** 输入框占位文案；默认按标注任务。质量管理页复用它时改成提交 ID 等。 */
  placeholder?: string;
  /** 状态标题；默认「请选择任务以查看…」，按提交聚合时由调用方覆盖。 */
  title?: string;
  /** 接口的作用域说明；默认说明"按任务聚合"。 */
  hint?: string;
  /** 是否展示「从任务列表选择」入口；提交质量检查等按提交聚合的页面不需要。 */
  showTaskListLink?: boolean;
}

/**
 * 任务范围提示：IAA/抽检/复核/仲裁的后端列表都要求 task_id，
 * 未指定时明确提示输入，而不是请求全量或伪造数据。
 *
 * 质量管理里的提交质量检查同样需要 URL 参数（submission_id），文案与入口
 * 通过可选属性覆盖，避免为同一个「缺 ID 就让用户输入」的交互再写一份。
 */
export default function TaskScopedPrompt({
  projectId,
  scope,
  initialTaskId,
  onApply,
  placeholder = "标注任务 UUID",
  title,
  hint,
  showTaskListLink = true,
}: TaskScopedPromptProps) {
  const [taskId, setTaskId] = useState(initialTaskId ?? "");
  return (
    <PageState
      variant="empty"
      title={title ?? `请选择任务以查看${scope}`}
      description={
        <Space direction="vertical" size={8}>
          <Typography.Text>
            {hint ??
              `${scope}接口按任务聚合，需要 URL 参数 task_id 或在此输入任务 ID。`}
          </Typography.Text>
          <Space wrap>
            <Input
              placeholder={placeholder}
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
            {showTaskListLink ? (
              <Link to={buildAnnotationTaskListPath(projectId)}>
                <Button>从任务列表选择</Button>
              </Link>
            ) : null}
          </Space>
        </Space>
      }
    />
  );
}
