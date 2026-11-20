import { ReloadOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useCallback } from "react";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage, type CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { captureSessionApi } from "../../services/captureSessions";
import CaptureSessionListBody from "./CaptureSessionListBody";

function Content({ projectId }: { projectId: string }) {
  const load = useCallback((options: CursorPageRequestOptions) => captureSessionApi.list(projectId, options), [projectId]);
  const page = useCursorPage(load, (item) => item.session_id);
  return <PageContainer title="采集会话" description="查看真实采集执行记录；可用动作仅由服务端 allowed_actions 决定。" extra={<Button icon={<ReloadOutlined />} loading={page.refreshing} onClick={page.reload}>刷新</Button>}>
    <CaptureSessionListBody {...page} onRetry={page.reload} onLoadMore={page.loadMore} />
  </PageContainer>;
}
export default function CaptureSessionListPage() {
  const projectId = useProjectId();
  if (!projectId) return <PageContainer title="采集会话" description="采集会话必须按项目隔离展示。"><ProjectScopeRequired scope="采集会话列表" /></PageContainer>;
  return <Content key={projectId} projectId={projectId} />;
}
