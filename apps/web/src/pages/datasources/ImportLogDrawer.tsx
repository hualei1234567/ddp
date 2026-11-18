/** 导入步骤日志抽屉：只读、可翻更早、RUNNING 时自动刷新详情。 */

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
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
import { useCallback, useMemo } from "react";

import ApiErrorState from "../../components/ui/ApiErrorState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { usePolling } from "../../hooks/usePolling";
import { useRequest } from "../../hooks/useRequest";
import { dataSourceApi } from "../../services/datasources";
import { formatDateTime } from "../../services/format";
import {
  presentImportProgress,
  presentImportStage,
} from "../../services/importProgress";
import { IMPORT_POLL_INTERVAL_MS } from "../../services/importPolling";
import type {
  DataSourceImport,
  ImportLog,
  ImportLogLevel,
} from "../../types/datasource";

const LOG_PAGE_SIZE = 100;

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
  // 列表页传来的 target 可能是挂载前的快照；抽屉内重新取一次详情，
  // 并在 RUNNING 时按固定间隔刷新进度，终态自动停止。
  const detailRequest = useRequest((signal) =>
    dataSourceApi.getImport(target.project_id, target.import_id, { signal }),
  );
  const detail = detailRequest.data ?? target;
  const running = detail.status === "RUNNING";

  usePolling({
    enabled: running,
    intervalMs: IMPORT_POLL_INTERVAL_MS,
    tick: detailRequest.reload,
  });

  const loadLogs = useCallback(
    (options: CursorPageRequestOptions) =>
      dataSourceApi
        .listImportLogs(target.project_id, target.import_id, {
          cursor: options.cursor,
          signal: options.signal,
          limit: LOG_PAGE_SIZE,
        })
        .then((page) => ({
          items: page.items,
          next_cursor: page.next_cursor,
        })),
    [target.project_id, target.import_id],
  );
  const {
    items,
    hasMore,
    loading: logsLoading,
    refreshing: logsRefreshing,
    loadingMore,
    error: logsError,
    loadMoreError,
    stale: logsStale,
    reload: reloadLogs,
    loadMore,
  } = useCursorPage(loadLogs, (log) => log.log_id);

  // 服务端按时间正序返回。最新一页放最上面，加载更早时追加在底部，
  // 视觉顺序始终是「新 → 旧」，也让「加载更多」出现在自然的位置。
  const rows = useMemo(() => [...items].reverse(), [items]);

  const progress = presentImportProgress(
    detail.processed_items,
    detail.total_items,
  );

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Tag>{presentImportStage(detail.stage)}</Tag>
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
        <Typography.Text type="secondary">状态：{detail.status}</Typography.Text>
        {running ? (
          <Typography.Text type="secondary">
            每 {IMPORT_POLL_INTERVAL_MS / 1000} 秒自动刷新进度
          </Typography.Text>
        ) : null}
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={logsRefreshing}
          onClick={reloadLogs}
        >
          刷新日志
        </Button>
      </Space>

      {detail.error_message ? (
        <Alert
          type="error"
          showIcon
          message="错误详情"
          description={detail.error_message}
        />
      ) : null}

      {detailRequest.error ? (
        <Typography.Text type="warning">
          详情刷新失败，展示的是打开抽屉时的快照。
        </Typography.Text>
      ) : null}

      {logsStale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的日志。
        </Typography.Text>
      ) : null}

      {logsLoading && items.length === 0 ? (
        <Spin tip="正在加载日志…">
          <div style={{ minHeight: 120 }} />
        </Spin>
      ) : null}

      {logsError && items.length === 0 ? (
        <ApiErrorState compact error={logsError} onRetry={reloadLogs} />
      ) : null}

      {items.length === 0 && !logsLoading && !logsError ? (
        <Empty description="暂无步骤日志" />
      ) : null}

      {rows.length > 0 ? (
        <>
          <Table<ImportLog>
            rowKey="log_id"
            size="small"
            loading={logsLoading}
            columns={LOG_COLUMNS}
            dataSource={rows}
            pagination={false}
            scroll={{ x: 640 }}
          />
          {hasMore ? (
            <Button block loading={loadingMore} onClick={loadMore}>
              加载更多
            </Button>
          ) : null}
          {loadMoreError ? (
            <Typography.Text type="danger">
              加载更多失败：{loadMoreError.message}
            </Typography.Text>
          ) : null}
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
