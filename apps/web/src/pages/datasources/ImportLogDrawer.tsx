/** 导入步骤日志抽屉：只读、可重试、三态齐全（Loading / Empty / Error）。 */

import {
  Alert,
  Drawer,
  Empty,
  Progress,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";

import ApiErrorState from "../../components/ui/ApiErrorState";
import { useRequest } from "../../hooks/useRequest";
import { dataSourceApi } from "../../services/datasources";
import { formatDateTime } from "../../services/format";
import {
  presentImportProgress,
  presentImportStage,
} from "../../services/importProgress";
import type {
  DataSourceImport,
  ImportLog,
  ImportLogLevel,
} from "../../types/datasource";

const LEVEL_COLORS: Record<ImportLogLevel, string> = {
  INFO: "blue",
  WARNING: "gold",
  ERROR: "red",
};

const LOG_COLUMNS: ColumnsType<ImportLog> = [
  {
    title: "时间",
    dataIndex: "occurred_at",
    width: 170,
    render: (value: string) => formatDateTime(value),
  },
  {
    title: "阶段",
    dataIndex: "stage",
    width: 80,
    render: (value: ImportLog["stage"]) => presentImportStage(value),
  },
  {
    title: "等级",
    dataIndex: "level",
    width: 80,
    render: (value: ImportLogLevel) => (
      <Tag color={LEVEL_COLORS[value]}>{value}</Tag>
    ),
  },
  {
    title: "尝试",
    dataIndex: "attempt",
    width: 60,
  },
  {
    title: "消息",
    dataIndex: "message",
    render: (value: string) => (
      <Typography.Text style={{ whiteSpace: "pre-wrap" }}>{value}</Typography.Text>
    ),
  },
];

function ImportLogContent({ target }: { target: DataSourceImport }) {
  const { data, error, loading, stale, reload } = useRequest((signal) =>
    dataSourceApi.listImportLogs(target.project_id, target.import_id, {
      signal,
      limit: 200,
    }),
  );
  const progress = presentImportProgress(
    target.processed_items,
    target.total_items,
  );

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Tag>{presentImportStage(target.stage)}</Tag>
        {progress.measured ? (
          <Progress
            percent={progress.percent ?? 0}
            size="small"
            style={{ minWidth: 200 }}
            format={() => progress.label}
          />
        ) : (
          <Typography.Text type="secondary">进度：未测量</Typography.Text>
        )}
        <Typography.Text type="secondary">状态：{target.status}</Typography.Text>
      </Space>

      {target.error_message ? (
        <Alert
          type="error"
          showIcon
          message="错误详情"
          description={target.error_message}
        />
      ) : null}

      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的日志。
        </Typography.Text>
      ) : null}

      {loading && data === null ? (
        <Spin tip="正在加载日志…">
          <div style={{ minHeight: 120 }} />
        </Spin>
      ) : null}

      {error && data === null ? (
        <ApiErrorState compact error={error} onRetry={reload} />
      ) : null}

      {data !== null ? (
        <>
          {data.truncated ? (
            <Alert
              type="warning"
              showIcon
              message="日志超过展示上限"
              description="仅返回最近的 200 条步骤日志，更早的日志未展示。"
            />
          ) : null}
          {data.items.length === 0 ? (
            <Empty description="暂无步骤日志" />
          ) : (
            <Table<ImportLog>
              rowKey="log_id"
              size="small"
              loading={loading}
              columns={LOG_COLUMNS}
              dataSource={data.items}
              pagination={false}
              scroll={{ x: 640 }}
            />
          )}
        </>
      ) : null}
    </Space>
  );
}

export default function ImportLogDrawer({
  target,
  onClose,
}: {
  target: DataSourceImport | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={target ? `导入日志 · ${target.import_id.slice(0, 8)}` : "导入日志"}
      width={760}
      open={target !== null}
      onClose={onClose}
      destroyOnClose
    >
      {target ? <ImportLogContent key={target.import_id} target={target} /> : null}
    </Drawer>
  );
}
