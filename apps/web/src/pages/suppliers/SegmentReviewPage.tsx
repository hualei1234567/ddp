import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Input,
  Radio,
  Space,
  Statistic,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import StatusTag from "../../components/supplier/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { supplierApi } from "../../services/suppliers";
import {
  diagnosticEntries,
  presentDecision,
  presentSegmentStatus,
} from "../../services/supplierPresentation";
import { batchDetailPath, segmentPath } from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { InboundSegment } from "../../types/supplier";

const DECISION_OPTIONS = [
  { value: "ACCEPT", label: "通过验收" },
  { value: "REJECT", label: "拒收" },
  { value: "NEED_MORE", label: "待补件" },
];

function formatDuration(value: number | null): string {
  if (value === null) {
    return "—";
  }
  const totalSeconds = Math.round(value / 1_000_000_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function DecisionPanel({
  projectId,
  segment,
  onDecided,
}: {
  projectId: string;
  segment: InboundSegment;
  onDecided: () => void;
}) {
  const [decision, setDecision] = useState("ACCEPT");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const canDecide = segment.status === "PENDING_REVIEW";
  const reasonRequired = decision !== "ACCEPT";

  const submit = async () => {
    if (reasonRequired && reason.trim() === "") {
      messageApi.warning("拒收或待补件必须填写原因");
      return;
    }
    try {
      setSubmitting(true);
      await supplierApi.decideSegment(projectId, segment.batch_id, segment.segment_id, {
        version: segment.version,
        decision,
        reason: reason.trim() || null,
      });
      messageApi.success("裁决已记录并写入核验账本");
      setReason("");
      onDecided();
    } catch (caught) {
      messageApi.error(caught instanceof Error ? caught.message : "裁决失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (!canDecide) {
    return (
      <Alert
        type="info"
        showIcon
        message="当前片段不可裁决"
        description={`片段状态为 ${presentSegmentStatus(segment.status).label}。只有「待审看」状态的片段可以裁决；已裁决结果不可修改。`}
      />
    );
  }

  return (
    <>
      {contextHolder}
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text strong>人工裁决</Typography.Text>
        <Radio.Group
          value={decision}
          onChange={(event) => setDecision(event.target.value)}
          options={DECISION_OPTIONS}
          optionType="button"
          buttonStyle="solid"
        />
        <Input.TextArea
          rows={3}
          maxLength={2000}
          value={reason}
          placeholder={reasonRequired ? "原因（必填）" : "备注（可选）"}
          onChange={(event) => setReason(event.target.value)}
        />
        <Button type="primary" loading={submitting} onClick={() => void submit()}>
          提交裁决
        </Button>
        <Typography.Text type="secondary">
          裁决人不得与片段登记人相同；裁决结果只追加、不可修改。
        </Typography.Text>
      </Space>
    </>
  );
}

function SegmentContent({
  projectId,
  batchId,
  segmentId,
}: {
  projectId: string;
  batchId: string;
  segmentId: string;
}) {
  const navigate = useNavigate();
  const load = useCallback(
    (signal: AbortSignal) =>
      supplierApi.getSegment(projectId, batchId, segmentId, { signal }),
    [projectId, batchId, segmentId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });
  const diagnostics = data ? diagnosticEntries(data.diagnostics) : [];
  const sendState = feedbackOf(data?.diagnostics);

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载片段" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const segment: InboundSegment = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <Space wrap>
          <StatusTag value={segment.status} presentation={presentSegmentStatus} />
          {segment.decision ? (
            <StatusTag value={segment.decision} presentation={presentDecision} />
          ) : null}
          <Tag>时长 {formatDuration(segment.duration_ns)}</Tag>
          {segment.raw_asset_id ? <Tag color="blue">已关联 RawAsset</Tag> : (
            <Tag color="orange">未关联 RawAsset</Tag>
          )}
        </Space>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 2fr) minmax(320px, 1fr)",
            gap: 16,
            alignItems: "start",
          }}
        >
          <Card size="small" title="画面工具">
            <div
              style={{
                height: 380,
                borderRadius: 8,
                background:
                  "repeating-linear-gradient(45deg, #f5f5f5, #f5f5f5 12px, #ececec 12px, #ececec 24px)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Typography.Text type="secondary">
                预览需接入供应商原始视频对象；DDP 只保存对象引用，不在页面内联大文件。
              </Typography.Text>
            </div>
            <Divider style={{ margin: "12px 0" }} />
            <Typography.Text type="secondary">
              {sendState.summary}
            </Typography.Text>
          </Card>

          <Card size="small" title="片段语义与初筛诊断">
            <Descriptions
              size="small"
              column={1}
              items={[
                { key: "task", label: "任务", children: segment.task_name ?? "—" },
                {
                  key: "code",
                  label: "片段编码",
                  children: segment.segment_code,
                },
                {
                  key: "registered",
                  label: "登记人",
                  children: segment.registered_by ?? "—",
                },
                {
                  key: "decided",
                  label: "裁决",
                  children: segment.decision
                    ? `${presentDecision(segment.decision).label} · ${formatDateTime(
                        segment.decided_at,
                      )}`
                    : "尚未裁决",
                },
              ]}
            />
            <Divider style={{ margin: "12px 0" }} />
            <Typography.Text strong>诊断快照（只读）</Typography.Text>
            {diagnostics.length > 0 ? (
              <Descriptions
                size="small"
                column={1}
                style={{ marginTop: 8 }}
                items={diagnostics.map(([key, value]) => ({
                  key,
                  label: key,
                  children: value,
                }))}
              />
            ) : (
              <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                供应商未提供诊断快照；DDP 不推断画面结论。
              </Typography.Paragraph>
            )}
            <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
              初筛仅作参考，放行以人工裁决为准。
            </Typography.Paragraph>
          </Card>
        </div>

        <Card size="small" title="到货人工验收裁决">
          <Space size={24} wrap style={{ marginBottom: 12 }}>
            <Statistic title="已裁决" value={segment.decision ? 1 : 0} suffix="/ 1" />
            <Statistic
              title="裁决人"
              value={segment.decided_by ? "已签名" : "未签名"}
            />
          </Space>
          {segment.decision_reason ? (
            <Typography.Paragraph>
              裁决说明：{segment.decision_reason}
            </Typography.Paragraph>
          ) : null}
          <DecisionPanel
            projectId={projectId}
            segment={segment}
            onDecided={() => void reload()}
          />
        </Card>
      </Space>
    );
  }

  return (
    <PageContainer
      title="片段审片"
      description="对照画面与诊断快照逐条裁决；结果写入只追加的验收核验账本。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(batchDetailPath(batchId))}>
            返回批次
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        当前页面：{segmentPath(batchId, segmentId)}
      </Typography.Paragraph>
    </PageContainer>
  );
}

function feedbackOf(diagnostics: Record<string, unknown> | undefined): { summary: string } {
  if (!diagnostics) {
    return { summary: "尚未加载诊断信息。" };
  }
  const summary = diagnostics.vlm_summary;
  if (typeof summary === "string" && summary.trim() !== "") {
    return { summary: `初筛结论：${summary}` };
  }
  return { summary: "供应商未提供初筛结论，请人工判断。" };
}

export default function SegmentReviewPage() {
  const projectId = useProjectId();
  const { batchId, segmentId } = useParams<{ batchId: string; segmentId: string }>();
  if (!projectId || !batchId || !segmentId) {
    return (
      <PageContainer title="片段审片" description="片段必须按项目隔离展示。">
        <ProjectScopeRequired scope="片段审片" />
      </PageContainer>
    );
  }
  return (
    <SegmentContent
      key={`${projectId}:${batchId}:${segmentId}`}
      projectId={projectId}
      batchId={batchId}
      segmentId={segmentId}
    />
  );
}
