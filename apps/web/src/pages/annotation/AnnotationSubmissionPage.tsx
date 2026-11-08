import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Divider, Space, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AnnotationFeedbackAlert from "../../components/annotation/AnnotationFeedbackAlert";
import ArbitrationPanel, {
  type ArbitrationCreateFormInput,
} from "../../components/annotation/ArbitrationPanel";
import ReviewPanel, {
  type ReviewActionPayload,
} from "../../components/annotation/ReviewPanel";
import SubmissionSummary from "../../components/annotation/SubmissionSummary";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import {
  annotationSuccess,
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../../services/annotationPresentation";
import { loadSubmissionBundle } from "../../services/annotationQueues";
import { buildAnnotationTaskDetailPath } from "../../services/annotationUrl";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  AnnotationArbitration,
  ArbitrationDecision,
} from "../../types/annotation";

function SubmissionContent({
  projectId,
  submissionId,
}: {
  projectId: string;
  submissionId: string;
}) {
  const navigate = useNavigate();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);

  const load = useCallback(
    (signal: AbortSignal) =>
      loadSubmissionBundle(annotationApi, projectId, submissionId, signal),
    [projectId, submissionId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const runAction = useCallback(
    async (key: string, operation: () => Promise<unknown>, success: string) => {
      setBusyAction(key);
      setFeedback(null);
      try {
        await operation();
        setFeedback(annotationSuccess(success));
        reload();
      } catch (caught) {
        setFeedback(describeAnnotationActionError(caught));
      } finally {
        setBusyAction(null);
      }
    },
    [reload],
  );

  const handleSubmissionAction = (action: "submit" | "withdraw") => {
    if (!data) {
      return;
    }
    void runAction(
      `submission:${action}`,
      () =>
        annotationApi.actOnSubmission(projectId, submissionId, action, {
          version: data.submission.version,
          reason:
            action === "withdraw" ? "提交详情页撤回草稿以重新填写" : null,
        }),
      action === "submit" ? "提交已冻结，等待复核。" : "草稿已撤回。",
    );
  };

  const handleCreateReview = (reviewerId: string) => {
    void runAction(
      "review:create",
      () =>
        annotationApi.createReviewForSubmission(
          projectId,
          submissionId,
          reviewerId,
        ),
      "复核已创建。",
    );
  };

  const handleReviewAction = (
    reviewId: string,
    version: number,
    action: string,
    payload: ReviewActionPayload,
  ) => {
    void runAction(
      `review:${reviewId}:${action}`,
      () =>
        annotationApi.actOnReview(projectId, reviewId, action, {
          version,
          reason: payload.reason ?? null,
          issues: payload.issues,
          checklist: payload.checklist,
        }),
      `复核动作 ${action} 已提交。`,
    );
  };

  const handleCreateArbitration = (input: ArbitrationCreateFormInput) => {
    void runAction(
      "arb:create",
      () =>
        annotationApi.createArbitrationForSubmission(
          projectId,
          submissionId,
          {
            arbiterIds: input.arbiterIds,
            quorum: input.quorum,
            trigger: input.trigger,
            reviewId: input.reviewId,
          },
        ),
      "仲裁已创建。",
    );
  };

  const handleArbitrationAction = (
    arbitration: AnnotationArbitration,
    action: string,
    reason: string | null,
  ) => {
    void runAction(
      `arb:${arbitration.arbitration_id}:${action}`,
      () =>
        annotationApi.actOnArbitration(
          projectId,
          arbitration.arbitration_id,
          action,
          { version: arbitration.version, reason },
        ),
      `仲裁动作 ${action} 已提交。`,
    );
  };

  const handleVote = (
    arbitration: AnnotationArbitration,
    decision: ArbitrationDecision,
    reason: string | null,
  ) => {
    void runAction(
      `arb:${arbitration.arbitration_id}:vote`,
      () =>
        annotationApi.voteOnArbitration(
          projectId,
          arbitration.arbitration_id,
          { decision, reason },
          { version: arbitration.version },
        ),
      "投票已记录。",
    );
  };

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() =>
          navigate(
            data
              ? buildAnnotationTaskDetailPath(projectId, data.submission.task_id)
              : "/annotation-tasks",
          )
        }
      >
        返回任务
      </Button>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载提交详情"
        description="正在读取提交、记录、复核与仲裁。"
      />
    );
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，当前展示的是上次成功获取的提交数据。"
            onRetry={reload}
          />
        ) : null}
        {data.partialErrors.length > 0 ? (
          <PageState
            compact
            variant="partial"
            title="部分数据加载失败"
            description={data.partialErrors
              .map((item) => `${item.scope}（${item.error.code ?? "unknown"}）`)
              .join("；")}
            onRetry={reload}
          />
        ) : null}
        <AnnotationFeedbackAlert feedback={feedback} />
        <SubmissionSummary
          submission={data.submission}
          records={data.records}
          busyAction={busyAction}
          onSubmit={() => handleSubmissionAction("submit")}
          onWithdraw={() => handleSubmissionAction("withdraw")}
        />
        <Divider orientation="left">复核</Divider>
        <ReviewPanel
          submission={data.submission}
          reviews={data.reviews}
          busyAction={busyAction}
          onCreate={handleCreateReview}
          onAction={(review, action, payload) =>
            handleReviewAction(review.review_id, review.version, action, payload)
          }
        />
        <Divider orientation="left">仲裁</Divider>
        <ArbitrationPanel
          submission={data.submission}
          arbitrations={data.arbitrations}
          busyAction={busyAction}
          onCreate={handleCreateArbitration}
          onAction={handleArbitrationAction}
          onVote={handleVote}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="标注提交详情"
      description={
        data
          ? `提交 v${data.submission.submission_version} · ${data.submission.submission_id}`
          : "查看提交版本、记录、复核与仲裁。"
      }
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationSubmissionPage() {
  const projectId = useProjectId();
  const { submissionId } = useParams<{ submissionId: string }>();
  if (!projectId) {
    return (
      <PageContainer title="标注提交详情" description="提交必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注提交详情" />
      </PageContainer>
    );
  }
  if (!submissionId) {
    return (
      <PageContainer title="标注提交详情">
        <ProjectScopeRequired scope="标注提交详情" />
      </PageContainer>
    );
  }
  return (
    <SubmissionContent
      key={`${projectId}:${submissionId}`}
      projectId={projectId}
      submissionId={submissionId}
    />
  );
}
