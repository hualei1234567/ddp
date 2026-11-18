import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { ApiError } from "../../services/api";
import { dataSourceApi } from "../../services/datasources";
import { presentCamera } from "../../services/datasourceParsers";
import { formatDateTime } from "../../services/format";
import type {
  DataReview,
  QualityReport,
  ReviewDecision,
  ReviewEvidence,
} from "../../types/datasource";
import { buildReviewEvidence, type ReviewFormValues } from "./reviewEvidence";
import ImportQualityRunSection from "./ImportQualityRunSection";

/**
 * 导入结果的质量报告与人工审核页。
 *
 * 分工是刻意的：
 * - 质量报告由**服务端规则**判定（`rule_version` 随报告返回），前端只呈现结论；
 * - 审核结论由**人**给出，且审核人不得是导入提交人——职责分离由服务端与数据库
 *   双重约束，前端在这里只负责把"能不能审"如实显示出来，不做权限推断。
 */

const DECISION_LABELS: Record<ReviewDecision, string> = {
  ACCEPT: "通过",
  REJECT: "拒绝",
  NEEDS_FIX: "需修正",
};

const REVIEW_STATUS_LABELS: Record<DataReview["status"], string> = {
  PENDING: "待审核",
  APPROVED: "已通过",
  REJECTED: "已拒绝",
};

const REVIEW_STATUS_COLORS: Record<DataReview["status"], string> = {
  PENDING: "gold",
  APPROVED: "green",
  REJECTED: "red",
};

export interface ReviewEvidenceDisplayProps {
  evidence: ReviewEvidence | null;
}

/** 只展示服务端实际返回的证据字段；旧记录没有证据就不渲染空表。 */
export function ReviewEvidenceDetails({ evidence }: ReviewEvidenceDisplayProps) {
  if (!evidence) {
    return null;
  }
  const items: ReactNode[] = [];
  if (
    typeof evidence.timestamp_seconds === "number" &&
    Number.isFinite(evidence.timestamp_seconds)
  ) {
    items.push(
      <Descriptions.Item key="timestamp" label="关注时间点">
        {evidence.timestamp_seconds} 秒
      </Descriptions.Item>,
    );
  }
  if (evidence.camera) {
    items.push(
      <Descriptions.Item key="camera" label="机位">
        {presentCamera(evidence.camera)}
      </Descriptions.Item>,
    );
  }
  if (evidence.episode_key) {
    items.push(
      <Descriptions.Item key="episode" label="片段">
        {evidence.episode_key}
      </Descriptions.Item>,
    );
  }
  if (evidence.note) {
    items.push(
      <Descriptions.Item key="note" label="证据备注">
        {evidence.note}
      </Descriptions.Item>,
    );
  }
  if (items.length === 0) {
    return null;
  }
  return (
    <Descriptions size="small" column={1} bordered title="审核证据">
      {items}
    </Descriptions>
  );
}

export function QualitySection({
  report,
  loading,
  error,
  onReload,
}: {
  report: QualityReport | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
}) {
  // 统一状态组件：与全站其余页面一致，避免本页裸写 Spin/Alert/Empty
  // 导致六态覆盖不一致（DEV-004 审计点名项）。
  if (loading) {
    return <PageState variant="loading" title="正在加载质量报告…" compact />;
  }
  if (error) {
    return (
      <PageState
        variant="error"
        title="质量报告加载失败"
        description={error}
        onRetry={onReload}
        compact
      />
    );
  }
  if (!report) {
    return (
      <PageState
        variant="empty"
        title="尚无质量报告"
        description="导入完成后由服务端质量规则生成"
        compact
      />
    );
  }
  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Alert
        type={report.passed ? "success" : "warning"}
        showIcon
        message={
          report.passed
            ? `质量校验通过（规则版本 ${report.rule_version}）`
            : `质量校验未通过（规则版本 ${report.rule_version}）`
        }
        description={
          report.passed
            ? undefined
            : "未通过的规则如下；这些是服务端判定结果，不是前端推断。"
        }
      />
      <Descriptions size="small" column={3} bordered>
        <Descriptions.Item label="样本范围">
          {report.sample_scope}
        </Descriptions.Item>
        <Descriptions.Item label="片段数">
          {report.total_episodes}
        </Descriptions.Item>
        <Descriptions.Item label="资产数">
          {report.total_assets}
        </Descriptions.Item>
      </Descriptions>
      {report.failures.length > 0 ? (
        <Space direction="vertical" style={{ width: "100%" }} size={4}>
          <Typography.Text strong>未通过的规则</Typography.Text>
          {report.failures.map((finding) => (
            <Alert
              key={finding.rule_id}
              type="error"
              showIcon
              message={finding.rule_id}
              description={finding.detail}
            />
          ))}
        </Space>
      ) : null}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        共 {report.findings.length} 条规则，全部结果随报告返回，便于解释当时为何通过。
      </Typography.Text>
    </Space>
  );
}

export function ReviewSection({
  projectId,
  importId,
  review,
  loading,
  error,
  forbidden,
  onReload,
}: {
  projectId: string;
  importId: string;
  review: DataReview | null;
  loading: boolean;
  error: string | null;
  forbidden: boolean;
  onReload: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<ReviewFormValues>();

  const submit = useCallback(
    async (decision: ReviewDecision) => {
      if (!review) {
        return;
      }
      const values = form.getFieldsValue();
      setSubmitting(true);
      try {
        await dataSourceApi.decideReview(
          projectId,
          importId,
          {
            decision,
            reason: values.reason ?? null,
            evidence: buildReviewEvidence(values),
          },
          { version: review.version },
        );
        message.success(`已提交审核结论：${DECISION_LABELS[decision]}`);
        onReload();
      } catch (caught) {
        if (caught instanceof ApiError && caught.isConflict) {
          message.warning("审核记录已被他人更新，请刷新后重试");
          onReload();
        } else {
          message.error(caught instanceof Error ? caught.message : "提交失败");
        }
      } finally {
        setSubmitting(false);
      }
    },
    [form, importId, onReload, projectId, review],
  );

  if (loading) {
    return <PageState variant="loading" title="正在加载审核记录…" compact />;
  }
  if (error) {
    return (
      <PageState
        variant="error"
        title="审核记录加载失败"
        description={error}
        onRetry={onReload}
        compact
      />
    );
  }
  if (!review) {
    return (
      <PageState
        variant="empty"
        title="尚未创建审核记录"
        description="导入任务完成后可创建审核"
        compact
      />
    );
  }

  const decided = review.status !== "PENDING";

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Tag color={REVIEW_STATUS_COLORS[review.status]}>
          {REVIEW_STATUS_LABELS[review.status]}
        </Tag>
        {review.decision ? (
          <Tag>{DECISION_LABELS[review.decision]}</Tag>
        ) : null}
        <Typography.Text type="secondary">
          提交人 {review.submitted_by.slice(0, 8)}…
        </Typography.Text>
        {review.reviewer_id ? (
          <Typography.Text type="secondary">
            审核人 {review.reviewer_id.slice(0, 8)}…
          </Typography.Text>
        ) : null}
        {review.decided_at ? (
          <Typography.Text type="secondary">
            结论时间 {formatDateTime(review.decided_at)}
          </Typography.Text>
        ) : null}
      </Space>
      {review.reason ? (
        <Alert type="info" showIcon message="审核说明" description={review.reason} />
      ) : null}
      <ReviewEvidenceDetails evidence={review.evidence} />
      {decided ? (
        <Typography.Text type="secondary">
          该审核已有结论；如需重新审核，需由服务端开启新的审核轮次。
        </Typography.Text>
      ) : forbidden ? (
        <Alert
          type="warning"
          showIcon
          message="无审核权限"
          description="当前账号在该项目内没有审核权限，或本次导入由你提交（职责分离要求审核人不同于提交人）。"
        />
      ) : (
        <Form form={form} layout="vertical" disabled={submitting}>
          <Form.Item
            name="reason"
            label="审核说明（可选）"
            rules={[{ max: 2000, message: "说明不得超过 2000 字" }]}
          >
            <Input.TextArea rows={3} placeholder="记录判断依据，便于事后追溯" />
          </Form.Item>
          <Typography.Text strong>审核证据（可选）</Typography.Text>
          <Space wrap align="start">
            <Form.Item
              name="evidence_timestamp"
              label="时间点（秒）"
              rules={[
                { type: "number", min: 0, message: "时间点必须是非负数" },
              ]}
            >
              <InputNumber
                min={0}
                step={0.1}
                style={{ width: 180 }}
                aria-label="审核证据时间点（秒）"
                placeholder="如 69"
              />
            </Form.Item>
            <Form.Item
              name="evidence_camera"
              label="机位"
              rules={[{ max: 64, message: "机位标识不得超过 64 字符" }]}
            >
              <Input
                maxLength={64}
                style={{ width: 180 }}
                aria-label="审核证据机位"
                placeholder="如 main / right"
              />
            </Form.Item>
            <Form.Item
              name="evidence_note"
              label="证据备注"
              rules={[{ max: 500, message: "证据备注不得超过 500 字" }]}
            >
              <Input.TextArea
                rows={2}
                maxLength={500}
                style={{ width: 320 }}
                aria-label="审核证据备注"
                placeholder="记录具体画面、异常点等可核对的事实"
              />
            </Form.Item>
          </Space>
          <Space wrap>
            <Button
              type="primary"
              loading={submitting}
              onClick={() => void submit("ACCEPT")}
            >
              通过
            </Button>
            <Button
              loading={submitting}
              onClick={() => void submit("NEEDS_FIX")}
            >
              需修正
            </Button>
            <Button danger loading={submitting} onClick={() => void submit("REJECT")}>
              拒绝
            </Button>
          </Space>
        </Form>
      )}
    </Space>
  );
}

function ReviewContent({
  projectId,
  importId,
}: {
  projectId: string;
  importId: string;
}) {
  const [report, setReport] = useState<QualityReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [review, setReview] = useState<DataReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(true);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewForbidden, setReviewForbidden] = useState(false);

  const loadReport = useCallback(
    async (signal?: AbortSignal) => {
      setReportLoading(true);
      setReportError(null);
      try {
        setReport(await dataSourceApi.qualityReport(projectId, importId, { signal }));
      } catch (caught) {
        setReportError(caught instanceof Error ? caught.message : "加载失败");
      } finally {
        setReportLoading(false);
      }
    },
    [importId, projectId],
  );

  const loadReview = useCallback(
    async (signal?: AbortSignal) => {
      setReviewLoading(true);
      setReviewError(null);
      setReviewForbidden(false);
      try {
        // 先确保审核记录存在（服务端幂等），再读取当前状态。
        await dataSourceApi.ensureReview(projectId, importId, { signal });
        setReview(await dataSourceApi.getReview(projectId, importId, { signal }));
      } catch (caught) {
        if (caught instanceof ApiError && caught.isForbidden) {
          // 无审核/提交权限时，仍然要把现有审核状态显示出来，而不是整页报错。
          setReviewForbidden(true);
          setReview(
            await dataSourceApi
              .getReview(projectId, importId, { signal })
              .catch(() => null),
          );
        } else {
          setReviewError(caught instanceof Error ? caught.message : "加载失败");
        }
      } finally {
        setReviewLoading(false);
      }
    },
    [importId, projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadReport(controller.signal);
    void loadReview(controller.signal);
    return () => controller.abort();
  }, [loadReport, loadReview]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <ImportQualityRunSection projectId={projectId} importId={importId} />
      <Card
          title="导入质量报告（历史扁平规则）"
          extra={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => void loadReport()}
            >
              刷新
            </Button>
          }
        >
          <Typography.Text type="secondary">
            该区块为导入阶段的历史扁平规则结论，仅用于对照；六类分层结论以上方
            「统一质量检查」为准。
          </Typography.Text>
          <QualitySection
            report={report}
            loading={reportLoading}
            error={reportError}
            onReload={() => void loadReport()}
          />
        </Card>
        <Card
          title="人工审核"
          extra={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => void loadReview()}
            >
              刷新
            </Button>
          }
        >
          <ReviewSection
            projectId={projectId}
            importId={importId}
            review={review}
            loading={reviewLoading}
            error={reviewError}
            forbidden={reviewForbidden}
            onReload={() => void loadReview()}
          />
      </Card>
    </Space>
  );
}

export default function ImportReviewPage() {
  const projectId = useProjectId();
  const { importId } = useParams<{ importId: string }>();
  const [searchParams] = useSearchParams();
  const resolvedImportId = importId ?? searchParams.get("import_id") ?? "";

  if (!projectId) {
    return (
      <PageContainer title="质量报告与审核" description="导入结果必须按项目隔离展示。">
        <ProjectScopeRequired scope="导入审核" />
      </PageContainer>
    );
  }
  if (!resolvedImportId) {
    return (
      <PageContainer title="质量报告与审核">
        <PageState
          variant="empty"
          title="缺少导入任务 ID"
          description="请从导入任务列表进入"
        />
      </PageContainer>
    );
  }
  return (
    <PageContainer
      title="质量报告与审核"
      description="质量结论由服务端规则给出，审核结论由人给出；审核人不得是提交人。"
      extra={
        <Link to={`/data-source-imports/${resolvedImportId}/assets`}>
          <Button>查看视频</Button>
        </Link>
      }
    >
      <ReviewContent projectId={projectId} importId={resolvedImportId} />
    </PageContainer>
  );
}
