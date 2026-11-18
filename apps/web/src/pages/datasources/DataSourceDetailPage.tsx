import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Descriptions,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import { dataSourceApi } from "../../services/datasources";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  DataSourceDetail,
  DataSourceImport,
} from "../../types/datasource";

const ACCESS_COLORS: Record<string, string> = {
  PUBLIC: "green",
  GATED: "orange",
  PRIVATE: "red",
  UNKNOWN: "default",
};

const IMPORT_STATUS_COLORS: Record<DataSourceImport["status"], string> = {
  PENDING: "gold",
  RUNNING: "blue",
  SUCCEEDED: "green",
  FAILED: "red",
  CANCELLED: "default",
};

export interface DataSourceDetailBodyProps {
  detail: DataSourceDetail | null;
  loading: boolean;
  error: ApiError | null;
  stale: boolean;
  onRetry: () => void;
}

const IMPORT_COLUMNS: ColumnsType<DataSourceImport> = [
  {
    title: "导入任务",
    dataIndex: "import_id",
    width: 150,
    render: (value: string) => (
      <Link to={`/data-source-imports/${value}/review`}>
        <Typography.Text code>{value.slice(0, 8)}…</Typography.Text>
      </Link>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 110,
    render: (value: DataSourceImport["status"]) => (
      <Tag color={IMPORT_STATUS_COLORS[value]}>{value}</Tag>
    ),
  },
  { title: "范围", dataIndex: "scope", width: 90 },
  {
    title: "工具",
    key: "tool",
    render: (_, item) => `${item.tool_name}@${item.tool_version}`,
  },
  {
    title: "产出",
    key: "counts",
    width: 160,
    render: (_, item) =>
      item.imported_row_count === null && item.imported_file_count === null
        ? "未统计"
        : `${item.imported_row_count ?? "—"} 行 / ${
            item.imported_file_count ?? "—"
          } 文件`,
  },
  {
    title: "失败原因",
    dataIndex: "error_message",
    render: (value: string | null) => value ?? "—",
  },
];

/**
 * 数据源详情内容区（纯展示）。
 *
 * 单独导出是为了让测试覆盖 Loading / Empty / Error / Forbidden 三态，
 * 不需要在测试里模拟路由与网络。
 */
export function DataSourceDetailBody({
  detail,
  loading,
  error,
  stale,
  onRetry,
}: DataSourceDetailBodyProps) {
  const phase = resolvePagePhase({ hasData: detail !== null, loading, error });

  if (phase === "forbidden") {
    return <PageState variant="forbidden" />;
  }
  if (phase === "not-found") {
    return (
      <PageState
        variant="empty"
        title="数据源不存在或无权查看"
        description="该数据源可能已被删除，或不属于当前项目。"
        action={
          <Link to="/ingestion">
            <Button icon={<ArrowLeftOutlined />}>返回数据源列表</Button>
          </Link>
        }
      />
    );
  }
  if (phase === "loading") {
    return <PageState variant="loading" title="正在加载数据源详情" />;
  }
  if (phase === "error" && error) {
    return <ApiErrorState error={error} onRetry={onRetry} />;
  }
  if (!detail) {
    return (
      <PageState
        variant="empty"
        title="暂无数据源详情"
        description="未取得可展示的数据源信息。"
        onRetry={onRetry}
      />
    );
  }

  const source = detail.data_source;
  const check = detail.remote_check;

  return (
    <>
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Descriptions
        bordered
        size="small"
        column={2}
        items={[
          { key: "code", label: "编码", children: source.code },
          { key: "name", label: "名称", children: source.name },
          {
            key: "repo",
            label: "Repo ID",
            children: (
              <Typography.Text code copyable>
                {source.repo_id}
              </Typography.Text>
            ),
          },
          {
            key: "revision",
            label: "Revision",
            children: (
              <Typography.Text code copyable>
                {source.revision}
              </Typography.Text>
            ),
          },
          { key: "license", label: "许可证", children: source.license },
          {
            key: "kind",
            label: "类型",
            children: <Tag>{source.kind}</Tag>,
          },
          {
            key: "access",
            label: "访问条件",
            children: (
              <Space size={4}>
                <Tag color={ACCESS_COLORS[source.access_condition] ?? "default"}>
                  {source.access_condition}
                </Tag>
                {source.is_gated ? <Tag color="volcano">需申请</Tag> : null}
              </Space>
            ),
          },
          { key: "config", label: "Config", children: source.config_name ?? "—" },
          { key: "split", label: "Split", children: source.split ?? "—" },
          {
            key: "card",
            label: "Dataset Card",
            children: source.dataset_card_url ? (
              <a
                href={source.dataset_card_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                {source.dataset_card_url}
              </a>
            ) : (
              "—"
            ),
          },
          { key: "notes", label: "备注", children: source.notes ?? "—" },
        ]}
      />

      <section style={{ marginTop: 20 }}>
        <Typography.Title level={5}>最近远端校验</Typography.Title>
        {check ? (
          <>
            <Space
              size={8}
              wrap
              style={{ marginBottom: 8 }}
            >
              <Tag color="green">已验证</Tag>
              <Typography.Text type="secondary">
                校验于 {formatDateTime(check.checked_at)}
              </Typography.Text>
            </Space>
            <Descriptions
              bordered
              size="small"
              column={2}
              items={[
                {
                  key: "resolved_revision",
                  label: "远端 revision",
                  children: (
                    <Typography.Text code copyable>
                      {check.resolved_revision}
                    </Typography.Text>
                  ),
                },
                {
                  key: "license",
                  label: "远端许可证",
                  children: check.license ?? "未声明（不代表无限制）",
                },
                {
                  key: "gated",
                  label: "远端 gated",
                  children: check.is_gated ? "是" : "否",
                },
                {
                  key: "card",
                  label: "Dataset Card",
                  children: check.dataset_card_url ? (
                    <a
                      href={check.dataset_card_url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {check.dataset_card_url}
                    </a>
                  ) : (
                    "—"
                  ),
                },
              ]}
            />
          </>
        ) : (
          <Alert
            type="info"
            showIcon
            message="尚未进行远端校验"
            description="登记时未请求 verify_remote，或校验摘要已损坏。远端校验不是默认行为；此处为空不代表校验通过。"
          />
        )}
      </section>

      <section style={{ marginTop: 20 }}>
        <Typography.Title level={5}>导入任务</Typography.Title>
        <Table<DataSourceImport>
          rowKey="import_id"
          size="small"
          columns={IMPORT_COLUMNS}
          dataSource={detail.imports}
          pagination={false}
          scroll={{ x: 900 }}
          locale={{ emptyText: "暂无导入任务" }}
        />
      </section>
    </>
  );
}

function DataSourceDetailContent({
  projectId,
  dataSourceId,
}: {
  projectId: string;
  dataSourceId: string;
}) {
  const request = useCallback(
    (signal: AbortSignal) =>
      dataSourceApi.getDataSourceDetail(projectId, dataSourceId, { signal }),
    [projectId, dataSourceId],
  );
  const { data, loading, refreshing, error, stale, reload } =
    useRequest(request);

  return (
    <PageContainer
      title="数据源详情"
      description="登记信息、导入任务与最近一次远端校验结论。校验结论以服务端记录为准。"
      extra={
        <Space>
          <Link to="/ingestion">
            <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
          </Link>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      <DataSourceDetailBody
        detail={data}
        loading={loading}
        error={error}
        stale={stale}
        onRetry={reload}
      />
    </PageContainer>
  );
}

export default function DataSourceDetailPage() {
  const projectId = useProjectId();
  const { dataSourceId } = useParams<{ dataSourceId: string }>();

  if (!projectId) {
    return (
      <PageContainer title="数据源详情" description="数据源必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据源详情" />
      </PageContainer>
    );
  }
  if (!dataSourceId) {
    return (
      <PageContainer title="数据源详情" description="缺少数据源标识。">
        <PageState
          variant="empty"
          title="数据源不存在或无权查看"
          description="地址中缺少 data_source_id。"
          action={
            <Link to="/ingestion">
              <Button icon={<ArrowLeftOutlined />}>返回数据源列表</Button>
            </Link>
          }
        />
      </PageContainer>
    );
  }
  return (
    <DataSourceDetailContent projectId={projectId} dataSourceId={dataSourceId} />
  );
}
