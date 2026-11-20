import { Space, Table } from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageState from "../../components/ui/PageState";
import type { ApiError } from "../../services/api";
import { captureSessionArtifactPath } from "../../services/captureSessionPresentation";
import { formatDateTime } from "../../services/format";
import type { CaptureSessionArtifact } from "../../types/captureSession";

interface Props { items: CaptureSessionArtifact[]; loading: boolean; refreshing: boolean; loadingMore: boolean; hasMore: boolean; error: ApiError | null; loadMoreError: ApiError | null; stale: boolean; onRetry: () => void; onLoadMore: () => void; }
const columns: TableColumnsType<CaptureSessionArtifact> = [
  { title: "类型", dataIndex: "kind", render: (value: string) => value === "RAW_ASSET" ? "原始资产" : "Episode" },
  { title: "编码", dataIndex: "code", render: (value: string, row) => <Link to={captureSessionArtifactPath(row.kind, row.artifact_id)}>{value}</Link> },
  { title: "状态", dataIndex: "status" },
  { title: "创建时间", dataIndex: "created_at", render: formatDateTime },
];
export default function CaptureSessionArtifactList(props: Props) {
  if (props.loading) return <PageState compact variant="loading" title="正在加载会话产物" />;
  if (props.error && props.items.length === 0) return <ApiErrorState compact error={props.error} onRetry={props.onRetry} />;
  return <Space direction="vertical" size={12} className="ddp-stack">
    {props.stale ? <PageState compact variant="stale" description="产物刷新失败，展示上次成功获取的数据。" onRetry={props.onRetry} /> : null}
    {props.items.length === 0 ? <PageState compact variant="empty" title="尚无产物" description="会话存在但尚未产出 RawAsset 或 Episode，这是合法状态。" /> : <Table rowKey={(row) => `${row.kind}:${row.artifact_id}`} columns={columns} dataSource={props.items} pagination={false} scroll={{ x: 620 }} />}
    <LoadMoreBar loadedCount={props.items.length} unit="个产物" hasMore={props.hasMore} loadingMore={props.loadingMore} disabled={props.loading || props.refreshing} error={props.loadMoreError} onLoadMore={props.onLoadMore} />
  </Space>;
}
