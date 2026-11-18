import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import StatusTag from "../../components/ui/StatusTag";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import type { ApiError } from "../../services/api";
import { modelOpsApi } from "../../services/modelops";
import {
  presentDimensionType,
  presentGapEvidenceSource,
  presentGapUnmappedReason,
} from "../../services/modelopsPresentation";
import { requirementDetailPath } from "../../services/modelopsUrl";
import {
  GAP_DIMENSION_VALUES,
  type GapSourceCount,
  type GapSummary,
  type GapSummaryItem,
  type GapUnmappedEvidence,
} from "../../types/modelops";

function SourceBadges({ sources }: { sources: GapSourceCount[] }) {
  if (sources.length === 0) {
    return (
      <Typography.Text type="secondary">
        本次扫描无来源证据（不代表缺口为 0）
      </Typography.Text>
    );
  }
  return (
    <Space size={[0, 4]} wrap>
      {sources.map((item) => (
        <StatusTag
          key={`${item.source}:${item.count}`}
          value={item.source}
          presentation={presentGapEvidenceSource}
          suffix={`× ${item.count}`}
        />
      ))}
    </Space>
  );
}

function UnmappedReason({ reason }: { reason: string }) {
  const label = presentGapUnmappedReason(reason);
  return (
    <Space direction="vertical" size={0}>
      <span>{label}</span>
      {label === reason ? null : (
        <Typography.Text type="secondary">reason={reason}</Typography.Text>
      )}
    </Space>
  );
}

const UNMAPPED_COLUMNS: ColumnsType<GapUnmappedEvidence> = [
  {
    title: "来源",
    dataIndex: "source",
    width: 180,
    render: (value: string) => (
      <StatusTag value={value} presentation={presentGapEvidenceSource} />
    ),
  },
  { title: "条数", dataIndex: "count", width: 90 },
  {
    title: "原因",
    dataIndex: "reason",
    render: (value: string) => <UnmappedReason reason={value} />,
  },
];

const GAP_COLUMNS: ColumnsType<GapSummaryItem> = [
  {
    title: "维度",
    dataIndex: "dimension_type",
    width: 100,
    render: (value: string) => presentDimensionType(value),
  },
  { title: "维度值", dataIndex: "dimension_value" },
  {
    title: "未关闭反馈",
    dataIndex: "open_feedback_count",
    width: 120,
  },
  {
    title: "当前 / 目标",
    key: "counts",
    width: 140,
    render: (_, item) =>
      `${item.current_count ?? "—"} / ${item.target_count ?? "—"}`,
  },
  {
    title: "建议补采 Episode",
    dataIndex: "suggested_episode_count",
    width: 150,
  },
  {
    title: "来源证据",
    key: "sources",
    width: 260,
    render: (_, item) => <SourceBadges sources={item.sources} />,
  },
  {
    title: "关联需求",
    key: "requirements",
    render: (_, item) =>
      item.requirement_ids.length > 0 ? (
        <Space direction="vertical" size={0}>
          {item.requirement_ids.map((id) => (
            <Link key={id} to={requirementDetailPath(id)}>
              {id}
            </Link>
          ))}
        </Space>
      ) : (
        "—"
      ),
  },
];

function truncatedSourceLabels(sources: string[]): string {
  return sources
    .map((value) => presentGapEvidenceSource(value).label)
    .join("、");
}

export interface DataGapBodyProps {
  data: GapSummary | null;
  loading: boolean;
  stale: boolean;
  error: ApiError | null;
  onRetry: () => void;
}

/**
 * 数据缺口展示层（不发请求，便于静态渲染测试）。
 *
 * 三个必须如实呈现的事实：
 * 1. `sources` 为空只表示本次扫描没有可归属的证据，不代表缺口为 0；
 * 2. `unmapped` 是归不上维度键的证据，不参与任何缺口计算，也不能静默丢弃；
 * 3. `truncated_sources` 非空时，来源"没有出现"不能读成"没有"。
 */
export function DataGapBody({
  data,
  loading,
  stale,
  error,
  onRetry,
}: DataGapBodyProps) {
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示的是上次成功获取的数据。"
          onRetry={onRetry}
        />
      ) : null}
      {error && !data ? <ApiErrorState error={error} onRetry={onRetry} /> : null}
      {data ? (
        <>
          {data.truncated_sources.length > 0 ? (
            <PageState
              compact
              variant="partial"
              title="来源证据可能不完整"
              description={`服务端在本次扫描中因资源上限截断了以下来源：${truncatedSourceLabels(
                data.truncated_sources,
              )}。这些来源没有出现，不代表没有证据。`}
            />
          ) : null}
          <Table<GapSummaryItem>
            rowKey={(item) => `${item.dimension_type}:${item.dimension_value}`}
            size="small"
            loading={loading}
            columns={GAP_COLUMNS}
            dataSource={data.items}
            pagination={false}
            locale={{ emptyText: "当前没有未关闭的数据缺口" }}
          />
          <Typography.Text type="secondary">
            来源证据只说明缺口结论由哪些反馈、需求、失败用例等支撑，不参与「当前 /
            目标」与建议补采计算；没有来源证据不代表缺口为 0。
          </Typography.Text>
          {data.unmapped.length > 0 ? (
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Typography.Title level={5} style={{ margin: 0 }}>
                未映射证据
              </Typography.Title>
              <Typography.Text type="secondary">
                以下证据无法归因到当前维度键，未计入任何缺口，也不会被静默丢弃。
              </Typography.Text>
              <Table<GapUnmappedEvidence>
                rowKey={(item) => `${item.source}:${item.reason}`}
                size="small"
                columns={UNMAPPED_COLUMNS}
                dataSource={data.unmapped}
                pagination={false}
              />
            </Space>
          ) : null}
          <Typography.Text type="secondary">
            数据新鲜度：{data.freshness === "FRESH" ? "最新" : data.freshness}
          </Typography.Text>
        </>
      ) : null}
    </Space>
  );
}

function DataGapContent({ projectId }: { projectId: string }) {
  const [dimensionType, setDimensionType] = useState<string | undefined>(undefined);
  const load = useCallback(
    (signal: AbortSignal) =>
      modelOpsApi.getDataGaps(projectId, { signal, dimension_type: dimensionType }),
    [projectId, dimensionType],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);

  return (
    <PageContainer
      title="数据缺口"
      description="按模型反馈聚合的采集缺口；建议补采数量由服务端计算，本页不做推断。"
      filters={
        <Space wrap>
          <Typography.Text>维度</Typography.Text>
          <Select
            allowClear
            value={dimensionType}
            placeholder="全部维度"
            style={{ minWidth: 150 }}
            options={GAP_DIMENSION_VALUES.map((value) => ({
              value,
              label: presentDimensionType(value),
            }))}
            onChange={(value) => setDimensionType(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
          刷新
        </Button>
      }
    >
      <DataGapBody
        data={data}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={reload}
      />
    </PageContainer>
  );
}

export default function DataGapPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="数据缺口" description="数据缺口必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据缺口" />
      </PageContainer>
    );
  }
  return <DataGapContent key={projectId} projectId={projectId} />;
}
