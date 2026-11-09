import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Form,
  Input,
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
  presentPriority,
  presentRequirementStatus,
  presentSourceType,
} from "../../services/modelopsPresentation";
import {
  requirementDetailPath,
  requirementListPath,
} from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DataRequirement } from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function SatisfyModal({
  projectId,
  requirement,
  open,
  onClose,
  onDone,
}: {
  projectId: string;
  requirement: DataRequirement;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<{ dataset_version_ids: string[]; evidence: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.actRequirement(projectId, requirement.requirement_id, "satisfy", {
        version: requirement.version,
        dataset_version_ids: values.dataset_version_ids,
        satisfaction_evidence_ref: values.evidence || null,
      });
      messageApi.success("需求已标记满足，请继续关联复测评测。");
      form.resetFields();
      onDone();
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
        title="标记需求满足"
        open={open}
        okText="确认满足"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Typography.Paragraph type="secondary">
          只能关联已冻结（FROZEN）的数据集版本；服务端会逐一校验归属与有效性。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="dataset_version_ids"
            label="数据集版本 ID"
            rules={[{ required: true, message: "至少关联一个冻结版本" }]}
          >
            <Select
              mode="tags"
              tokenSeparators={[",", " "]}
              placeholder="粘贴冻结版本 UUID，回车确认"
            />
          </Form.Item>
          <Form.Item name="evidence" label="满足证据引用（可选）">
            <Input placeholder="对象存储键或验收记录编号" maxLength={1024} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function LinkRetestModal({
  projectId,
  requirement,
  open,
  onClose,
  onDone,
}: {
  projectId: string;
  requirement: DataRequirement;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<{ evaluation_id: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.actRequirement(projectId, requirement.requirement_id, "link-retest", {
        version: requirement.version,
        evaluation_id: values.evaluation_id,
      });
      messageApi.success("已关联复测评测");
      form.resetFields();
      onDone();
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
        title="关联复测评测"
        open={open}
        okText="关联"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="evaluation_id"
            label="复测评测 ID（UUID）"
            rules={[
              { required: true, message: "请输入评测 ID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function RequirementDetailContent({
  projectId,
  requirementId,
}: {
  projectId: string;
  requirementId: string;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [satisfyOpen, setSatisfyOpen] = useState(false);
  const [retestOpen, setRetestOpen] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => modelOpsApi.getRequirement(projectId, requirementId, { signal }),
    [projectId, requirementId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, payload: { reason: string | null }) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await modelOpsApi.actRequirement(projectId, requirementId, action, {
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
    body = <PageState variant="loading" title="正在加载数据需求" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const requirement: DataRequirement = data;
    const canSatisfy = requirement.allowed_actions.includes("satisfy");
    const canLinkRetest = requirement.allowed_actions.includes("link-retest");
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
            { key: "code", label: "编码", children: requirement.code },
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={requirement.status} presentation={presentRequirementStatus} />
              ),
            },
            {
              key: "priority",
              label: "优先级",
              children: <StatusTag value={requirement.priority} presentation={presentPriority} />,
            },
            {
              key: "source",
              label: "来源",
              children: presentSourceType(requirement.source_type),
            },
            {
              key: "owner",
              label: "负责人",
              children: <Typography.Text copyable>{requirement.owner_id}</Typography.Text>,
            },
            {
              key: "target",
              label: "目标 Episode",
              children: requirement.target_episode_count,
            },
            {
              key: "due",
              label: "截止时间",
              children: formatDateTime(requirement.due_at),
            },
            {
              key: "satisfied",
              label: "满足时间",
              children: formatDateTime(requirement.satisfied_at),
            },
            {
              key: "description",
              label: "说明",
              span: 2,
              children: requirement.description ?? "—",
            },
            {
              key: "evidence",
              label: "满足证据",
              span: 2,
              children: requirement.satisfaction_evidence_ref ?? "—",
            },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          缺口维度
        </Typography.Title>
        <DimensionTable dimensions={requirement.dimensions} />
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          关联数据集版本
        </Typography.Title>
        {requirement.linked_dataset_version_ids.length > 0 ? (
          <Space direction="vertical">
            {requirement.linked_dataset_version_ids.map((id) => (
              <Typography.Text key={id} copyable code>
                {id}
              </Typography.Text>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">尚未关联冻结数据集版本。</Typography.Text>
        )}
        <Typography.Title level={5} style={{ margin: 0 }}>
          复测评测
        </Typography.Title>
        {requirement.retest_evaluation_ids.length > 0 ? (
          <Space direction="vertical">
            {requirement.retest_evaluation_ids.map((id) => (
              <Link key={id} to={`/evaluations/${id}`}>
                {id}
              </Link>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">尚未关联复测评测。</Typography.Text>
        )}
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            需求动作
          </Typography.Title>
          <Space wrap>
            <ActionBar
              actions={requirement.allowed_actions}
              busy={busy}
              skipActions={["satisfy", "link-retest"]}
              onAction={act}
            />
            {canSatisfy ? (
              <Button type="primary" onClick={() => setSatisfyOpen(true)}>
                标记满足
              </Button>
            ) : null}
            {canLinkRetest ? (
              <Button onClick={() => setRetestOpen(true)}>关联复测</Button>
            ) : null}
          </Space>
          <Typography.Text type="secondary">
            批准与关闭要求项目负责人权限；提交人不能自批，越权请求由服务端返回 403 并写入审计。
          </Typography.Text>
        </Space>
        <SatisfyModal
          projectId={projectId}
          requirement={requirement}
          open={satisfyOpen}
          onClose={() => setSatisfyOpen(false)}
          onDone={reload}
        />
        <LinkRetestModal
          projectId={projectId}
          requirement={requirement}
          open={retestOpen}
          onClose={() => setRetestOpen(false)}
          onDone={reload}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="数据需求详情"
      description="反馈 → 需求 → 满足 → 复测 → 关闭 的闭环状态由服务端状态机驱动。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(requirementListPath())}>
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
        当前页面：<Link to={requirementDetailPath(requirementId)}>{requirementId}</Link>
      </Typography.Paragraph>
    </PageContainer>
  );
}

export default function RequirementDetailPage() {
  const projectId = useProjectId();
  const { requirementId } = useParams<{ requirementId: string }>();
  if (!projectId || !requirementId) {
    return (
      <PageContainer title="数据需求详情" description="数据需求必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据需求详情" />
      </PageContainer>
    );
  }
  return (
    <RequirementDetailContent
      key={`${projectId}:${requirementId}`}
      projectId={projectId}
      requirementId={requirementId}
    />
  );
}
