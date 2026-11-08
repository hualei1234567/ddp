import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from "antd";

import { formatDateTime } from "../../services/format";
import type {
  AnnotationRecord,
  AnnotationSubmission,
} from "../../types/annotation";
import IdentifierValue from "../ui/IdentifierValue";
import AnnotationStatusTag from "./AnnotationStatusTag";

interface SubmissionSummaryProps {
  submission: AnnotationSubmission;
  records: AnnotationRecord[];
  busyAction: string | null;
  onSubmit: () => void;
  onWithdraw: () => void;
}

function formatLabelValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value);
}

export default function SubmissionSummary({
  submission,
  records,
  busyAction,
  onSubmit,
  onWithdraw,
}: SubmissionSummaryProps) {
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="提交 ID">
          <IdentifierValue value={submission.submission_id} label="提交 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <AnnotationStatusTag value={submission.status} />
        </Descriptions.Item>
        <Descriptions.Item label="版本">
          v{submission.submission_version}
        </Descriptions.Item>
        <Descriptions.Item label="任务">
          <IdentifierValue value={submission.task_id} label="任务 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="分配">
          <IdentifierValue value={submission.assignment_id} label="分配 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="标注人">
          <IdentifierValue value={submission.annotator_id} label="标注人 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="Schema">
          v{submission.schema_version}
        </Descriptions.Item>
        <Descriptions.Item label="记录数">{submission.item_count}</Descriptions.Item>
        <Descriptions.Item label="数据来源">
          <Tag>{submission.data_source}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Schema Hash">
          <Typography.Text code>
            {submission.schema_sha256.slice(0, 22)}…
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="记录摘要">
          <Typography.Text code>
            {submission.records_digest.slice(0, 22)}…
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="提交时间">
          {formatDateTime(submission.submitted_at)}
        </Descriptions.Item>
        <Descriptions.Item label="结论时间">
          {formatDateTime(submission.decided_at)}
        </Descriptions.Item>
        <Descriptions.Item label="替换版本">
          <IdentifierValue value={submission.supersedes_id} label="旧提交 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="原因">
          {submission.reason ?? "—"}
        </Descriptions.Item>
      </Descriptions>

      {submission.status === "DRAFT" ? (
        <Space wrap>
          <Button
            type="primary"
            disabled={busyAction !== null}
            loading={busyAction === "submission:submit"}
            onClick={onSubmit}
          >
            提交该版本
          </Button>
          <Button
            danger
            disabled={busyAction !== null}
            loading={busyAction === "submission:withdraw"}
            onClick={onWithdraw}
          >
            撤回草稿
          </Button>
        </Space>
      ) : null}

      {submission.review_id ? (
        <Alert
          type="info"
          showIcon
          message="当前复核"
          description={<IdentifierValue value={submission.review_id} label="复核 ID" />}
        />
      ) : null}
      {submission.arbitration_id ? (
        <Alert
          type="warning"
          showIcon
          message="当前仲裁"
          description={
            <IdentifierValue value={submission.arbitration_id} label="仲裁 ID" />
          }
        />
      ) : null}

      {records.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="未加载标注记录"
          description="提交条目存在但未返回记录；请刷新或检查服务端记录接口。页面不会用空值冒充记录内容。"
        />
      ) : (
        <Space direction="vertical" size={10} className="ddp-stack">
          {records.map((record) => (
            <Card
              key={record.annotation_id}
              size="small"
              title={
                <Space wrap>
                  <IdentifierValue value={record.episode_id} label="Episode ID" />
                  <Tag>{record.status}</Tag>
                </Space>
              }
            >
              <Space direction="vertical" size={4} className="ddp-stack">
                <Typography.Text type="secondary">
                  Episode v{record.episode_version} · 记录 v
                  {record.annotation_version}
                </Typography.Text>
                {record.time_range ? (
                  <Typography.Text type="secondary">
                    记录时间范围：{JSON.stringify(record.time_range)}
                  </Typography.Text>
                ) : null}
                {record.labels.map((label) => (
                  <div key={label.label_key}>
                    <Typography.Text code>{label.label_key}</Typography.Text>
                    <Typography.Text> {formatLabelValue(label.value)}</Typography.Text>
                    <Tag>{label.source}</Tag>
                    <Tag>{label.label_status}</Tag>
                  </div>
                ))}
                {record.evidence_refs.length > 0 ? (
                  <Typography.Text type="secondary">
                    证据 {record.evidence_refs.length} 项
                  </Typography.Text>
                ) : null}
              </Space>
            </Card>
          ))}
        </Space>
      )}
    </Space>
  );
}
