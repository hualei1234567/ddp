import { Alert, Button, Space, Typography } from "antd";

import type { ApiError } from "../../services/api";

interface LoadMoreBarProps {
  loadedCount: number;
  /** 例如“台设备”“条传感器记录”。 */
  unit: string;
  hasMore: boolean;
  loadingMore: boolean;
  /** 首屏加载或刷新进行中时禁用，避免并发分页。 */
  disabled?: boolean;
  error: ApiError | null;
  onLoadMore: () => void;
  /** 手机端使用整行按钮，扩大触控目标。 */
  block?: boolean;
}

/**
 * 游标分页的加载更多区域：明确已加载范围、下一页状态与失败重试。
 * 首次加载失败由页面主状态展示，不在这里重复。
 */
export default function LoadMoreBar({
  loadedCount,
  unit,
  hasMore,
  loadingMore,
  disabled = false,
  error,
  onLoadMore,
  block = false,
}: LoadMoreBarProps) {
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Typography.Text type="secondary">
        {hasMore
          ? `已加载 ${loadedCount} ${unit}，还有更多未加载。`
          : `已加载 ${loadedCount} ${unit}，已全部加载。`}
      </Typography.Text>
      {error ? (
        <Alert
          type="error"
          showIcon
          message="加载更多失败"
          description={error.message}
          action={
            <Button size="small" onClick={onLoadMore} disabled={disabled}>
              重试
            </Button>
          }
        />
      ) : null}
      {hasMore ? (
        <Button
          block={block}
          loading={loadingMore}
          disabled={disabled}
          onClick={onLoadMore}
        >
          加载更多
        </Button>
      ) : null}
    </Space>
  );
}
