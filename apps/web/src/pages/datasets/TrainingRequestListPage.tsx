import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal, Select, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import RequirementSnapshotFields from "../../components/dataset/RequirementSnapshotFields";
import {
  snapshotFromValues,
  type SnapshotFormValues,
} from "../../services/requirementSnapshot";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { trainingApi } from "../../services/training";
import {
  presentPriority,
  presentTrainingRequestStatus,
} from "../../services/datasetPresentation";
import { trainingRequestDetailPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  TRAINING_REQUEST_STATUS_VALUES,
  type TrainingRequest,
} from "../../types/dataset";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface RequestFormValues extends SnapshotFormValues {
  code: string;
  title: string;
  owner_id: string;
}

function CreateRequestModal({
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
  const [form] = Form.useForm<RequestFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, holder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await trainingApi.createRequest(projectId, {
        code: values.code,
        title: values.title,
        owner_id: values.owner_id,
        priority: values.priority,
        snapshot: snapshotFromValues(values),
      });
      messageApi.success("训练需求已创建（DRAFT）");
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {holder}
      <Modal
        title="新建训练需求"
        open={open}
        width={760}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Space style={{ width: "100%" }} styles={{ item: { flex: 1 } }}>
            <Form.Item name="code" label="需求编码" rules={[{ required: true, max: 64 }]}>
              <Input placeholder="TR-2026-001" />
            </Form.Item>
            <Form.Item name="title" label="标题" rules={[{ required: true, max: 200 }]}>
              <Input />
            </Form.Item>
          </Space>
          <Form.Item
            name="owner_id"
            label="负责人用户 ID（UUID）"
            rules={[
              { required: true, message: "请输入负责人 UUID" },
              { pattern: UUID_PATTERN, message: "必须是标准 UUID" },
            ]}
          >
            <Input />
          </Form.Item>
          <RequirementSnapshotFields />
        </Form>
      </Modal>
    </>
  );
}

function TrainingRequestListContent({
  projectId,
  status,
}: {
  projectId: string;
  status?: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      trainingApi.listRequests(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, loading, refreshing, error, stale, reload } = useCursorPage(
    load,
    (item) => item.training_request_id,
  );
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<TrainingRequest> = [
    {
      title: "标题 / 编码",
      key: "title",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={trainingRequestDetailPath(item.training_request_id)}>{item.title}</Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 120,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentTrainingRequestStatus} />
      ),
    },
    {
      title: "优先级",
      key: "priority",
      width: 90,
      render: (_, item) => (
        <StatusTag value={item.priority} presentation={presentPriority} />
      ),
    },
    {
      title: "当前修订",
      key: "revision",
      width: 100,
      render: (_, item) =>
        item.current_revision_no ? `r${item.current_revision_no}` : "—",
    },
    { title: "更新时间", dataIndex: "updated_at", width: 190 },
  ];

  const changeStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set("status", value);
    else params.delete("status");
    setSearchParams(params, { replace: true });
  };

  return (
    <PageContainer
      title="训练需求"
      description="阶段 4 训练需求、修订审批与交付；DDP 不训练模型，只保存外部引用。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={TRAINING_REQUEST_STATUS_VALUES.map((value) => ({
              value,
              label: presentTrainingRequestStatus(value).label,
            }))}
            onChange={(value) => changeStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建需求
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
          {stale ? <Typography.Text type="warning">刷新失败，展示上次成功数据。</Typography.Text> : null}
          <Table<TrainingRequest>
            rowKey="training_request_id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            locale={{ emptyText: "暂无训练需求" }}
          />
          {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
        </>
      )}
      <CreateRequestModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function TrainingRequestListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="训练需求" description="训练需求必须按项目隔离展示。">
        <ProjectScopeRequired scope="训练需求列表" />
      </PageContainer>
    );
  }
  return (
    <TrainingRequestListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
