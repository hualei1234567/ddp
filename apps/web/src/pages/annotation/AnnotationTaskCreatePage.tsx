import { ArrowLeftOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Typography,
} from "antd";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import AnnotationFeedbackAlert from "../../components/annotation/AnnotationFeedbackAlert";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
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
import {
  buildAnnotationTaskDetailPath,
  buildAnnotationTaskListPath,
} from "../../services/annotationUrl";
import type {
  AnnotationSampleSet,
  AnnotationSchema,
  AnnotationTaskCreateInput,
  TaskPriority,
} from "../../types/annotation";
import { TASK_PRIORITY_VALUES } from "../../types/annotation";

interface CreateFormValues {
  code: string;
  name: string;
  description?: string;
  sample_set_id: string;
  schema_id: string;
  target_item_count: number;
  target_episode_count: number;
  priority: TaskPriority;
  assignment_mode: string;
  due_at?: string;
}

function toIsoOrNull(value: string | undefined): string | null {
  const text = value?.trim() ?? "";
  if (text === "") {
    return null;
  }
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) {
    throw new Error("截止时间不是合法的日期时间");
  }
  return new Date(parsed).toISOString();
}

function TaskCreateContent({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const [form] = Form.useForm<CreateFormValues>();
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadOptions = useCallback(
    async (signal: AbortSignal) => {
      const [schemas, sampleSets] = await Promise.all([
        annotationApi.listSchemas(projectId, { signal }),
        annotationApi.listSampleSets(projectId, { signal }),
      ]);
      return {
        schemas: schemas.items.filter((item) => item.status === "PUBLISHED"),
        sampleSets: sampleSets.items.filter(
          (item) => item.status === "SNAPSHOTTED",
        ),
      };
    },
    [projectId],
  );
  const { data, error, loading, refreshing, stale, reload } =
    useRequest(loadOptions);

  const schemas = useMemo<AnnotationSchema[]>(
    () => data?.schemas ?? [],
    [data],
  );
  const sampleSets = useMemo<AnnotationSampleSet[]>(
    () => data?.sampleSets ?? [],
    [data],
  );
  const cannotCreateReason =
    data && schemas.length === 0
      ? "没有可用的已发布 Schema；请先发布 Schema 后再创建任务。"
      : data && sampleSets.length === 0
        ? "没有已快照的样本集；请先完成样本集快照。"
        : null;

  const handleSubmit = useCallback(
    async (values: CreateFormValues) => {
      setSubmitting(true);
      setFeedback(null);
      try {
        const schema = schemas.find((item) => item.schema_id === values.schema_id);
        const input: AnnotationTaskCreateInput = {
          code: values.code.trim(),
          name: values.name.trim(),
          description: values.description?.trim() || null,
          sample_set_id: values.sample_set_id,
          schema_id: values.schema_id,
          schema_version: schema?.schema_version ?? null,
          target_item_count: values.target_item_count,
          target_episode_count: values.target_episode_count,
          due_at: toIsoOrNull(values.due_at),
          priority: values.priority,
          assignment_mode: values.assignment_mode,
        };
        const task = await annotationApi.createTask(projectId, input);
        setFeedback(annotationSuccess("任务已创建，当前为 DRAFT。"));
        navigate(buildAnnotationTaskDetailPath(projectId, task.task_id));
      } catch (caught) {
        setFeedback(describeAnnotationActionError(caught));
      } finally {
        setSubmitting(false);
      }
    },
    [navigate, projectId, schemas],
  );

  const extra = (
    <Button
      icon={<ArrowLeftOutlined />}
      onClick={() => navigate(buildAnnotationTaskListPath(projectId))}
    >
      返回任务列表
    </Button>
  );

  if (loading) {
    return (
      <PageContainer title="新建标注任务" extra={extra}>
        <PageState
          variant="loading"
          title="正在加载 Schema 与样本集"
          description="创建任务前需要读取已发布 Schema 与已快照样本集。"
        />
      </PageContainer>
    );
  }
  if (error) {
    return (
      <PageContainer title="新建标注任务" extra={extra}>
        <ApiErrorState error={error} onRetry={reload} />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title="新建标注任务"
      description="绑定已发布 Schema 与已快照样本集创建任务。创建后任务为 DRAFT，审批与就绪由服务端状态机控制。"
      extra={extra}
    >
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，当前展示的是上次成功获取的选项。"
            onRetry={reload}
          />
        ) : null}
        <AnnotationFeedbackAlert feedback={feedback} />
        {cannotCreateReason ? (
          <Alert type="warning" showIcon message="暂不可创建任务" description={cannotCreateReason} />
        ) : null}
        <Form<CreateFormValues>
          form={form}
          layout="vertical"
          initialValues={{
            priority: "P1",
            assignment_mode: "MANUAL",
            target_item_count: 1,
            target_episode_count: 1,
          }}
          onFinish={(values) => void handleSubmit(values)}
        >
          <Form.Item
            label="任务编码"
            name="code"
            rules={[{ required: true, message: "请输入任务编码" }]}
          >
            <Input maxLength={64} placeholder="例如 AN-TASK-001" />
          </Form.Item>
          <Form.Item
            label="任务名称"
            name="name"
            rules={[{ required: true, message: "请输入任务名称" }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea maxLength={2000} rows={2} />
          </Form.Item>
          <Form.Item
            label="标注 Schema"
            name="schema_id"
            rules={[{ required: true, message: "请选择已发布 Schema" }]}
            extra={
              schemas.length === 0
                ? "没有可用 Schema。"
                : "只列出 PUBLISHED Schema；任务创建时冻结快照。"
            }
          >
            <Select
              options={schemas.map((schema) => ({
                value: schema.schema_id,
                label: `${schema.code} · ${schema.name} · v${schema.schema_version}`,
              }))}
              disabled={schemas.length === 0}
            />
          </Form.Item>
          <Form.Item
            label="样本集"
            name="sample_set_id"
            rules={[{ required: true, message: "请选择已快照样本集" }]}
            extra={
              sampleSets.length === 0
                ? "没有可用的已快照样本集。"
                : "样本集快照后清单不可静默变化。"
            }
          >
            <Select
              options={sampleSets.map((sampleSet) => ({
                value: sampleSet.sample_set_id,
                label: `${sampleSet.code} · 已选 ${sampleSet.selected_count}/${sampleSet.candidate_count}`,
              }))}
              disabled={sampleSets.length === 0}
            />
          </Form.Item>
          <Form.Item label="任务 ID" extra={<Typography.Text type="secondary">由服务端生成</Typography.Text>}>
            <IdentifierValue value={null} label="任务 ID" />
          </Form.Item>
          <Space wrap size={16}>
            <Form.Item
              label="目标项数"
              name="target_item_count"
              rules={[{ required: true, message: "请输入目标项数" }]}
            >
              <InputNumber min={1} precision={0} style={{ width: 160 }} />
            </Form.Item>
            <Form.Item
              label="目标 Episode 数"
              name="target_episode_count"
              rules={[{ required: true, message: "请输入目标 Episode 数" }]}
            >
              <InputNumber min={1} precision={0} style={{ width: 180 }} />
            </Form.Item>
            <Form.Item label="优先级" name="priority">
              <Select
                options={TASK_PRIORITY_VALUES.map((value) => ({ value, label: value }))}
                style={{ width: 100 }}
              />
            </Form.Item>
            <Form.Item label="分配模式" name="assignment_mode">
              <Select
                options={[
                  { value: "MANUAL", label: "人工分配" },
                  { value: "CLAIM", label: "领取" },
                  { value: "MIXED", label: "混合" },
                ]}
                style={{ width: 120 }}
              />
            </Form.Item>
          </Space>
          <Form.Item
            label="截止时间"
            name="due_at"
            extra="可选，ISO 日期时间或浏览器可解析的格式。"
            rules={[
              {
                validator: async (_rule, value: string | undefined) => {
                  if (!value || value.trim() === "") {
                    return;
                  }
                  if (Number.isNaN(Date.parse(value))) {
                    throw new Error("截止时间不是合法的日期时间");
                  }
                },
              },
            ]}
          >
            <Input placeholder="2026-11-30T18:00:00Z" />
          </Form.Item>
          <Space wrap>
            <Button
              type="primary"
              htmlType="submit"
              loading={submitting}
              disabled={cannotCreateReason !== null || refreshing}
            >
              创建任务
            </Button>
            <Button onClick={() => form.resetFields()}>重置</Button>
          </Space>
        </Form>
      </Space>
    </PageContainer>
  );
}

export default function AnnotationTaskCreatePage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="新建标注任务" description="标注任务必须按项目隔离创建。">
        <ProjectScopeRequired scope="标注任务创建" />
      </PageContainer>
    );
  }
  return <TaskCreateContent key={projectId} projectId={projectId} />;
}
