import { Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";

import { buildEpisodeDetailPath } from "../../services/collectionUrl";
import type { SelectionPreview, SelectionPreviewItem } from "../../types/dataset";

const DECISION_COLORS: Record<string, string> = {
  SELECTED: "green",
  EXCLUDED: "red",
  DUPLICATED: "orange",
};

/**
 * 选择预览的解释面板：把服务端已经返回的「排除原因」与「逐条决策」渲染出来，
 * 并让每条 Episode 可跳回详情页核对。数据全部来自 preview API，不在这里
 * 重新推导选择规则——规则权威实现只有后端一份。
 */
export default function SelectionPreviewDetails({
  projectId,
  preview,
}: {
  projectId: string;
  preview: SelectionPreview;
}) {
  const columns: ColumnsType<SelectionPreviewItem> = [
    {
      title: "决策",
      dataIndex: "decision",
      width: 110,
      render: (value: string) => (
        <Tag color={DECISION_COLORS[value] ?? "default"}>{value}</Tag>
      ),
    },
    {
      title: "Episode",
      dataIndex: "episode_id",
      render: (value: string) => (
        <Link to={buildEpisodeDetailPath(projectId, value)}>
          <Typography.Text code>{value}</Typography.Text>
        </Link>
      ),
    },
    {
      title: "划分",
      dataIndex: "split",
      width: 90,
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "原因",
      dataIndex: "reason",
      render: (value: string | null) => value ?? "—",
    },
  ];

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text strong>排除原因</Typography.Text>
      {preview.exclusion_summary.length === 0 ? (
        <Typography.Text type="secondary">没有排除条目。</Typography.Text>
      ) : (
        preview.exclusion_summary.map((entry) => (
          <Space key={entry.code} wrap size={4}>
            <Tag>
              {entry.code}：{entry.count}
            </Tag>
            {entry.sample_episode_ids.map((episodeId) => (
              <Link key={episodeId} to={buildEpisodeDetailPath(projectId, episodeId)}>
                <Typography.Text code>{episodeId}</Typography.Text>
              </Link>
            ))}
          </Space>
        ))
      )}
      <Typography.Text strong>逐条决策</Typography.Text>
      <Table<SelectionPreviewItem>
        rowKey={(item) => `${item.decision}:${item.episode_id}`}
        size="small"
        columns={columns}
        dataSource={preview.items_sample}
        pagination={false}
        locale={{ emptyText: "没有逐条决策样例。" }}
      />
    </Space>
  );
}
