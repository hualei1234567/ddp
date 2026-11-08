import { ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useMemo } from "react";
import type { ReactNode } from "react";

import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageState from "../../components/ui/PageState";
import { useRequest } from "../../hooks/useRequest";
import { formatDateTime, formatTimestamp } from "../../services/format";
import { lineageApi } from "../../services/lineage";
import {
  LINEAGE_DIRECTION_LABELS,
  LINEAGE_NODE_LABELS,
  LINEAGE_RELATION_LABELS,
  lineageNodePath,
} from "../../services/lineagePresentation";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  LineageDirection,
  LineageEdge,
  LineageNodeType,
} from "../../types/collection";

interface LineagePanelProps {
  projectId: string;
  episodeId: string;
  direction: LineageDirection;
  /** 离线时保留已加载数据，但禁用重试。 */
  online: boolean;
}

function buildColumns(
  projectId: string,
): TableColumnsType<LineageEdge> {
  return [
    {
      title: "起点",
      key: "from",
      width: 260,
      render: (_value, edge) => {
        const to = lineageNodePath(
          projectId,
          edge.from_node_type,
          edge.from_node_id,
        );
        return (
          <Space direction="vertical" size={0}>
            <Tag>{LINEAGE_NODE_LABELS[edge.from_node_type]}</Tag>
            <IdentifierValue
              value={edge.from_node_id}
              label={LINEAGE_NODE_LABELS[edge.from_node_type] + " ID"}
              to={to ?? undefined}
            />
          </Space>
        );
      },
    },
    {
      title: "关系",
      dataIndex: "relation",
      width: 150,
      render: (value: LineageEdge["relation"]) => (
        <Tag color="blue">{LINEAGE_RELATION_LABELS[value]}</Tag>
      ),
    },
    {
      title: "终点",
      key: "to",
      width: 260,
      render: (_value, edge) => {
        const to = lineageNodePath(
          projectId,
          edge.to_node_type,
          edge.to_node_id,
        );
        return (
          <Space direction="vertical" size={0}>
            <Tag>{LINEAGE_NODE_LABELS[edge.to_node_type]}</Tag>
            <IdentifierValue
              value={edge.to_node_id}
              label={LINEAGE_NODE_LABELS[edge.to_node_type] + " ID"}
              to={to ?? undefined}
            />
          </Space>
        );
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 170,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "操作者",
      dataIndex: "created_by",
      width: 210,
      render: (value: string) => (
        <IdentifierValue value={value} label="操作者 ID" />
      ),
    },
  ];
}

function nodeTypesOf(edges: LineageEdge[]): LineageNodeType[] {
  const seen = new Set<LineageNodeType>();
  for (const edge of edges) {
    seen.add(edge.from_node_type);
    seen.add(edge.to_node_type);
  }
  return [...seen];
}

/**
 * 单个方向的 Episode 血缘。正向/反向各自独立请求与重试，
 * 一侧失败不影响另一侧已成功的数据。
 */
export default function LineagePanel({
  projectId,
  episodeId,
  direction,
  online,
}: LineagePanelProps) {
  const load = useCallback(
    (signal: AbortSignal) =>
      lineageApi.getEpisodeLineage(projectId, episodeId, {
        direction,
        depth: 1,
        signal,
      }),
    [projectId, episodeId, direction],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });
  const columns = useMemo(() => buildColumns(projectId), [projectId]);
  const title = `${LINEAGE_DIRECTION_LABELS[direction]}血缘`;

  let body: ReactNode;
  if (phase === "forbidden") {
    body = error ? <ApiErrorState error={error} compact /> : null;
  } else if (phase === "not-found") {
    body = (
      <PageState
        variant="empty"
        compact
        title="节点不存在或无权查看"
        description="服务端未找到该 Episode；跨项目资源统一不可见。"
      />
    );
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        compact
        title={`正在加载${title}`}
        description="正在读取当前项目内的血缘边。"
      />
    );
  } else if (phase === "error") {
    body = error ? (
      <ApiErrorState error={error} onRetry={reload} compact />
    ) : null;
  } else if (!data || data.items.length === 0) {
    body = (
      <PageState
        variant="empty"
        compact
        title={`暂无${title}`}
        description="服务端未返回该方向的边；可能是尚未完成接入，或当前深度无关联节点。"
        action={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={refreshing}
            disabled={!online}
            onClick={reload}
          >
            重试
          </Button>
        }
      />
    );
  } else {
    const nodeTypes = nodeTypesOf(data.items);
    body = (
      <Space direction="vertical" size={8} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功的血缘数据。"
            onRetry={reload}
          />
        ) : null}
        <Space wrap>
          <Tag color="blue">共 {data.items.length} 条边</Tag>
          {nodeTypes.map((type) => (
            <Tag key={type}>{LINEAGE_NODE_LABELS[type]}</Tag>
          ))}
          <Typography.Text type="secondary">
            深度 1 · 更新于 {formatTimestamp(updatedAt)}
          </Typography.Text>
        </Space>
        <Table<LineageEdge>
          rowKey="edge_id"
          columns={columns}
          dataSource={data.items}
          pagination={false}
          scroll={{ x: 1050 }}
        />
      </Space>
    );
  }

  return (
    <Card
      title={title}
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={refreshing}
          disabled={!online || loading}
          onClick={reload}
        >
          刷新
        </Button>
      }
    >
      {body}
    </Card>
  );
}
