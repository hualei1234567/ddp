import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
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
import { modelOpsApi, type ModelCreateInput } from "../../services/modelops";
import {
  presentModelStatus,
  presentTaskType,
} from "../../services/modelopsPresentation";
import { modelDetailPath } from "../../services/modelopsUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  MODEL_STATUS_VALUES,
  MODEL_TASK_TYPE_VALUES,
  type Model,
} from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function CreateModelModal({
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
  const [form] = Form.useForm<ModelCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.createModel(projectId, { ...values, tags: values.tags ?? [] });
      messageApi.success("模型已创建（DRAFT）");
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
        title="新建模型"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="模型编码"
            rules={[{ required: true, max: 64, message: "请输入 1-64 字符编码" }]}
          >
            <Input placeholder="ACT-PICK-001" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, max: 200, message: "请输入名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
          <Form.Item
            name="task_type"
            label="任务类型"
            initialValue="IMITATION"
            rules={[{ required: true }]}
          >
            <Select
              options={MODEL_TASK_TYPE_VALUES.map((value) => ({
                value,
                label: presentTaskType(value),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="framework"
            label="框架"
            initialValue="pytorch"
            rules={[{ required: true, max: 100, message: "请输入框架名称" }]}
          >
            <Input placeholder="pytorch / jax / tensorflow" />
          </Form.Item>
          <Form.Item
            name="owner_id"
            label="负责人用户 ID（UUID）"
            rules={[
              { required: true, message: "请输入负责人 UUID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input placeholder="00000000-0000-0000-0000-000000000000" />
          </Form.Item>
          <Form.Item name="tags" label="标签">
            <Select mode="tags" tokenSeparators={[","]} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function ModelListContent({
  projectId,
  status,
  taskType,
}: {
  projectId: string;
  status: string | undefined;
  taskType: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      modelOpsApi.listModels(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
        task_type: taskType,
      }),
    [projectId, status, taskType],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.model_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const changeFilter = (key: string, value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    setSearchParams(params, { replace: true });
  };

  const columns: ColumnsType<Model> = [
    {
      title: "名称 / 编码",
      key: "name",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={modelDetailPath(item.model_id)}>{item.name}</Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "任务类型",
      dataIndex: "task_type",
      width: 120,
      render: (value: string) => presentTaskType(value),
    },
    { title: "框架", dataIndex: "framework", width: 120 },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentModelStatus} />
      ),
    },
    {
      title: "标签",
      key: "tags",
      render: (_, item) =>
        item.tags.length > 0 ? (
          <Space wrap size={4}>
            {item.tags.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    { title: "更新时间", dataIndex: "updated_at", width: 190 },
  ];

  return (
    <PageContainer
      title="模型与推理"
      description="阶段 5 模型注册、版本发布、评测、推理与数据缺口闭环。状态与动作均以服务端 allowed_actions 为准。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={MODEL_STATUS_VALUES.map((value) => ({
              value,
              label: presentModelStatus(value).label,
            }))}
            onChange={(value) => changeFilter("status", value ?? undefined)}
          />
          <Typography.Text>任务类型</Typography.Text>
          <Select
            allowClear
            value={taskType}
            placeholder="全部类型"
            style={{ minWidth: 150 }}
            options={MODEL_TASK_TYPE_VALUES.map((value) => ({
              value,
              label: presentTaskType(value),
            }))}
            onChange={(value) => changeFilter("task_type", value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建模型
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {phase === "forbidden" ? (
        error ? <ApiErrorState error={error} onRetry={reload} /> : null
      ) : (
        <>
          {stale ? (
            <Typography.Text type="warning">
              最近一次刷新失败，展示的是上次成功获取的数据。
            </Typography.Text>
          ) : null}
          <Table<Model>
            rowKey="model_id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            locale={{ emptyText: error ? "加载失败" : "暂无模型" }}
          />
          {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
          {hasMore ? (
            <Button block style={{ marginTop: 12 }} loading={loadingMore} onClick={loadMore}>
              加载更多
            </Button>
          ) : null}
          {loadMoreError ? (
            <Typography.Text type="danger">
              加载更多失败：{loadMoreError.message}
            </Typography.Text>
          ) : null}
        </>
      )}
      <CreateModelModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function ModelListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="模型与推理" description="模型必须按项目隔离展示。">
        <ProjectScopeRequired scope="模型列表" />
      </PageContainer>
    );
  }
  return (
    <ModelListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}:${searchParams.get("task_type") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
      taskType={searchParams.get("task_type") ?? undefined}
    />
  );
}
