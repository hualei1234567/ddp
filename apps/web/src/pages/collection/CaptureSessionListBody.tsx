import { Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import type { ApiError } from "../../services/api";
import { presentCaptureSessionStatus } from "../../services/captureSessionPresentation";
import { formatDateTime } from "../../services/format";
import type { CaptureSession } from "../../types/captureSession";

interface Props { items: CaptureSession[]; loading: boolean; refreshing: boolean; loadingMore: boolean; hasMore: boolean; error: ApiError | null; loadMoreError: ApiError | null; stale: boolean; onRetry: () => void; onLoadMore: () => void; }
const columns: TableColumnsType<CaptureSession> = [
  { title: "会话编码", dataIndex: "code", render: (value: string, row) => <Link to={`/collection/capture-sessions/${encodeURIComponent(row.session_id)}`}>{value}</Link> },
  { title: "状态", dataIndex: "status", render: (value: string) => <StatusTag value={value} presentation={presentCaptureSessionStatus} /> },
  { title: "任务 ID", dataIndex: "task_id", render: (value: string | null) => value ?? "—" },
  { title: "机器人 ID", dataIndex: "robot_id", render: (value: string | null) => value ?? "—" },
  { title: "开始时间", dataIndex: "started_at", render: formatDateTime },
  { title: "更新时间", dataIndex: "updated_at", render: formatDateTime },
];

export default function CaptureSessionListBody(props: Props) {
  if (props.loading) return <PageState variant="loading" title="正在加载采集会话" />;
  if (props.error && props.items.length === 0) return <ApiErrorState error={props.error} onRetry={props.onRetry} />;
  return <Space direction="vertical" size={12} className="ddp-stack">
    {props.stale ? <PageState compact variant="stale" description="最近一次刷新失败，展示的是上次成功获取的数据。" onRetry={props.onRetry} /> : null}
    {props.items.length === 0 ? <PageState variant="empty" title="暂无采集会话" description="当前项目尚无采集执行记录。" /> : <><Typography.Text type="secondary">已加载 {props.items.length} 个会话</Typography.Text><Table rowKey="session_id" columns={columns} dataSource={props.items} pagination={false} scroll={{ x: 900 }} /></>}
    <LoadMoreBar loadedCount={props.items.length} unit="个会话" hasMore={props.hasMore} loadingMore={props.loadingMore} disabled={props.loading || props.refreshing} error={props.loadMoreError} onLoadMore={props.onLoadMore} />
  </Space>;
}
