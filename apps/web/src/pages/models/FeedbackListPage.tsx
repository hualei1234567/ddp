import { MinusCircleOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { modelOpsApi, type FeedbackCreateInput } from "../../services/modelops";
import {
  presentDimensionType,
  presentFeedbackStatus,
  presentIssueType,
  presentSeverity,
} from "../../services/modelopsPresentation";
import { feedbackDetailPath } from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  FEEDBACK_ISSUE_TYPE_VALUES,
  FEEDBACK_SEVERITY_VALUES,
  FEEDBACK_STATUS_VALUES,
  GAP_DIMENSION_VALUES,
  type ModelFeedback,
} from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface FeedbackFormValues extends Omit<FeedbackCreateInput, "evidence_refs"> {
  evidence_refs?: string[];
}

function CreateFeedbackModal({
  projectId,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<FeedbackFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.createFeedback(projectId, {
        ...values,
        evaluation_id: values.evaluation_id || null,
        inference_task_id: values.inference_task_id || null,
        evidence_refs: values.evidence_refs ?? [],
        dimensions: values.dimensions.map((item) => ({
          dimension_type: item.dimension_type,
          dimension_value: item.dimension_value,
          current_count: item.current_count ?? null,
          target_count: item.target_count ?? null,
        })),
      });
      messageApi.success("模型反馈已登记（OPEN）");
      form.resetFields();
      onCreated();
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
        title="登记模型反馈"
        open={open}
        width={680}
        okText="登记"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="model_version_id"
            label="模型版本 ID（UUID）"
            rules={[
              { required: true, message: "请输入模型版本 ID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
          <Space style={{ display: "flex" }} align="start" wrap>
            <Form.Item
              name="evaluation_id"
              label="关联评测（可选）"
              rules={[{ pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
            >
              <Input style={{ width: 280 }} />
            </Form.Item>
            <Form.Item
              name="inference_task_id"
              label="关联推理任务（可选）"
              rules={[{ pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
            >
              <Input style={{ width: 280 }} />
            </Form.Item>
          </Space>
          <Space style={{ display: "flex" }} align="start" wrap>
            <Form.Item
              name="issue_type"
              label="问题类型"
              initialValue="SCENE_COVERAGE"
              rules={[{ required: true }]}
            >
              <Select
                style={{ width: 200 }}
                options={FEEDBACK_ISSUE_TYPE_VALUES.map((value) => ({
                  value,
                  label: presentIssueType(value),
                }))}
              />
            </Form.Item>
            <Form.Item
              name="severity"
              label="严重程度"
              initialValue="MAJOR"
              rules={[{ required: true }]}
            >
              <Select
                style={{ width: 160 }}
                options={FEEDBACK_SEVERITY_VALUES.map((value) => ({
                  value,
                  label: presentSeverity(value).label,
                }))}
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="description"
            label="问题描述"
            rules={[{ required: true, max: 4000, message: "请描述问题现象" }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Typography.Text strong>缺口维度</Typography.Text>
          <Form.List name="dimensions" initialValue={[{ dimension_type: "SCENE" }]}>
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 8 }}>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="start" wrap>
                    <Form.Item
                      {...rest}
                      name={[name, "dimension_type"]}
                      rules={[{ required: true }]}
                    >
                      <Select
                        style={{ width: 140 }}
                        options={GAP_DIMENSION_VALUES.map((value) => ({
                          value,
                          label: presentDimensionType(value),
                        }))}
                      />
                    </Form.Item>
                    <Form.Item
                      {...rest}
                      name={[name, "dimension_value"]}
                      rules={[{ required: true, message: "维度值" }]}
                    >
                      <Input placeholder="kitchen" style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "current_count"]}>
                      <InputNumber placeholder="当前" min={0} style={{ width: 100 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, "target_count"]}>
                      <InputNumber placeholder="目标" min={0} style={{ width: 100 }} />
                    </Form.Item>
                    <Button type="text" icon={<MinusCircleOutlined />} onClick={() => remove(name)} />
                  </Space>
                ))}
                <Button type="dashed" icon={<PlusOutlined />} onClick={() => add()}>
                  增加维度
                </Button>
              </Space>
            )}
          </Form.List>
          <Form.Item name="evidence_refs" label="证据引用" style={{ marginTop: 16 }}>
            <Select mode="tags" tokenSeparators={[","]} placeholder="对象存储键或评测报告引用" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function FeedbackListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      modelOpsApi.listFeedback(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.feedback_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<ModelFeedback> = [
    {
      title: "问题",
      key: "issue",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={feedbackDetailPath(item.feedback_id)}>{presentIssueType(item.issue_type)}</Link>
          <Typography.Text type="secondary" ellipsis>
            {item.description}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "严重程度",
      dataIndex: "severity",
      width: 110,
      render: (value: string) => <StatusTag value={value} presentation={presentSeverity} />,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentFeedbackStatus} />
      ),
    },
    {
      title: "模型版本",
      dataIndex: "model_version_id",
      width: 220,
      ellipsis: true,
    },
    {
      title: "缺口维度",
      key: "dimensions",
      render: (_, item) =>
        item.dimensions.length > 0
          ? item.dimensions
              .map((d) => `${presentDimensionType(d.dimension_type)}:${d.dimension_value}`)
              .join("，")
          : "—",
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 190,
      render: (value: string) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="模型反馈"
      description="把线上失败与评测缺口登记为结构化反馈，再转成数据需求；不直接修改模型或数据集。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={FEEDBACK_STATUS_VALUES.map((value) => ({
              value,
              label: presentFeedbackStatus(value).label,
            }))}
            onChange={(value) => {
              const params = new URLSearchParams(searchParams);
              if (value) {
                params.set("status", value);
              } else {
                params.delete("status");
              }
              setSearchParams(params, { replace: true });
            }}
          />
        </Space>
      }
      extra={
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            登记反馈
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<ModelFeedback>
        rowKey="feedback_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无反馈" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {hasMore ? (
        <Button block style={{ marginTop: 12 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <Typography.Text type="danger">加载更多失败：{loadMoreError.message}</Typography.Text>
      ) : null}
      <CreateFeedbackModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function FeedbackListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="模型反馈" description="反馈必须按项目隔离展示。">
        <ProjectScopeRequired scope="模型反馈" />
      </PageContainer>
    );
  }
  return (
    <FeedbackListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
