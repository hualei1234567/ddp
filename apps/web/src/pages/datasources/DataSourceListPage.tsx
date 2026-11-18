import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import {
  dataSourceApi,
  type DataSourceCreateInput,
} from "../../services/datasources";
import { resolvePagePhase } from "../../services/pagePhase";
import type { DataSource } from "../../types/datasource";

const ACCESS_VALUES = ["PUBLIC", "GATED", "PRIVATE", "UNKNOWN"] as const;

function AccessTag({ item }: { item: DataSource }) {
  const color =
    item.access_condition === "PUBLIC"
      ? "green"
      : item.access_condition === "GATED"
        ? "orange"
        : item.access_condition === "PRIVATE"
          ? "red"
          : "default";
  return <Tag color={color}>{item.access_condition}</Tag>;
}

function CreateDataSourceModal({
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
  const [form] = Form.useForm<DataSourceCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await dataSourceApi.createDataSource(projectId, values);
      messageApi.success("数据源已登记");
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
        title="登记外部数据源"
        open={open}
        okText="登记"
        cancelText="取消"
        width={640}
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="许可证与访问条件是合规底线"
          description="访问成功不等于拥有商业使用权。许可证缺失或条件不明时必须如实标注，不得猜测。"
        />
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="编码"
            rules={[{ required: true, max: 64 }]}
          >
            <Input placeholder="hf-lerobot-aloha" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, max: 200 }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="repo_id"
            label="Hugging Face Repo ID"
            rules={[
              { required: true },
              {
                pattern: /^[A-Za-z0-9][\w.-]*\/[A-Za-z0-9][\w.-]*$/,
                message: "格式为 owner/name，不要填 URL",
              },
            ]}
          >
            <Input placeholder="lerobot/aloha_sim_insertion_human" />
          </Form.Item>
          <Form.Item
            name="revision"
            label="Revision（40 位 commit sha）"
            rules={[
              { required: true },
              {
                pattern: /^[0-9a-f]{40}$/,
                message: "必须是 40 位 commit sha：分支名会移动，导入不可复现",
              },
            ]}
            extra="不接受 main / master 等分支名；请从数据集页面的 commit 历史获取 sha。"
          >
            <Input placeholder="0123456789abcdef0123456789abcdef01234567" />
          </Form.Item>
          <Form.Item
            name="license"
            label="许可证"
            rules={[{ required: true, max: 100 }]}
            extra="按 Dataset Card 填原值；不确定时填 UNKNOWN 并在备注说明。"
          >
            <Input placeholder="Apache-2.0" />
          </Form.Item>
          <Form.Item
            name="access_condition"
            label="访问条件"
            initialValue="PUBLIC"
            rules={[{ required: true }]}
          >
            <Select
              options={ACCESS_VALUES.map((value) => ({ value, label: value }))}
              onChange={(value) => {
                // GATED 必须与 is_gated 一致，表单联动避免用户提交必然失败的组合。
                if (value === "GATED") {
                  form.setFieldValue("is_gated", true);
                }
              }}
            />
          </Form.Item>
          <Form.Item name="is_gated" label="需要申请访问" valuePropName="checked">
            <Select
              options={[
                { value: true, label: "是" },
                { value: false, label: "否" },
              ]}
            />
          </Form.Item>
          <Form.Item name="config_name" label="Config">
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="split" label="Split">
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
          <Form.Item
            name="verify_remote"
            label="登记时校验远端元信息"
            valuePropName="checked"
            initialValue={false}
            extra="默认关闭，不访问外网。开启后会读取 Hugging Face 元信息：超时、仓库不存在或 gated 与声明不一致都会让登记失败。"
          >
            <Switch checkedChildren="校验" unCheckedChildren="不校验" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function ImportModal({
  projectId,
  source,
  open,
  onClose,
  onCreated,
}: {
  projectId: string;
  source: DataSource | null;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<{
    scope: string;
    tool_name: string;
    tool_version: string;
    sample_limit: number;
  }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const submit = async () => {
    if (!source) {
      return;
    }
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await dataSourceApi.createImport(projectId, source.data_source_id, values);
      messageApi.success("导入任务已登记，等待后台处理");
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
        title={`发起导入：${source?.name ?? ""}`}
        open={open}
        okText="创建导入任务"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="默认只做小样本导入"
          description="不默认全量下载。大文件处理在后台 worker 中进行，不在 HTTP 请求内完成。"
        />
        <Form
          form={form}
          layout="vertical"
          requiredMark
          initialValues={{
            scope: "SAMPLE",
            tool_name: "huggingface_hub",
            tool_version: "0.27.1",
            sample_limit: 10,
          }}
        >
          <Form.Item name="scope" label="导入范围" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "SAMPLE", label: "SAMPLE（小样本）" },
                { value: "SPLIT", label: "SPLIT（单个 split）" },
                { value: "FULL", label: "FULL（全量，需谨慎）" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="sample_limit"
            label="样本上限"
            rules={[{ required: true, type: "number", min: 1 }]}
          >
            <InputNumber min={1} max={100000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="tool_name" label="导入工具" rules={[{ required: true }]}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="tool_version" label="工具版本" rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

function DataSourceListContent({ projectId }: { projectId: string }) {
  const [createOpen, setCreateOpen] = useState(false);
  const [importTarget, setImportTarget] = useState<DataSource | null>(null);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      dataSourceApi.listDataSources(projectId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId],
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
  } = useCursorPage(load, (item) => item.data_source_id);
  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const columns: ColumnsType<DataSource> = [
    {
      title: "名称 / 编码",
      key: "name",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={`/ingestion/${item.data_source_id}`}>
            <Typography.Text strong>{item.name}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{item.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "Repo",
      dataIndex: "repo_id",
      render: (value: string) => (
        <Typography.Text code style={{ fontSize: 12 }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "Revision",
      dataIndex: "revision",
      width: 130,
      render: (value: string) => (
        <Typography.Text
          copyable={{ text: value }}
          style={{ fontFamily: "monospace", fontSize: 12 }}
        >
          {value.slice(0, 10)}…
        </Typography.Text>
      ),
    },
    { title: "许可证", dataIndex: "license", width: 140 },
    {
      title: "访问条件",
      key: "access",
      width: 130,
      render: (_, item) => (
        <Space size={4}>
          <AccessTag item={item} />
          {item.is_gated ? <Tag color="volcano">需申请</Tag> : null}
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_, item) => (
        <Button size="small" onClick={() => setImportTarget(item)}>
          发起导入
        </Button>
      ),
    },
  ];

  return (
    <PageContainer
      title="数据接入"
      description="登记外部数据源（当前支持 Hugging Face）并登记小样本导入任务。解析与审核在后续批次。"
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            登记数据源
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
          <Link to="/data-source-imports">
            <Button>查看导入任务</Button>
          </Link>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="revision 必须是 commit sha"
        description="数据集页面的 main 分支会移动；只有 commit sha 能保证同一份数据可复现。许可证与访问条件同时必填，访问成功不等于拥有商业使用权。"
      />
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<DataSource>
        rowKey="data_source_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        scroll={{ x: 1000 }}
        locale={{ emptyText: error ? "加载失败" : "暂无数据源" }}
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
      <CreateDataSourceModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
      <ImportModal
        projectId={projectId}
        source={importTarget}
        open={importTarget !== null}
        onClose={() => setImportTarget(null)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function DataSourceListPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="数据接入" description="数据源必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据源列表" />
      </PageContainer>
    );
  }
  return <DataSourceListContent projectId={projectId} />;
}
