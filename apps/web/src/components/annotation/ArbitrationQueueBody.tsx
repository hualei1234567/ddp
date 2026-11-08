import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import type { ArbitrationQueueResult } from "../../services/annotationQueues";
import { buildAnnotationSubmissionPath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

interface ArbitrationQueueBodyProps {
  projectId: string;
  phase: PagePhase;
  data: ArbitrationQueueResult | null;
  error: ApiError | null;
  stale: boolean;
  partialErrors: ArbitrationQueueResult["partialErrors"];
  onRetry: () => void;
}

export default function ArbitrationQueueBody({
  projectId,
  phase,
  data,
  error,
  stale,
  partialErrors,
  onRetry,
}: ArbitrationQueueBodyProps) {
  if (phase === "forbidden" || phase === "not-found") {
    return error ? <ApiErrorState error={error} onRetry={onRetry} /> : null;
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载仲裁队列"
        description="正在读取已升级提交与仲裁记录。"
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
        title="暂无仲裁对象"
        description="该任务暂无 ESCALATED、ARBITRATING 或已出结论的提交。"
      />
    );
  }
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示上次成功获取的仲裁队列。"
          onRetry={onRetry}
        />
      ) : null}
      {partialErrors.length > 0 ? (
        <PageState
          compact
          variant="partial"
          title="部分提交的仲裁记录加载失败"
          description={partialErrors
            .map((item) => `${item.scope}（${item.error.code ?? "unknown"}）`)
            .join("；")}
          onRetry={onRetry}
        />
      ) : null}
      <Alert
        type="info"
        showIcon
        message="仲裁队列按任务聚合"
        description="后端没有独立的仲裁列表端点；页面先筛选升级/仲裁中的提交，再读取每个提交的仲裁记录。"
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
              查看 / 仲裁
            </Link>
          }
        >
          <Space direction="vertical" size={6} className="ddp-stack">
            <Typography.Text type="secondary">
              标注人 {item.submission.annotator_id} · 记录{" "}
              {item.submission.item_count} · 提交于{" "}
              {formatDateTime(item.submission.submitted_at)}
            </Typography.Text>
            {item.arbitrations.length === 0 ? (
              <Typography.Text type="secondary">
                尚未创建仲裁。进入提交详情后填写仲裁人、法定人数与触发原因。
              </Typography.Text>
            ) : (
              item.arbitrations.map((arbitration) => (
                <Space key={arbitration.arbitration_id} wrap>
                  <AnnotationStatusTag
                    value={arbitration.status}
                    prefix="仲裁"
                  />
                  <Tag>{arbitration.trigger}</Tag>
                  <Tag>
                    投票 {arbitration.effective_vote_count}/{arbitration.quorum}
                  </Tag>
                  {arbitration.final_decision ? (
                    <Tag color="purple">{arbitration.final_decision}</Tag>
                  ) : null}
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
