import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Descriptions, Space } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import CaptureSessionActionBar from "../../components/collection/CaptureSessionActionBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/ui/StatusTag";
import { useCursorPage, type CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { presentCaptureSessionStatus } from "../../services/captureSessionPresentation";
import { captureSessionApi } from "../../services/captureSessions";
import { formatDateTime, formatText } from "../../services/format";
import type { CaptureSessionAction } from "../../types/captureSession";
import CaptureSessionArtifactList from "./CaptureSessionArtifactList";

function Content({ projectId, sessionId }: { projectId: string; sessionId: string }) {
  const navigate = useNavigate(); const [busy, setBusy] = useState(false);
  const loadDetail = useCallback((signal: AbortSignal) => captureSessionApi.get(projectId, sessionId, { signal }), [projectId, sessionId]);
  const detail = useRequest(loadDetail);
  const loadArtifacts = useCallback((options: CursorPageRequestOptions) => captureSessionApi.listArtifacts(projectId, sessionId, options), [projectId, sessionId]);
  const artifacts = useCursorPage(loadArtifacts, (item) => `${item.kind}:${item.artifact_id}`);
  const onAction = async (action: CaptureSessionAction, reason: string | null) => {
    if (!detail.data) return;
    setBusy(true);
    try { await captureSessionApi.action(projectId, sessionId, action, reason, { version: detail.data.version }); detail.reload(); }
    finally { setBusy(false); }
  };
  const detailBody = detail.loading ? <PageState variant="loading" title="正在加载会话详情" /> : detail.error && !detail.data ? <ApiErrorState error={detail.error} onRetry={detail.reload} /> : detail.data ? <Space direction="vertical" size={16} className="ddp-stack">
    {detail.stale ? <PageState compact variant="stale" description="详情刷新失败，展示上次成功获取的数据。" onRetry={detail.reload} /> : null}
    <Descriptions column={{ xs: 1, md: 2 }} bordered size="small" items={[
      { key: "code", label: "会话编码", children: detail.data.code },
      { key: "status", label: "状态", children: <StatusTag value={detail.data.status} presentation={presentCaptureSessionStatus} /> },
      { key: "task", label: "任务 ID", children: formatText(detail.data.task_id) },
      { key: "batch", label: "批次 ID", children: formatText(detail.data.batch_id) },
      { key: "operator", label: "操作员 ID", children: formatText(detail.data.operator_id) },
      { key: "robot", label: "机器人 ID", children: formatText(detail.data.robot_id) },
      { key: "location", label: "场地 ID", children: formatText(detail.data.location_id) },
      { key: "started", label: "开始时间", children: formatDateTime(detail.data.started_at) },
      { key: "ended", label: "结束时间", children: formatDateTime(detail.data.ended_at) },
      { key: "reason", label: "状态原因", children: formatText(detail.data.status_reason) },
    ]} />
    <Card size="small" title="会话动作"><CaptureSessionActionBar allowedActions={detail.data.allowed_actions} busy={busy || detail.refreshing} onAction={onAction} /></Card>
  </Space> : null;
  const oneRegionFailed = (detail.error !== null) !== (artifacts.error !== null);
  return <PageContainer title="采集会话详情" description="详情与产物分别加载；任一区域失败不会掩盖另一区域。" extra={<Space><Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/collection/capture-sessions")}>返回列表</Button><Button icon={<ReloadOutlined />} loading={detail.refreshing} onClick={detail.reload}>刷新详情</Button></Space>}>
    <Space direction="vertical" size={20} className="ddp-stack">{oneRegionFailed ? <PageState compact variant="partial" title="部分数据加载失败" description="会话详情与产物独立请求；可在失败区域单独重试。" /> : null}{detailBody}<Card title="会话产物"><CaptureSessionArtifactList {...artifacts} onRetry={artifacts.reload} onLoadMore={artifacts.loadMore} /></Card></Space>
  </PageContainer>;
}
export default function CaptureSessionDetailPage() {
  const projectId = useProjectId(); const { sessionId } = useParams<{ sessionId: string }>();
  if (!sessionId) return <PageContainer title="采集会话详情"><PageState variant="empty" title="未指定采集会话" /></PageContainer>;
  if (!projectId) return <PageContainer title="采集会话详情"><ProjectScopeRequired scope="采集会话详情" /></PageContainer>;
  return <Content key={`${projectId}:${sessionId}`} projectId={projectId} sessionId={sessionId} />;
}
