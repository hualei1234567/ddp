import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
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
import { modelOpsApi, type InferenceTaskCreateInput } from "../../services/modelops";
import { presentInferenceTaskStatus } from "../../services/modelopsPresentation";
import { inferenceTaskDetailPath } from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import { INFERENCE_TASK_STATUS_VALUES, type InferenceTask } from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function CreateTaskModal({
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
  interface FormValues {
    model_version_id: string;
    service_id?: string;
    dataset_version_id?: string;
    episode_ids?: string[];
  }
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      const input: InferenceTaskCreateInput = {
        model_version_id: values.model_version_id,
        service_id: values.service_id || null,
        dataset_version_id: values.dataset_version_id || null,
        episode_ids: values.episode_ids ?? [],
      };
      setSubmitting(true);
      await modelOpsApi.createTask(projectId, input);
      messageApi.success("推理任务已入队（QUEUED）");
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
        title="发起推理任务"
        open={open}
        okText="入队"
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
          <Form.Item
            name="service_id"
            label="推理服务 ID（可选）"
            rules={[{ pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="dataset_version_id"
            label="数据集版本 ID（可选）"
            rules={[{ pattern: UUID_PATTERN, message: "必须是标准 UUID" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="episode_ids" label="Episode ID 列表">
            <Select mode="tags" tokenSeparators={[",", " "]} placeholder="批量推理时填写" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function TaskListContent({ projectId, status }: { projectId: string; status: string | undefined }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      modelOpsApi.listTasks(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.task_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<InferenceTask> = [
    {
      title: "任务 ID",
      dataIndex: "task_id",
      width: 220,
      render: (value: string) => <Link to={inferenceTaskDetailPath(value)}>{value}</Link>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentInferenceTaskStatus} />
      ),
    },
    {
      title: "模型版本",
      dataIndex: "model_version_id",
      width: 220,
      ellipsis: true,
    },
    {
      title: "Episode 数",
      key: "episodes",
      width: 110,
      render: (_, item) => item.episode_ids.length,
    },
    {
      title: "指标",
      key: "metrics",
      render: (_, item) =>
        Object.keys(item.metrics).length > 0
          ? Object.entries(item.metrics)
              .map(([key, value]) => `${key}=${value}`)
              .join("，")
          : "—",
    },
    {
      title: "完成时间",
      dataIndex: "finished_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <PageContainer
      title="推理任务"
      description="推理任务由适配器异步执行并通过签名回调回传结果；本页不展示伪造的推理输出。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={INFERENCE_TASK_STATUS_VALUES.map((value) => ({
              value,
              label: presentInferenceTaskStatus(value).label,
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
            发起推理
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
      <Table<InferenceTask>
        rowKey="task_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无推理任务" }}
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
      <CreateTaskModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function InferenceTaskListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="推理任务" description="推理任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="推理任务" />
      </PageContainer>
    );
  }
  return (
    <TaskListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
