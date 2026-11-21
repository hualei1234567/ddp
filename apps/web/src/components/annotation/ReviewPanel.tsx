import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Input,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import type {
  AnnotationReview,
  AnnotationSubmission,
  ReviewChecklistItem,
  ReviewIssue,
  ReviewIssueSeverity,
} from "../../types/annotation";
import type { ProjectMember } from "../../types/project";
import { REVIEW_ISSUE_SEVERITY_VALUES } from "../../types/annotation";
import { formatDateTime } from "../../services/format";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

export interface ReviewActionPayload {
  reason?: string | null;
  issues?: ReviewIssue[];
  checklist?: ReviewChecklistItem[];
}

interface ReviewPanelProps {
  submission: AnnotationSubmission;
  reviews: AnnotationReview[];
  /** 项目成员：复核人候选；为空时退回手填 UUID。 */
  candidates: readonly ProjectMember[];
  /** 服务端认证的当前用户：默认复核人。 */
  currentUserId: string | null;
  /** 身份或成员列表读取失败，界面需说明为什么只能手填。 */
  candidatesDegraded?: boolean;
  busyAction: string | null;
  onCreate: (reviewerId: string) => void;
  onAction: (
    review: AnnotationReview,
    action: string,
    payload: ReviewActionPayload,
  ) => void;
}

function ReviewCard({
  review,
  busyAction,
  onAction,
}: {
  review: AnnotationReview;
  busyAction: string | null;
  onAction: ReviewPanelProps["onAction"];
}) {
  const [checked, setChecked] = useState(false);
  const [issueCode, setIssueCode] = useState("");
  const [issueSeverity, setIssueSeverity] =
    useState<ReviewIssueSeverity>("MAJOR");
  const [issueMessage, setIssueMessage] = useState("");
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const isInReview = review.status === "IN_REVIEW";
  const isPending = review.status === "PENDING";

  const run = (action: string, payload: ReviewActionPayload) => {
    setLocalError(null);
    onAction(review, action, payload);
  };

  const handleApprove = () => {
    if (!checked) {
      setLocalError("通过复核前必须勾选检查项。");
      return;
    }
    run("approve", {
      reason: reason.trim() || null,
      checklist: [{ key: "manual-review", passed: true, comment: null }],
    });
  };

  const handleReject = () => {
    if (issueCode.trim() === "" || issueMessage.trim() === "") {
      setLocalError("驳回必须填写问题码与问题说明。");
      return;
    }
    run("reject", {
      reason: reason.trim() || issueMessage.trim(),
      issues: [
        {
          code: issueCode.trim(),
          severity: issueSeverity,
          message: issueMessage.trim(),
          label_key: null,
          item_ref: null,
          evidence_ref: null,
        },
      ],
    });
  };

  const handleEscalate = () => {
    if (reason.trim() === "") {
      setLocalError("升级仲裁必须填写原因。");
      return;
    }
    run("escalate", { reason: reason.trim() });
  };

  return (
    <Card size="small" title={`第 ${review.review_round} 轮复核`}>
      <Space direction="vertical" size={8} className="ddp-stack">
        <Space wrap>
          <AnnotationStatusTag value={review.status} prefix="复核" />
          <span>复核人：</span>
          <IdentifierValue value={review.reviewer_id} label="复核人 ID" />
          <Typography.Text type="secondary">
            更新于 {formatDateTime(review.updated_at)}
          </Typography.Text>
        </Space>
        {review.decision ? <Tag>结论：{review.decision}</Tag> : null}
        {review.reason ? (
          <Typography.Paragraph type="secondary">
            原因：{review.reason}
          </Typography.Paragraph>
        ) : null}
        {review.issues.length > 0 ? (
          <Alert
            type="error"
            showIcon
            message="问题项"
            description={review.issues.map((issue) => (
              <div key={`${issue.code}-${issue.message}`}>
                [{issue.severity}] {issue.code}：{issue.message}
              </div>
            ))}
          />
        ) : null}
        {review.checklist.length > 0 ? (
          <Typography.Text type="secondary">
            检查项：
            {review.checklist
              .map((item) => `${item.key}=${item.passed ? "通过" : "未通过"}`)
              .join("；")}
          </Typography.Text>
        ) : null}
        {localError ? <Alert type="error" showIcon message={localError} /> : null}
        {isPending || isInReview ? (
          <Space direction="vertical" size={8} className="ddp-stack">
            <Space wrap>
              <Button
                disabled={!isPending || busyAction !== null}
                loading={busyAction === `review:${review.review_id}:claim`}
                onClick={() => run("claim", {})}
              >
                领取复核
              </Button>
              <Button
                type="primary"
                disabled={!isInReview || busyAction !== null}
                loading={busyAction === `review:${review.review_id}:approve`}
                onClick={handleApprove}
              >
                通过
              </Button>
              <Button
                danger
                disabled={!isInReview || busyAction !== null}
                loading={busyAction === `review:${review.review_id}:reject`}
                onClick={handleReject}
              >
                驳回（退回返工）
              </Button>
              <Button
                disabled={!isInReview || busyAction !== null}
                loading={busyAction === `review:${review.review_id}:escalate`}
                onClick={handleEscalate}
              >
                升级仲裁
              </Button>
            </Space>
            <Checkbox checked={checked} onChange={(event) => setChecked(event.target.checked)}>
              我已核对 Schema 快照、标签完整性、证据与职责分离
            </Checkbox>
            <Space wrap>
              <Input
                placeholder="问题码（驳回必填）"
                value={issueCode}
                onChange={(event) => setIssueCode(event.target.value)}
                style={{ width: 180 }}
              />
              <Select<ReviewIssueSeverity>
                value={issueSeverity}
                options={REVIEW_ISSUE_SEVERITY_VALUES.map((value) => ({
                  value,
                  label: value,
                }))}
                onChange={setIssueSeverity}
                style={{ width: 130 }}
              />
              <Input
                placeholder="问题说明（驳回必填）"
                value={issueMessage}
                onChange={(event) => setIssueMessage(event.target.value)}
                style={{ width: 260 }}
              />
            </Space>
            <Input
              placeholder="复核结论原因 / 升级仲裁原因"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Typography.Text type="secondary">
              复核人必须是标注人之外的项目成员；服务端校验失败会返回 403
              annotation_review_self_forbidden。
            </Typography.Text>
          </Space>
        ) : null}
      </Space>
    </Card>
  );
}

/** 复核面板：创建复核与逐轮复核动作；职责分离由服务端强制。 */
export default function ReviewPanel({
  submission,
  reviews,
  candidates,
  currentUserId,
  candidatesDegraded = false,
  busyAction,
  onCreate,
  onAction,
}: ReviewPanelProps) {
  // 默认以当前用户作为复核人：复核人去队列里"领取"自己那一条，而不是
  // 让操作者去别处复制 UUID。当前用户就是标注人时不能预填（服务端会拒），
  // 但保留候选列表让操作者改派给其他成员。
  const selfIsAnnotator = currentUserId === submission.annotator_id;
  const defaultReviewer = selfIsAnnotator ? "" : (currentUserId ?? "");
  const [reviewerId, setReviewerId] = useState(defaultReviewer);
  const canCreate = submission.status === "SUBMITTED";
  const reviewerOptions = candidates
    .filter((member) => member.user_id !== submission.annotator_id)
    .map((member) => ({
      value: member.user_id,
      label: `${member.display_name}（${member.user_id}）`,
    }));

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Card size="small" title="创建复核">
        <Space direction="vertical" size={8} className="ddp-stack">
          {!canCreate ? (
            <Alert
              type="info"
              showIcon
              message="当前提交状态不可创建复核"
              description="只有 SUBMITTED 提交可以创建复核；请先由标注人提交。"
            />
          ) : null}
          <Space wrap>
            <AutoComplete
              aria-label="复核人"
              id="annotation-reviewer-id"
              placeholder="复核人 UUID（默认当前用户）"
              value={reviewerId}
              options={reviewerOptions}
              onChange={setReviewerId}
              style={{ width: 330 }}
              disabled={!canCreate || busyAction !== null}
            />
            <Button
              type="primary"
              disabled={!canCreate || reviewerId.trim() === "" || busyAction !== null}
              loading={busyAction === "review:create"}
              onClick={() => onCreate(reviewerId.trim())}
            >
              创建复核
            </Button>
          </Space>
          <Typography.Text type="secondary">
            {candidatesDegraded
              ? "身份或成员列表读取失败，当前只能手填复核人 UUID。"
              : null}
            {selfIsAnnotator
              ? "当前用户就是本提交的标注人，服务端会拒绝自审；请选择其他成员。"
              : null}
            {!candidatesDegraded && !selfIsAnnotator
              ? `复核人不得是标注人（${submission.annotator_id}）；服务端会再次校验。`
              : null}
          </Typography.Text>
        </Space>
      </Card>
      {reviews.length === 0 ? (
        <PageState
          compact
          variant="empty"
          title="暂无复核记录"
          description="提交后可由具备权限的复核人创建复核。"
        />
      ) : (
        reviews.map((review) => (
          <ReviewCard
            key={review.review_id}
            review={review}
            busyAction={busyAction}
            onAction={onAction}
          />
        ))
      )}
    </Space>
  );
}
