import { Button } from "antd";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import PageState from "./PageState";

interface ProjectScopeRequiredProps {
  /** 页面名称，用于提示文案。 */
  scope: string;
  /** 额外操作，例如返回链接。 */
  action?: ReactNode;
}

/**
 * 未选择项目时的统一空态。项目 ID 只作为请求上下文，前端从不硬编码默认项目。
 */
export default function ProjectScopeRequired({
  scope,
  action,
}: ProjectScopeRequiredProps) {
  const navigate = useNavigate();
  return (
    <PageState
      variant="empty"
      title="未选择项目"
      description={`${scope}必须按项目隔离展示。请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。`}
      action={
        action ?? (
          <Button type="primary" onClick={() => navigate("/")}>
            返回工作台
          </Button>
        )
      }
    />
  );
}
