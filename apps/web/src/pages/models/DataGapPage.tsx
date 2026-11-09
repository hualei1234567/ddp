import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { modelOpsApi } from "../../services/modelops";
import { presentDimensionType } from "../../services/modelopsPresentation";
import { requirementDetailPath } from "../../services/modelopsUrl";
import { GAP_DIMENSION_VALUES, type GapSummaryItem } from "../../types/modelops";

function DataGapContent({ projectId }: { projectId: string }) {
  const [dimensionType, setDimensionType] = useState<string | undefined>(undefined);
  const load = useCallback(
    (signal: AbortSignal) =>
      modelOpsApi.getDataGaps(projectId, { signal, dimension_type: dimensionType }),
    [projectId, dimensionType],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);

  const columns: ColumnsType<GapSummaryItem> = [
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
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，展示的是上次成功获取的数据。"
          onRetry={reload}
        />
      ) : null}
      {error && !data ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {data ? (
        <Table<GapSummaryItem>
          rowKey={(item) => `${item.dimension_type}:${item.dimension_value}`}
          size="small"
          loading={loading}
          columns={columns}
          dataSource={data.items}
          pagination={false}
          locale={{ emptyText: "当前没有未关闭的数据缺口" }}
        />
      ) : null}
      {data ? (
        <Typography.Text type="secondary">
          数据新鲜度：{data.freshness === "FRESH" ? "最新" : data.freshness}
        </Typography.Text>
      ) : null}
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
