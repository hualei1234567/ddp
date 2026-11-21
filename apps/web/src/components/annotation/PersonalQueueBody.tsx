/**
 * 个人领取队列的展示体。
 *
 * 纯展示：数据、阶段与动作回调全部由页面传入，便于用静态渲染覆盖
 * Loading / Empty / Error / Forbidden / Partial / Stale 各状态。
 *
 * 「领取」按钮只按服务端 `allowed_actions` 渲染；缺失时不猜动作，
 * 而是给出明确提示（服务端没有授权任何动作）。
 */
import { Button, Card, Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import type { AnnotationFeedback } from "../../services/annotationPresentation";
import type { PagePhase } from "../../services/pagePhase";
import type { PersonalQueue, QueueEntry } from "../../services/personalQueue";
import AnnotationFeedbackAlert from "./AnnotationFeedbackAlert";
import AnnotationStatusTag from "./AnnotationStatusTag";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";

export interface PersonalQueueBodyProps {
  phase: PagePhase;
  data: PersonalQueue | null;
  error: ApiError | null;
  stale: boolean;
  feedback: AnnotationFeedback | null;
  busyAssignmentId: string | null;
  onRetry: () => void;
  onClaim: (entry: QueueEntry) => void;
  buildWorkbenchPath: (projectId: string, assignmentId: string) => string;
}

function QueueCard({
  entry,
  busyAssignmentId,
  onClaim,
  buildWorkbenchPath,
}: {
  entry: QueueEntry;
  busyAssignmentId: string | null;
  onClaim: PersonalQueueBodyProps["onClaim"];
  buildWorkbenchPath: PersonalQueueBodyProps["buildWorkbenchPath"];
}) {
  const assignment = entry.assignment;
  const canClaim = entry.allowedActions.includes("claim");
  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <Tag color="blue">{entry.projectCode}</Tag>
          <AnnotationStatusTag value={assignment.status} prefix="分配" />
          <span>{assignment.assignment_role}</span>
          <IdentifierValue value={assignment.task_id} label="任务 ID" />
        </Space>
      }
      extra={
        <Link
          to={buildWorkbenchPath(entry.projectId, assignment.assignment_id)}
        >
          打开工作台
        </Link>
      }
    >
      <Space wrap size={12}>
        <Typography.Text type="secondary">
          计划 {assignment.planned_item_count} 条 · 已领{" "}
          {assignment.claimed_item_count} · 已交{" "}
          {assignment.submitted_item_count} · 预期{" "}
          {assignment.planned_minutes} 分钟
        </Typography.Text>
        {canClaim ? (
          <Button
            type="primary"
            size="small"
            loading={busyAssignmentId === assignment.assignment_id}
            onClick={() => onClaim(entry)}
          >
            领取（claim）
          </Button>
        ) : entry.allowedActions.length === 0 ? (
          <Typography.Text type="secondary">
            服务端未返回可执行动作，请刷新或联系管理员确认权限。
          </Typography.Text>
        ) : null}
      </Space>
    </Card>
  );
}

export default function PersonalQueueBody({
  phase,
  data,
  error,
  stale,
  feedback,
  busyAssignmentId,
  onRetry,
  onClaim,
  buildWorkbenchPath,
}: PersonalQueueBodyProps) {
  if (phase === "forbidden" || phase === "not-found") {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载我的任务"
        description="正在读取当前身份、可访问项目与我的分配。"
      />
    );
  }
  if (phase === "error" || data === null) {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }

  const empty = data.open.length === 0 && data.submitted.length === 0;

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <AnnotationFeedbackAlert feedback={feedback} />
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示的是上次成功读取的队列。"
          onRetry={onRetry}
        />
      ) : null}
      {data.truncated ? (
        <PageState
          compact
          variant="partial"
          title="队列已截断"
          description="项目数或单个项目的页数超过上限，当前列表不是完整队列；请缩小范围后重试。"
          onRetry={onRetry}
        />
      ) : null}
      {data.failures.length > 0 ? (
        <PageState
          compact
          variant="partial"
          title={`${data.failures.length} 个作用域读取失败`}
          description={data.failures
            .map(
              (item) => `${item.scope}（${item.error.code ?? item.error.kind}）`,
            )
            .join("；")}
          onRetry={onRetry}
        />
      ) : null}
      {empty ? (
        <PageState
          variant="empty"
          title="当前没有待办分配"
          description="已扫描你加入的项目，没有处于待领取 / 进行中 / 待复核的分配。新分配出现后刷新即可看到。"
        />
      ) : null}
      {data.open.length > 0 ? (
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>待处理（{data.open.length}）</Typography.Text>
          {data.open.map((entry) => (
            <QueueCard
              key={entry.assignment.assignment_id}
              entry={entry}
              busyAssignmentId={busyAssignmentId}
              onClaim={onClaim}
              buildWorkbenchPath={buildWorkbenchPath}
            />
          ))}
        </Space>
      ) : null}
      {data.submitted.length > 0 ? (
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>
            已提交，等待复核（{data.submitted.length}）
          </Typography.Text>
          {data.submitted.map((entry) => (
            <QueueCard
              key={entry.assignment.assignment_id}
              entry={entry}
              busyAssignmentId={busyAssignmentId}
              onClaim={onClaim}
              buildWorkbenchPath={buildWorkbenchPath}
            />
          ))}
        </Space>
      ) : null}
    </Space>
  );
}
