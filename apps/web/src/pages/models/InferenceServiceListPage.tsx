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
import { useSearchParams } from "react-router-dom";

import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { modelOpsApi, type InferenceServiceCreateInput } from "../../services/modelops";
import {
  presentHealthStatus,
  presentServiceStatus,
} from "../../services/modelopsPresentation";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  INFERENCE_SERVICE_STATUS_VALUES,
  type InferenceService,
} from "../../types/modelops";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ENVIRONMENTS = ["DEV", "STAGING", "PROD"];

function CreateServiceModal({
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
  const [form] = Form.useForm<InferenceServiceCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await modelOpsApi.createService(projectId, { ...values, config: {} });
      messageApi.success("推理服务已登记（PENDING），等待部署。");
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
        title="登记推理服务"
        open={open}
        okText="登记"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Typography.Paragraph type="secondary">
          只有已发布（RELEASED）的模型版本才能部署；适配器未配置时部署动作会返回 503。
        </Typography.Paragraph>
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
          <Form.Item name="environment" label="环境" initialValue="DEV" rules={[{ required: true }]}>
            <Select options={ENVIRONMENTS.map((value) => ({ value, label: value }))} />
          </Form.Item>
          <Form.Item
            name="endpoint_url"
            label="服务地址"
            rules={[{ required: true, max: 1024, message: "请输入服务地址" }]}
          >
            <Input placeholder="http://inference.internal:8080" />
          </Form.Item>
          <Form.Item name="resource_ref" label="资源引用（可选）">
            <Input placeholder="GPU 节点或部署单元标识" maxLength={512} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function ServiceListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      modelOpsApi.listServices(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.service_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const act = async (service: InferenceService, action: string) => {
    try {
      setBusy(service.service_id);
      await modelOpsApi.actService(projectId, service.service_id, action, {
        version: service.version,
      });
      messageApi.success("推理服务动作已提交");
      reload();
    } catch (caught) {
      messageApi.error(caught instanceof Error ? caught.message : "动作执行失败");
    } finally {
      setBusy(null);
    }
  };

  const columns: ColumnsType<InferenceService> = [
    {
      title: "服务 ID",
      dataIndex: "service_id",
      width: 220,
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentServiceStatus} />
      ),
    },
    {
      title: "健康",
      dataIndex: "health_status",
      width: 100,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentHealthStatus} />
      ),
    },
    { title: "环境", dataIndex: "environment", width: 100 },
    { title: "服务地址", dataIndex: "endpoint_url", ellipsis: true },
    {
      title: "部署时间",
      dataIndex: "deployed_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "动作",
      key: "actions",
      width: 220,
      render: (_, item) => (
        <Space wrap>
          {item.allowed_actions.map((action) => (
            <Button
              key={action}
              size="small"
              loading={busy === item.service_id}
              onClick={() => void act(item, action)}
            >
              {action}
            </Button>
          ))}
          {item.allowed_actions.length === 0 ? (
            <Typography.Text type="secondary">无可用动作</Typography.Text>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <PageContainer
      title="推理服务"
      description="推理服务绑定已发布模型版本；部署/停止由服务端状态机与适配器能力决定。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={INFERENCE_SERVICE_STATUS_VALUES.map((value) => ({
              value,
              label: presentServiceStatus(value).label,
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
            登记服务
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {contextHolder}
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<InferenceService>
        rowKey="service_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无推理服务" }}
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
      <CreateServiceModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function InferenceServiceListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="推理服务" description="推理服务必须按项目隔离展示。">
        <ProjectScopeRequired scope="推理服务" />
      </PageContainer>
    );
  }
  return (
    <ServiceListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
