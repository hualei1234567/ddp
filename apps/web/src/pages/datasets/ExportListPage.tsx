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
import { datasetApi, type ExportCreateInput } from "../../services/datasets";
import { presentExportStatus } from "../../services/datasetPresentation";
import { exportDetailPath } from "../../services/datasetUrl";
import { defaultFormatVersion, exportFormatOptions } from "../../services/exportFormat";
import { resolvePagePhase } from "../../services/pagePhase";
import { EXPORT_JOB_STATUS_VALUES, type ExportJob } from "../../types/dataset";

function CreateExportModal({
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
  const [form] = Form.useForm<ExportCreateInput>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, holder] = message.useMessage();

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await datasetApi.createExport(projectId, {
        ...values,
        format: values.format,
        format_version: values.format_version,
        config: {},
      });
      messageApi.success("导出任务已创建（QUEUED）");
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
        title="新建导出任务"
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="dataset_version_id"
            label="数据集版本 ID（必须已冻结）"
            rules={[{ required: true, message: "请输入版本 UUID" }]}
          >
            <Input placeholder="00000000-0000-0000-0000-000000000000" />
          </Form.Item>
          <Form.Item name="exporter_id" label="导出器 ID" initialValue="lerobot-exporter" rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="exporter_version" label="导出器版本" initialValue="1.0.0" rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="mapping_version" label="字段映射版本" initialValue="ddp.lerobot.mapping/1.0.0" rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="format"
            label="导出格式"
            initialValue="LE_ROBOT"
            rules={[{ required: true, message: "请选择导出格式" }]}
            extra="格式与版本成对：切换格式会自动带出对应版本，交叉组合会被服务端拒绝。"
          >
            <Select options={exportFormatOptions(undefined)} onChange={(value: string) => {
              // 格式与版本必须成对，切换格式时同步版本，避免用户手工拼出交叉组合。
              form.setFieldValue("format_version", defaultFormatVersion(value) ?? "");
            }} />
          </Form.Item>
          <Form.Item name="format_version" label="格式版本" initialValue="ddp.lerobot/1.0.0" rules={[{ required: true }]}>
            <Input maxLength={64} disabled />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary">
          仅冻结且有效的版本允许导出；导出为异步 Job，产物校验和由服务端计算。
        </Typography.Text>
      </Modal>
    </>
  );
}

function ExportListContent({ projectId, status }: { projectId: string; status?: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listExports(projectId, { cursor: options.cursor, signal: options.signal, status }),
    [projectId, status],
  );
  const { items, loading, refreshing, error, stale, reload } = useCursorPage(
    load,
    (item) => item.export_job_id,
  );
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<ExportJob> = [
    {
      title: "任务",
      key: "id",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={exportDetailPath(item.export_job_id)}>{item.export_job_id.slice(0, 8)}</Link>
          <Typography.Text type="secondary">
            版本：{item.dataset_version_id.slice(0, 8)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 120,
      render: (_, item) => <StatusTag value={item.status} presentation={presentExportStatus} />,
    },
    { title: "格式", key: "format", width: 200, render: (_, item) => `${item.format} ${item.format_version}` },
    { title: "产物数", dataIndex: "artifact_count", width: 90 },
    { title: "总字节", dataIndex: "total_bytes", width: 120 },
    { title: "尝试", key: "attempt", width: 90, render: (_, item) => `${item.attempt}/${item.max_attempts}` },
    { title: "验证", dataIndex: "verification_status", width: 150 },
    { title: "创建时间", dataIndex: "created_at", width: 190 },
  ];

  const changeStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set("status", value);
    else params.delete("status");
    setSearchParams(params, { replace: true });
  };

  return (
    <PageContainer
      title="导出任务"
      description="阶段 4 异步导出与产物校验；PARTIAL 不视为成功。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 160 }}
            options={EXPORT_JOB_STATUS_VALUES.map((value) => ({
              value,
              label: presentExportStatus(value).label,
            }))}
            onChange={(value) => changeStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建导出
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
          <Table<ExportJob>
            rowKey="export_job_id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
            locale={{ emptyText: "暂无导出任务" }}
          />
          {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
        </>
      )}
      <CreateExportModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={reload}
      />
    </PageContainer>
  );
}

export default function ExportListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="导出任务" description="导出任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="导出任务列表" />
      </PageContainer>
    );
  }
  return (
    <ExportListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
