/**
 * 个人领取队列（「我的任务」）。
 *
 * 数据来源全部是服务端权威接口：`GET /api/v1/me` 回答「我是谁」，
 * `/projects` 给出我可见的项目，分配按 `assignee_id = 我` 查询。
 * 页面**不提供**「随便挑一个用户看看」的入口——那会把别人的队列当自己的。
 *
 * 领取动作只按服务端 `allowed_actions` 渲染；提交/复核流程仍在工作台与复核页。
 */
import { ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Typography } from "antd";
import { useCallback, useState } from "react";

import PersonalQueueBody from "../../components/annotation/PersonalQueueBody";
import PageContainer from "../../components/ui/PageContainer";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import {
  annotationSuccess,
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../../services/annotationPresentation";
import { buildAnnotationWorkbenchPath } from "../../services/annotationUrl";
import { formatTimestamp } from "../../services/format";
import { identityApi } from "../../services/identity";
import { resolvePagePhase } from "../../services/pagePhase";
import { loadPersonalQueue, type QueueEntry } from "../../services/personalQueue";
import { projectManagementApi } from "../../services/projectManagement";

const deps = {
  identity: identityApi,
  projects: projectManagementApi,
  assignments: annotationApi,
};

export default function AnnotationMyTasksPage() {
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(
    (signal: AbortSignal) => loadPersonalQueue(deps, signal),
    [],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const claim = useCallback(
    async (entry: QueueEntry) => {
      const assignment = entry.assignment;
      setBusy(assignment.assignment_id);
      setFeedback(null);
      try {
        await annotationApi.actOnAssignment(
          entry.projectId,
          assignment.assignment_id,
          "claim",
          { version: assignment.version },
        );
        setFeedback(annotationSuccess("已领取，可在工作台开始标注。"));
        reload();
      } catch (caught) {
        setFeedback(describeAnnotationActionError(caught));
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  return (
    <PageContainer
      title="我的任务"
      description="按服务端认证身份聚合所有项目的待办分配；领取、开始与提交仍由服务端状态机裁决。"
      extra={
        <Space wrap>
          <Typography.Text type="secondary">
            更新于 {formatTimestamp(updatedAt)}
          </Typography.Text>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      <PersonalQueueBody
        phase={phase}
        data={data}
        error={error}
        stale={stale}
        feedback={feedback}
        busyAssignmentId={busy}
        onRetry={reload}
        onClaim={claim}
        buildWorkbenchPath={buildAnnotationWorkbenchPath}
      />
    </PageContainer>
  );
}
