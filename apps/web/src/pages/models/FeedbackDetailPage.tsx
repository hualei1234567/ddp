import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/modelops/ActionBar";
import DimensionTable from "../../components/modelops/DimensionTable";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { modelOpsApi } from "../../services/modelops";
import {
  presentFeedbackStatus,
  presentIssueType,
  presentPriority,
  presentSeverity,
} from "../../services/modelopsPresentation";
import {
  feedbackDetailPath,
  feedbackListPath,
  requirementDetailPath,
} from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { ModelFeedback } from "../../types/modelops";

const P0_P3 = ["P0", "P1", "P2", "P3"] as const;

interface ConvertFormValues {
  requirement_code: string;
  requirement_title?: string;
  target_episode_count?: number;
  priority: string;
  due_at?: string;
}

function ConvertRequirementModal({
  projectId,
  feedback,
  open,
  onClose,
  onConverted,
}: {
  projectId: string;
  feedback: ModelFeedback;
  open: boolean;
  onClose: () => void;
  onConverted: (requirementId: string) => void;
}) {
  const [form] = Form.useForm<ConvertFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const updated = await modelOpsApi.actFeedback(
        projectId,
        feedback.feedback_id,
        "create-requirement",
        {
          version: feedback.version,
          requirement_code: values.requirement_code,
          requirement_title: values.requirement_title ?? feedback.description.slice(0, 200),
          target_episode_count: values.target_episode_count ?? null,
          priority: values.priority,
          due_at: values.due_at ? new Date(values.due_at).toISOString() : null,
        },
      );
      messageApi.success("已生成数据需求（SUBMITTED 前置状态由服务端决定）");
      if (updated.converted_requirement_id) {
        onConverted(updated.converted_requirement_id);
      }
      form.resetFields();
      onClose();
    } catch (caught) {
      if (caught instanceof Error) {
        messageApi.error(caught.message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title="转为数据需求"
        open={open}
        okText="生成需求"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="requirement_code"
            label="需求编码"
            rules={[{ required: true, max: 64, message: "请输入 1-64 字符编码" }]}
          >
            <Input placeholder="DR-KITCHEN-001" />
          </Form.Item>
          <Form.Item name="requirement_title" label="需求标题">
            <Input maxLength={200} placeholder="默认取反馈描述" />
          </Form.Item>
          <Form.Item
            name="target_episode_count"
            label="目标 Episode 数"
            initialValue={
              feedback.dimensions.reduce(
                (sum, item) =>
                  sum +
                  Math.max(0, (item.target_count ?? 0) - (item.current_count ?? 0)),
                0,
              ) || 1
            }
          >
            <InputNumber min={1} style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue="P1">
            <Select
              options={P0_P3.map((value) => ({
                value,
                label: presentPriority(value).label,
              }))}
            />
          </Form.Item>
          <Form.Item name="due_at" label="期望完成时间（可选）">
            <Input placeholder="2026-12-01T00:00:00Z" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function FeedbackDetailContent({
  projectId,
  feedbackId,
}: {
  projectId: string;
  feedbackId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [convertOpen, setConvertOpen] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => modelOpsApi.getFeedback(projectId, feedbackId, { signal }),
    [projectId, feedbackId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, payload: { reason: string | null }) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await modelOpsApi.actFeedback(projectId, feedbackId, action, {
        version: data.version,
        reason: payload.reason,
      });
      reload();
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载反馈" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const feedback: ModelFeedback = data;
    const canConvert = feedback.allowed_actions.includes("create-requirement");
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
        <Descriptions
          size="small"
          column={2}
          items={[
            {
              key: "issue",
              label: "问题类型",
              children: presentIssueType(feedback.issue_type),
            },
            {
              key: "severity",
              label: "严重程度",
              children: (
                <StatusTag value={feedback.severity} presentation={presentSeverity} />
              ),
            },
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={feedback.status} presentation={presentFeedbackStatus} />
              ),
            },
            {
              key: "model",
              label: "模型版本",
              children: <Typography.Text copyable>{feedback.model_version_id}</Typography.Text>,
            },
            {
              key: "evaluation",
              label: "关联评测",
              children: feedback.evaluation_id
                ? <Link to={`/evaluations/${feedback.evaluation_id}`}>{feedback.evaluation_id}</Link>
                : "—",
            },
            {
              key: "task",
              label: "关联推理任务",
              children: feedback.inference_task_id ?? "—",
            },
            {
              key: "requirement",
              label: "已转需求",
              children: feedback.converted_requirement_id ? (
                <Link to={requirementDetailPath(feedback.converted_requirement_id)}>
                  查看数据需求
                </Link>
              ) : (
                "—"
              ),
            },
            {
              key: "updated",
              label: "更新时间",
              children: formatDateTime(feedback.updated_at),
            },
            {
              key: "description",
              label: "问题描述",
              span: 2,
              children: feedback.description,
            },
            {
              key: "evidence",
              label: "证据引用",
              span: 2,
              children:
                feedback.evidence_refs.length > 0
                  ? feedback.evidence_refs.map((item) => (
                      <Typography.Text key={item} code>
                        {item}
                      </Typography.Text>
                    ))
                  : "—",
            },
            {
              key: "resolution",
              label: "处理说明",
              span: 2,
              children: feedback.resolution_note ?? "—",
            },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          缺口维度
        </Typography.Title>
        <DimensionTable dimensions={feedback.dimensions} />
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            反馈动作
          </Typography.Title>
          <Space wrap>
            <ActionBar
              actions={feedback.allowed_actions}
              busy={busy}
              skipActions={["create-requirement"]}
              onAction={act}
            />
            {canConvert ? (
              <Button type="primary" onClick={() => setConvertOpen(true)}>
                转为数据需求
              </Button>
            ) : null}
          </Space>
        </Space>
        <ConvertRequirementModal
          projectId={projectId}
          feedback={feedback}
          open={convertOpen}
          onClose={() => setConvertOpen(false)}
          onConverted={(requirementId) => {
            reload();
            navigate(requirementDetailPath(requirementId));
          }}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="模型反馈详情"
      description={`反馈 ${feedbackId}`}
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(feedbackListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
        当前页面：<Link to={feedbackDetailPath(feedbackId)}>{feedbackId}</Link>
      </Typography.Paragraph>
    </PageContainer>
  );
}

export default function FeedbackDetailPage() {
  const projectId = useProjectId();
  const { feedbackId } = useParams<{ feedbackId: string }>();
  if (!projectId || !feedbackId) {
    return (
      <PageContainer title="模型反馈详情" description="反馈必须按项目隔离展示。">
        <ProjectScopeRequired scope="模型反馈详情" />
      </PageContainer>
    );
  }
  return (
    <FeedbackDetailContent
      key={`${projectId}:${feedbackId}`}
      projectId={projectId}
      feedbackId={feedbackId}
    />
  );
}
