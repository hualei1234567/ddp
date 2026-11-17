import {
  Alert,
  Button,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Tabs,
  Typography,
} from "antd";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";

import { annotationApi } from "../../services/annotation";
import { fetchAllCursorPages } from "../../services/cursorPage";
import type {
  QualitySampleCreateRequest,
} from "../../services/annotationQualityApi";
import type {
  AnnotationSubmission,
  QualitySampleType,
} from "../../types/annotation";
import { QUALITY_SAMPLE_TYPE_VALUES } from "../../types/annotation";

interface PlanFormValues {
  submission_ids: string[];
  sample_size: number;
  sample_type: QualitySampleType;
  sampling_policy_version: string;
  sample_reason: string;
  stratum?: string | null;
  risk_threshold?: number | null;
  due_at?: Dayjs | null;
}

interface ManualFormValues {
  submission_id: string;
  sample_type: QualitySampleType;
  sampling_policy_version: string;
  sample_reason: string;
  stratum?: string | null;
  item_refs?: string[];
  due_at?: Dayjs | null;
}

interface QualitySampleCreateDrawerProps {
  open: boolean;
  projectId: string;
  taskId: string;
  onClose: () => void;
  onSubmit: (request: QualitySampleCreateRequest) => Promise<void>;
}

const sampleTypeOptions = QUALITY_SAMPLE_TYPE_VALUES.map((value) => ({
  value,
  label: value,
}));

function toIso(value: Dayjs | null | undefined): string | null {
  return value ? value.toISOString() : null;
}

function submissionOptions(submissions: AnnotationSubmission[]) {
  return submissions.map((submission) => ({
    value: submission.submission_id,
    label: `${submission.submission_id} · ${submission.annotator_id} · ${submission.status}`,
  }));
}

/**
 * 生成抽检抽屉：按计划批量抽样（`/quality-samples/plan`）或手工创建一条。
 *
 * 候选提交来自当前任务的提交列表；抽样策略版本必须显式填写，
 * 前端不预设策略版本，避免把某个版本当成默认业务规则。
 */
export default function QualitySampleCreateDrawer({
  open,
  projectId,
  taskId,
  onClose,
  onSubmit,
}: QualitySampleCreateDrawerProps) {
  const [planForm] = Form.useForm<PlanFormValues>();
  const [manualForm] = Form.useForm<ManualFormValues>();
  const [activeTab, setActiveTab] = useState<"plan" | "manual">("plan");
  const [submissions, setSubmissions] = useState<AnnotationSubmission[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    planForm.resetFields();
    manualForm.resetFields();
    planForm.setFieldsValue({
      submission_ids: [],
      sample_size: 1,
      sample_type: "RANDOM",
      sampling_policy_version: "",
      sample_reason: "",
      stratum: null,
      risk_threshold: null,
      due_at: null,
    });
    manualForm.setFieldsValue({
      submission_id: undefined,
      sample_type: "RANDOM",
      sampling_policy_version: "",
      sample_reason: "",
      stratum: null,
      item_refs: [],
      due_at: null,
    });
    const controller = new AbortController();
    setLoading(true);
    setLoadError(null);
    // 抽检候选必须覆盖任务的全部提交，不能只看服务端第一页。
    fetchAllCursorPages(
      (options) =>
        annotationApi.listSubmissions(projectId, taskId, {
          cursor: options.cursor,
          limit: options.limit,
          signal: controller.signal,
        }),
      (submission) => submission.submission_id,
    )
      .then((items) => setSubmissions(items))
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setSubmissions([]);
          setLoadError(
            caught instanceof Error ? caught.message : "提交列表加载失败。",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [manualForm, open, planForm, projectId, taskId]);

  const handleSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      if (activeTab === "plan") {
        const values = await planForm.validateFields();
        await onSubmit({
          mode: "plan",
          input: {
            task_id: taskId,
            candidates: values.submission_ids.map((submissionId) => ({
              submission_id: submissionId,
            })),
            sampling_policy_version: values.sampling_policy_version.trim(),
            sample_type: values.sample_type,
            sample_size: values.sample_size,
            sample_reason: values.sample_reason.trim(),
            stratum: values.stratum?.trim() ? values.stratum.trim() : null,
            risk_threshold: values.risk_threshold ?? null,
            due_at: toIso(values.due_at),
          },
        });
      } else {
        const values = await manualForm.validateFields();
        const submission = submissions.find(
          (item) => item.submission_id === values.submission_id,
        );
        if (!submission) {
          throw new Error("请选择有效的提交；标注人由提交自动带出。");
        }
        await onSubmit({
          mode: "manual",
          input: {
            task_id: taskId,
            submission_id: submission.submission_id,
            annotator_id: submission.annotator_id,
            sampling_policy_version: values.sampling_policy_version.trim(),
            sample_type: values.sample_type,
            sample_reason: values.sample_reason.trim(),
            item_refs: (values.item_refs ?? [])
              .map((value) => value.trim())
              .filter((value) => value !== ""),
            stratum: values.stratum?.trim() ? values.stratum.trim() : null,
            due_at: toIso(values.due_at),
          },
        });
      }
    } catch (caught) {
      if (caught instanceof Error) {
        setError(caught.message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const options = submissionOptions(submissions);

  return (
    <Drawer
      title="生成质检抽检"
      width={680}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
            生成
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
        {!loading && submissions.length === 0 && !loadError ? (
          <Alert
            type="warning"
            showIcon
            message="该任务暂无提交，无法生成抽检；抽检候选必须是已存在的提交。"
          />
        ) : null}
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key === "manual" ? "manual" : "plan")}
          items={[
            {
              key: "plan",
              label: "按计划生成",
              children: (
                <Form form={planForm} layout="vertical">
                  <Form.Item
                    name="submission_ids"
                    label="候选提交"
                    rules={[{ required: true, message: "请选择至少一条提交" }]}
                  >
                    <Select
                      mode="multiple"
                      loading={loading}
                      options={options}
                      placeholder="选择任务内的提交"
                      optionFilterProp="label"
                    />
                  </Form.Item>
                  <Form.Item
                    name="sample_size"
                    label="抽样数量"
                    rules={[{ required: true, message: "请填写抽样数量" }]}
                    extra="实际生成数量可能少于候选数（去重或风险阈值过滤）。"
                  >
                    <InputNumber min={1} precision={0} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item name="sample_type" label="抽样类型">
                    <Select options={sampleTypeOptions} />
                  </Form.Item>
                  <Form.Item
                    name="sampling_policy_version"
                    label="抽样策略版本"
                    rules={[{ required: true, message: "请填写策略版本" }]}
                  >
                    <Input maxLength={64} placeholder="由质量策略负责人填写" />
                  </Form.Item>
                  <Form.Item
                    name="sample_reason"
                    label="抽样理由"
                    rules={[{ required: true, message: "请填写抽样理由" }]}
                  >
                    <Input.TextArea rows={2} maxLength={500} />
                  </Form.Item>
                  <Form.Item name="stratum" label="目标分层（可选）">
                    <Input maxLength={100} />
                  </Form.Item>
                  <Form.Item name="risk_threshold" label="风险阈值（可选，0–1）">
                    <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item name="due_at" label="截止时间（可选）">
                    <DatePicker showTime style={{ width: "100%" }} />
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: "manual",
              label: "手工创建",
              children: (
                <Form form={manualForm} layout="vertical">
                  <Form.Item
                    name="submission_id"
                    label="提交"
                    rules={[{ required: true, message: "请选择提交" }]}
                  >
                    <Select
                      loading={loading}
                      options={options}
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择任务内的提交"
                    />
                  </Form.Item>
                  <Form.Item name="sample_type" label="抽样类型">
                    <Select options={sampleTypeOptions} />
                  </Form.Item>
                  <Form.Item
                    name="sampling_policy_version"
                    label="抽样策略版本"
                    rules={[{ required: true, message: "请填写策略版本" }]}
                  >
                    <Input maxLength={64} />
                  </Form.Item>
                  <Form.Item
                    name="sample_reason"
                    label="抽样理由"
                    rules={[{ required: true, message: "请填写抽样理由" }]}
                  >
                    <Input.TextArea rows={2} maxLength={500} />
                  </Form.Item>
                  <Form.Item name="stratum" label="分层（可选）">
                    <Input maxLength={100} />
                  </Form.Item>
                  <Form.Item name="item_refs" label="抽检条目（可选）">
                    <Select mode="tags" open={false} tokenSeparators={[",", " "]} placeholder="回车添加条目 ID" />
                  </Form.Item>
                  <Form.Item name="due_at" label="截止时间（可选）">
                    <DatePicker showTime style={{ width: "100%" }} />
                  </Form.Item>
                  <Typography.Text type="secondary">
                    标注人由所选提交自动带出；抽检复核人不得是标注人。
                  </Typography.Text>
                </Form>
              ),
            },
          ]}
        />
      </Space>
    </Drawer>
  );
}
