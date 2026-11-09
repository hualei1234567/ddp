import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Divider, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/dataset/ActionButtons";
import CountsGrid from "../../components/dataset/CountsGrid";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { datasetApi } from "../../services/datasets";
import { presentExportStatus } from "../../services/datasetPresentation";
import { exportListPath } from "../../services/datasetUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type { ExportArtifactManifest, ExportJob } from "../../types/dataset";

function ArtifactsPanel({
  projectId,
  exportJobId,
  status,
}: {
  projectId: string;
  exportJobId: string;
  status: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      datasetApi.getExportArtifacts(projectId, exportJobId, { signal }),
    [projectId, exportJobId],
  );
  const { data, error, loading, reload } = useRequest<ExportArtifactManifest>(load);
  const [messageApi, holder] = message.useMessage();
  const done = status === "SUCCEEDED" || status === "PARTIAL";

  const download = async (path: string) => {
    try {
      const result = await datasetApi.getExportArtifactDownload(projectId, exportJobId, path);
      window.open(result.download_url, "_blank", "noopener,noreferrer");
      messageApi.success("已生成短期下载地址");
    } catch (caught) {
      messageApi.error(caught instanceof Error ? caught.message : "获取下载地址失败");
    }
  };

  const columns: ColumnsType<ExportArtifactManifest["artifacts"][number]> = [
    { title: "路径", dataIndex: "path", ellipsis: true },
    { title: "大小（字节）", dataIndex: "size_bytes", width: 130 },
    { title: "SHA256", dataIndex: "sha256", ellipsis: true },
    {
      title: "下载",
      key: "download",
      width: 100,
      render: (_, item) => (
        <Button size="small" disabled={!done} onClick={() => void download(item.path)}>
          下载
        </Button>
      ),
    },
  ];

  if (!done) {
    return (
      <Typography.Text type="secondary">
        导出尚未成功完成，产物清单可能为空；PARTIAL 不视为成功。
      </Typography.Text>
    );
  }

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      {data?.aggregate_sha256 ? (
        <Typography.Text>
          汇总 SHA256：<Typography.Text copyable>{data.aggregate_sha256}</Typography.Text>
        </Typography.Text>
      ) : null}
      {loading ? <PageState compact variant="loading" description="加载产物清单" /> : null}
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {data ? (
        <Table
          rowKey="path"
          size="small"
          columns={columns}
          dataSource={data.artifacts}
          pagination={false}
          locale={{ emptyText: "暂无产物" }}
        />
      ) : null}
    </Space>
  );
}

function ExportDetailContent({ projectId, exportJobId }: { projectId: string; exportJobId: string }) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => datasetApi.getExport(projectId, exportJobId, { signal }),
    [projectId, exportJobId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载导出任务" description="读取任务与产物。" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const job: ExportJob = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState compact variant="stale" description="刷新失败，展示上次成功数据。" onRetry={reload} />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            { key: "status", label: "状态", children: <StatusTag value={job.status} presentation={presentExportStatus} /> },
            { key: "version", label: "数据集版本", children: job.dataset_version_id },
            { key: "format", label: "格式", children: `${job.format} ${job.format_version}` },
            { key: "exporter", label: "导出器", children: `${job.exporter_id} ${job.exporter_version}` },
            { key: "mapping", label: "映射版本", children: job.mapping_version },
            { key: "prefix", label: "输出前缀", children: job.output_prefix },
            { key: "verification", label: "验证状态", children: job.verification_status },
            { key: "attempt", label: "尝试", children: `${job.attempt}/${job.max_attempts}` },
            { key: "started", label: "开始", children: job.started_at ?? "—" },
            { key: "finished", label: "结束", children: job.finished_at ?? "—" },
          ]}
        />
        <CountsGrid
          items={[
            { label: "产物数", value: job.artifact_count },
            { label: "总字节", value: job.total_bytes },
            { label: "汇总 SHA256", value: job.aggregate_sha256 ?? "—" },
          ]}
          column={3}
        />
        {job.error_code ? (
          <Typography.Text type="danger">
            {job.error_code}：{job.error_summary ?? ""}
          </Typography.Text>
        ) : null}
        <Space wrap>
          <Typography.Text strong>任务动作</Typography.Text>
          <ActionButtons
            actions={job.allowed_actions}
            onAction={async (action, payload) => {
              await datasetApi.actExport(projectId, exportJobId, action, {
                version: job.version,
                reason: payload.reason,
              });
              refreshAll();
            }}
          />
        </Space>
        <Divider />
        <ArtifactsPanel
          key={`${exportJobId}:${refreshToken}`}
          projectId={projectId}
          exportJobId={exportJobId}
          status={job.status}
        />
        <Tag color="default">verification_status={job.verification_status}（真实 Loader 验证前不会置为 VERIFIED）</Tag>
      </Space>
    );
  }

  return (
    <PageContainer
      title={data ? `导出任务 ${data.export_job_id.slice(0, 8)}` : "导出任务"}
      description={data ? data.export_job_id : "查看导出状态、产物与下载。"}
      extra={
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(exportListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refreshAll}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function ExportDetailPage() {
  const projectId = useProjectId();
  const { exportJobId } = useParams<{ exportJobId: string }>();
  if (!projectId || !exportJobId) {
    return (
      <PageContainer title="导出任务" description="导出任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="导出任务详情" />
      </PageContainer>
    );
  }
  return (
    <ExportDetailContent
      key={`${projectId}:${exportJobId}`}
      projectId={projectId}
      exportJobId={exportJobId}
    />
  );
}
