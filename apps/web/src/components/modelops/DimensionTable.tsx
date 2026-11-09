import { Table } from "antd";

import type { GapDimension } from "../../types/modelops";
import { presentDimensionType } from "../../services/modelopsPresentation";

export default function DimensionTable({ dimensions }: { dimensions: GapDimension[] }) {
  return (
    <Table<GapDimension>
      rowKey={(item) => `${item.dimension_type}:${item.dimension_value}`}
      size="small"
      pagination={false}
      dataSource={dimensions}
      locale={{ emptyText: "未声明缺口维度" }}
      columns={[
        {
          title: "维度",
          dataIndex: "dimension_type",
          width: 100,
          render: (value: string) => presentDimensionType(value),
        },
        { title: "维度值", dataIndex: "dimension_value" },
        {
          title: "当前数量",
          dataIndex: "current_count",
          width: 110,
          render: (value: number | null) => value ?? "—",
        },
        {
          title: "目标数量",
          dataIndex: "target_count",
          width: 110,
          render: (value: number | null) => value ?? "—",
        },
      ]}
    />
  );
}
