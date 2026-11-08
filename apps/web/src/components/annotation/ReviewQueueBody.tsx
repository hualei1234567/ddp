import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import {
  buildAnnotationSubmissionPath,
} from "../../services/annotationUrl";
import type { PagePhase } from "../../services/pagePhase";
import type { ReviewQueueResult } from "../../services/annotationQueues";
import { formatDateTime } from "../../services/format";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

interface ReviewQueueBodyProps {
  projectId: string;
  phase: PagePhase;
  data: ReviewQueueResult | null;
  error: ApiError | null;
  stale: boolean;
  partialErrors: ReviewQueueResult["partialErrors"];
  onRetry: () => void;
}

export default function ReviewQueueBody({
  projectId,
  phase,
  data,
  error,
  stale,
  partialErrors,
  onRetry,
}: ReviewQueueBodyProps) {
  if (phase === "forbidden" || phase === "not-found") {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载复核队列"
        description="正在读取任务提交与复核记录。"
      />
    );
  }
  if (phase === "error" || data === null) {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (data.items.length === 0) {
    return (
      <PageState
        variant="empty"
        title="暂无待复核提交"
        description="该任务尚未产生非草稿提交，或当前账号看不到相关提交。"
      />
    );
  }
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示上次成功获取的复核队列。"
          onRetry={onRetry}
        />
      ) : null}
      {partialErrors.length > 0 ? (
        <PageState
          compact
          variant="partial"
          title="部分提交的复核记录加载失败"
          description={partialErrors
            .map((item) => `${item.scope}（${item.error.code ?? "unknown"}）`)
            .join("；")}
          onRetry={onRetry}
        />
      ) : null}
      <Alert
        type="info"
        showIcon
        message="复核队列按任务聚合"
        description="后端没有独立的复核列表端点；页面先列出任务提交，再读取每个提交的复核记录。单个提交失败不影响其它行。"
      />
      {data.items.map((item) => (
        <Card
          key={item.submission.submission_id}
          size="small"
          title={
            <Space wrap>
              <AnnotationStatusTag value={item.submission.status} prefix="提交" />
              <span>v{item.submission.submission_version}</span>
              <IdentifierValue
                value={item.submission.submission_id}
                label="提交 ID"
              />
            </Space>
          }
          extra={
            <Link
              to={buildAnnotationSubmissionPath(
                projectId,
                item.submission.submission_id,
              )}
            >
              查看 / 复核
            </Link>
          }
        >
          <Space direction="vertical" size={6} className="ddp-stack">
            <Space wrap>
              <Tag>{item.submission.data_source}</Tag>
              <Typography.Text type="secondary">
                标注人 {item.submission.annotator_id} · 记录{" "}
                {item.submission.item_count} · 提交于{" "}
                {formatDateTime(item.submission.submitted_at)}
              </Typography.Text>
            </Space>
            {item.reviews.length === 0 ? (
              <Typography.Text type="secondary">
                尚未创建复核。进入提交详情后填写复核人并创建。
              </Typography.Text>
            ) : (
              item.reviews.map((review) => (
                <Space key={review.review_id} wrap>
                  <AnnotationStatusTag value={review.status} prefix="复核" />
                  <span>第 {review.review_round} 轮</span>
                  <IdentifierValue value={review.reviewer_id} label="复核人 ID" />
                  <Typography.Text type="secondary">
                    {review.reason ?? ""}
                  </Typography.Text>
                </Space>
              ))
            )}
          </Space>
        </Card>
      ))}
      <Button onClick={onRetry}>刷新队列</Button>
    </Space>
  );
}
