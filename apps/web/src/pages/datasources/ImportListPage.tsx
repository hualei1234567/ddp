import { ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { dataSourceApi } from "../../services/datasources";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DataSourceImport, ImportAction } from "../../types/datasource";

const STATUS_VALUES = [
  "PENDING",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
] as const;

const ACTION_LABELS: Record<ImportAction, string> = {
  start: "开始",
  succeed: "标记成功",
  fail: "标记失败",
  cancel: "取消",
};

function StatusTag({ status }: { status: DataSourceImport["status"] }) {
  const color =
    status === "SUCCEEDED"
      ? "green"
      : status === "FAILED"
        ? "red"
        : status === "RUNNING"
          ? "blue"
          : status === "CANCELLED"
            ? "default"
            : "gold";
  return <Tag color={color}>{status}</Tag>;
}

/** `fail` 必须填写原因：失败留痕是 `docs/ddp/07` §5 的硬要求。 */
function FailModal({
  target,
  onClose,
  onDone,
}: {
  target: DataSourceImport | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<{ error_message: string }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    if (!target) {
      return;
    }
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await dataSourceApi.actImport(target.project_id, target.import_id, "fail", {
        version: target.version,
        error_message: values.error_message,
      });
      messageApi.success("已标记失败");
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
        title="标记导入失败"
        open={target !== null}
        okText="提交"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="error_message"
            label="失败原因"
            rules={[{ required: true, max: 2000 }]}
          >
            <Input.TextArea rows={3} maxLength={2000} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function SuccessModal({
  target,
  onClose,
  onDone,
}: {
  target: DataSourceImport | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<{ row_count: number; file_count: number }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    if (!target) {
      return;
    }
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await dataSourceApi.actImport(
        target.project_id,
        target.import_id,
        "succeed",
        {
          version: target.version,
          row_count: values.row_count,
          file_count: values.file_count,
        },
      );
      messageApi.success("已标记成功");
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
        title="标记导入成功"
        open={target !== null}
        okText="提交"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item name="row_count" label="导入行数" rules={[{ required: true, type: "number", min: 0 }]}>
            <Input type="number" />
          </Form.Item>
          <Form.Item name="file_count" label="导入文件数" rules={[{ required: true, type: "number", min: 0 }]}>
            <Input type="number" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function ImportListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [failTarget, setFailTarget] = useState<DataSourceImport | null>(null);
  const [successTarget, setSuccessTarget] = useState<DataSourceImport | null>(
    null,
  );
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      dataSourceApi.listImports(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
  );
  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } = useCursorPage(load, (item) => item.import_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const runAction = async (item: DataSourceImport, action: ImportAction) => {
    try {
      await dataSourceApi.actImport(projectId, item.import_id, action, {
        version: item.version,
      });
      messageApi.success(`已${ACTION_LABELS[action]}`);
      reload();
    } catch (caught) {
      if (caught instanceof Error) {
        messageApi.error(caught.message);
      }
    }
  };

  const columns: ColumnsType<DataSourceImport> = [
    {
      title: "导入任务",
      dataIndex: "import_id",
      width: 130,
      render: (value: string) => (
        <Typography.Text code>{value.slice(0, 8)}</Typography.Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: DataSourceImport["status"]) => <StatusTag status={value} />,
    },
    { title: "范围", dataIndex: "scope", width: 90 },
    {
      title: "样本上限",
      dataIndex: "sample_limit",
      width: 100,
      render: (value: number | null) => value ?? "—",
    },
    {
      title: "工具",
      key: "tool",
      width: 170,
      render: (_, item) => `${item.tool_name}@${item.tool_version}`,
    },
    {
      title: "结果",
      key: "result",
      width: 160,
      render: (_, item) => {
        if (item.error_message) {
          return (
            <Typography.Text type="danger" ellipsis style={{ maxWidth: 150 }}>
              {item.error_message}
            </Typography.Text>
          );
        }
        if (item.imported_row_count === null) {
          return <Typography.Text type="secondary">—</Typography.Text>;
        }
        return `${item.imported_row_count} 行 / ${item.imported_file_count ?? 0} 文件`;
      },
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      width: 180,
      render: (value: string | null) => (value ? formatDateTime(value) : "—"),
    },
    {
      title: "操作",
      key: "actions",
      width: 220,
      // 只按服务端 allowed_actions 渲染，前端不自行推断状态机。
      render: (_, item) => (
        <Space size={4} wrap>
          {item.allowed_actions.includes("start") ? (
            <Popconfirm
              title="开始导入？"
              onConfirm={() => void runAction(item, "start")}
            >
              <Button size="small">开始</Button>
            </Popconfirm>
          ) : null}
          {item.allowed_actions.includes("succeed") ? (
            <Button size="small" type="primary" onClick={() => setSuccessTarget(item)}>
              成功
            </Button>
          ) : null}
          {item.allowed_actions.includes("fail") ? (
            <Button size="small" danger onClick={() => setFailTarget(item)}>
              失败
            </Button>
          ) : null}
          {item.allowed_actions.includes("cancel") ? (
            <Popconfirm
              title="取消该导入？"
              onConfirm={() => void runAction(item, "cancel")}
            >
              <Button size="small">取消</Button>
            </Popconfirm>
          ) : null}
          {item.allowed_actions.length === 0 ? (
            <Typography.Text type="secondary">已结束</Typography.Text>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <PageContainer
      title="导入任务"
      description="外部数据源导入任务的状态与结果；可执行动作完全以服务端 allowed_actions 为准。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={STATUS_VALUES.map((value) => ({ value, label: value }))}
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
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
          刷新
        </Button>
      }
    >
      {contextHolder}
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<DataSourceImport>
        rowKey="import_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        scroll={{ x: 1200 }}
        locale={{ emptyText: error ? "加载失败" : "暂无导入任务" }}
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
      <FailModal
        target={failTarget}
        onClose={() => setFailTarget(null)}
        onDone={reload}
      />
      <SuccessModal
        target={successTarget}
        onClose={() => setSuccessTarget(null)}
        onDone={reload}
      />
    </PageContainer>
  );
}

export default function ImportListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="导入任务" description="导入任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="导入任务" />
      </PageContainer>
    );
  }
  return (
    <ImportListContent
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
