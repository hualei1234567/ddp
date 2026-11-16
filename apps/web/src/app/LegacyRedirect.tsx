import { Navigate, useLocation } from "react-router-dom";

interface LegacyRedirectProps {
  /** 目标路径（不含查询串）。 */
  to: string;
}

/**
 * 旧路径 → 新路径的保留式重定向。
 *
 * 质量资源从 `/annotation/*` 迁到 `/quality/*`（导航「质量管理」）后，
 * 历史书签与外部链接仍可能指向旧地址：这里原样保留查询串（`project_id`、
 * `task_id` 等），只替换路径，避免用户落到"未选择项目"的空态。
 */
export default function LegacyRedirect({ to }: LegacyRedirectProps) {
  const location = useLocation();
  return (
    <Navigate to={{ pathname: to, search: location.search }} replace />
  );
}
