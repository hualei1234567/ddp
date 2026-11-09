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
import {
  DATASET_PURPOSE_VALUES,
  DATASET_STATUS_VALUES,
  DATASET_USAGE_POLICY_VALUES,
  type Dataset,
} from "../../types/dataset";
import { datasetApi, type DatasetCreateInput } from "../../services/datasets";
import {
  presentDatasetStatus,
  presentPurpose,
  presentUsagePolicy,
} from "../../services/datasetPresentation";
import { datasetDetailPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function CreateDatasetModal({
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
  const [form] = Form.useForm<DatasetCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await datasetApi.create(projectId, {
        ...values,
        tags: values.tags ?? [],
      });
      messageApi.success("数据集已创建（DRAFT）");
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
        title="创建数据集"
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
            label="数据集编码"
            rules={[{ required: true, max: 64, message: "请输入 1-64 字符编码" }]}
          >
            <Input placeholder="DS-PICK-001" />
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
            name="purpose"
            label="用途"
            initialValue="TRAINING"
            rules={[{ required: true }]}
          >
            <Select options={DATASET_PURPOSE_VALUES.map((value) => ({ value, label: presentPurpose(value) }))} />
          </Form.Item>
          <Form.Item
            name="usage_policy"
            label="使用策略"
            initialValue="TRAINING_ALLOWED"
            rules={[{ required: true }]}
          >
            <Select
              options={DATASET_USAGE_POLICY_VALUES.map((value) => ({
                value,
                label: presentUsagePolicy(value).label,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="owner_id"
            label="负责人用户 ID（UUID）"
            rules={[
              { required: true, message: "请输入负责人 UUID" },
              {
                pattern: UUID_PATTERN,
                message: "必须是标准 UUID",
              },
            ]}
          >
            <Input placeholder="00000000-0000-0000-0000-000000000000" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function DatasetListContent({
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
      datasetApi.list(projectId, { cursor: options.cursor, signal: options.signal, status }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.dataset_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<Dataset> = [
    {
      title: "名称 / 编码",
      key: "name",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={datasetDetailPath(item.dataset_id)}>{item.name}</Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 120,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentDatasetStatus} />
      ),
    },
    {
      title: "用途",
      key: "purpose",
      width: 100,
      render: (_, item) => presentPurpose(item.purpose),
    },
    {
      title: "使用策略",
      key: "usage",
      width: 140,
      render: (_, item) => (
        <StatusTag value={item.usage_policy} presentation={presentUsagePolicy} />
      ),
    },
    {
      title: "冻结版本",
      dataIndex: "frozen_version_count",
      width: 100,
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: 190,
      render: (_, item) => item.updated_at,
    },
  ];

  const changeStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("status", value);
    } else {
      params.delete("status");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <PageContainer
      title="数据集管理"
      description="阶段 4 数据集容器、版本、策略与授权。状态与动作以服务端为准，本页不使用 Mock 数据。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={DATASET_STATUS_VALUES.map((value) => ({
              value,
              label: presentDatasetStatus(value).label,
            }))}
            onChange={(value) => changeStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建数据集
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
          <Table<Dataset>
            rowKey="dataset_id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            locale={{ emptyText: error ? "加载失败" : "暂无数据集" }}
          />
          {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
          {hasMore ? (
            <Button
              block
              style={{ marginTop: 12 }}
              loading={loadingMore}
              onClick={loadMore}
            >
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
      <CreateDatasetModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function DatasetListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="数据集管理" description="数据集必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据集列表" />
      </PageContainer>
    );
  }
  return (
    <DatasetListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
