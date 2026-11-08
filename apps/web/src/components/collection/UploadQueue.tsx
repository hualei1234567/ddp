import {
  CloudUploadOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Progress,
  Space,
  Tag,
  Typography,
} from "antd";

import IdentifierValue from "../ui/IdentifierValue";
import {
  canCancelUpload,
  canPauseUpload,
  canRefreshVerification,
  canRetryUpload,
  formatBytes,
  formatRemainingTime,
  getUploadQueueStatusLabel,
  uploadProgressPercent,
  uploadSpeedBytesPerSecond,
  type UploadQueueItem,
} from "../../services/uploadQueue";

interface UploadQueueProps {
  items: UploadQueueItem[];
  online: boolean;
  /** 任务状态或权限导致上传不可用时展示的原因。 */
  disabledReason?: string | null;
  onAddFiles: (files: File[]) => void;
  onPause: (itemId: string) => void;
  onResume: (itemId: string) => void;
  onRetry: (itemId: string) => void;
  onCancel: (itemId: string) => void;
  onRefreshVerification: (itemId: string) => void;
  onRemove: (itemId: string) => void;
  /** 注入时间，便于组件测试计算速度与剩余时间。 */
  now?: number;
}

const ACCEPTED_EXTENSIONS =
  ".mcap,.db3,.h5,.hdf5,.mp4,.mov,.parquet,.csv,.json,.zip";

function QueueStatusTag({ item }: { item: UploadQueueItem }) {
  const color =
    item.status === "registered" || item.status === "deduplicated"
      ? "green"
      : item.status === "failed"
        ? "red"
        : item.status === "verifying"
          ? "processing"
          : item.status === "paused" || item.status === "expired"
            ? "orange"
            : "blue";
  return <Tag color={color}>{getUploadQueueStatusLabel(item.status)}</Tag>;
}

function QueueItem({
  item,
  online,
  onPause,
  onResume,
  onRetry,
  onCancel,
  onRefreshVerification,
  onRemove,
  now = Date.now(),
}: Omit<UploadQueueProps, "items" | "onAddFiles" | "disabledReason"> & {
  item: UploadQueueItem;
}) {
  const percent = uploadProgressPercent(item);
  const speed = uploadSpeedBytesPerSecond(item, now);
  const active =
    item.status === "uploading" ||
    item.status === "creating" ||
    item.status === "hashing";
  const canNewSession =
    item.status === "expired" || item.error?.requiresNewSession === true;

  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span className="ddp-break-all">{item.fileName}</span>
          <QueueStatusTag item={item} />
          {item.verificationStatus ? (
            <Tag
              color={
                item.verificationStatus === "VERIFIED"
                  ? "green"
                  : item.verificationStatus === "REJECTED"
                    ? "red"
                    : "processing"
              }
            >
              {item.verificationStatus === "VERIFIED"
                ? "服务端校验通过"
                : item.verificationStatus === "REJECTED"
                  ? "服务端校验未通过"
                  : "服务端校验中"}
            </Tag>
          ) : null}
        </Space>
      }
    >
      <Space direction="vertical" size={8} className="ddp-stack">
        {percent !== null ? (
          <Progress
            percent={percent}
            status={
              item.status === "failed"
                ? "exception"
                : item.status === "registered" ||
                    item.status === "deduplicated"
                  ? "success"
                  : active
                    ? "active"
                    : "normal"
            }
          />
        ) : (
          <Typography.Text type="secondary">
            文件大小未知，无法计算真实百分比；不会伪造进度。
          </Typography.Text>
        )}
        <Typography.Text type="secondary">
          {formatBytes(item.uploadedBytes)} / {formatBytes(item.sizeBytes)}
          {item.totalParts !== null
            ? ` · 已确认 ${item.uploadedPartCount}/${item.totalParts} 个分片`
            : ""}
          {speed !== null ? ` · ${formatBytes(speed)}/s` : ""}
          {active && item.status === "uploading"
            ? ` · 剩余 ${formatRemainingTime(item, now)}`
            : ""}
        </Typography.Text>

        {item.status === "uploading" ? (
          <Typography.Text type="secondary">
            “上传完成”不等于成功；服务端校验通过并登记后才显示已登记。
          </Typography.Text>
        ) : null}

        {item.status === "verifying" ? (
          <Alert
            type="info"
            showIcon
            message="已提交，等待服务端校验"
            description="整文件 Hash、格式与安全扫描由服务端异步完成；校验通过前不会显示成功。"
          />
        ) : null}

        {item.status === "registered" || item.status === "deduplicated" ? (
          <Alert
            type="success"
            showIcon
            message={
              item.status === "deduplicated"
                ? "服务端命中已有资产，本次未新增重复资产"
                : "服务端已登记原始资产"
            }
            description={
              item.deduplicatedAssetId ? (
                <Space wrap>
                  <span>资产 ID：</span>
                  <IdentifierValue
                    value={item.deduplicatedAssetId}
                    label="原始资产 ID"
                  />
                </Space>
              ) : (
                "资产详情页尚未在本任务范围内开放。"
              )
            }
          />
        ) : null}

        {item.error ? (
          <Alert
            type={item.error.cancelled ? "warning" : "error"}
            showIcon
            message={item.error.summary}
            description={
              <span>
                {item.error.hint}；错误码：<code>{item.error.code}</code>
                {item.error.missingPartNumbers.length > 0
                  ? `；缺失分片：${item.error.missingPartNumbers.join("、")}`
                  : ""}
                {item.error.requestId ? (
                  <>
                    ；request_id：<code>{item.error.requestId}</code>
                  </>
                ) : null}
              </span>
            }
          />
        ) : null}

        <Space wrap>
          {canPauseUpload(item) ? (
            <Button
              icon={<PauseCircleOutlined />}
              onClick={() => onPause(item.id)}
            >
              暂停
            </Button>
          ) : null}
          {item.status === "paused" ? (
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={!online}
              onClick={() => onResume(item.id)}
            >
              继续上传
            </Button>
          ) : null}
          {canRetryUpload(item) || canNewSession ? (
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              disabled={!online}
              onClick={() => onRetry(item.id)}
            >
              {canNewSession ? "新建会话并重传" : "重试失败分片"}
            </Button>
          ) : null}
          {canRefreshVerification(item) ? (
            <Button
              icon={<ReloadOutlined />}
              disabled={!online}
              onClick={() => onRefreshVerification(item.id)}
            >
              刷新校验状态
            </Button>
          ) : null}
          {canCancelUpload(item) ? (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={() => onCancel(item.id)}
            >
              取消上传
            </Button>
          ) : null}
          {item.status === "failed" ||
          item.status === "cancelled" ||
          item.status === "registered" ||
          item.status === "deduplicated" ? (
            <Button
              type="text"
              icon={<DeleteOutlined />}
              onClick={() => onRemove(item.id)}
            >
              移除记录
            </Button>
          ) : null}
        </Space>
        {item.status === "paused" ? (
          <Typography.Text type="secondary">
            已暂停；本地文件保留。继续时会先向服务端确认已传分片，不重传已确认分片。
          </Typography.Text>
        ) : null}
        {!online && item.status === "paused" ? (
          <Typography.Text type="secondary">
            当前离线：恢复网络后可继续上传。
          </Typography.Text>
        ) : null}
      </Space>
    </Card>
  );
}

/**
 * 上传队列展示与操作。组件不直接调用 fetch；上传编排由
 * `useUploadQueue` 负责，便于页面复用与状态测试。
 */
export default function UploadQueue({
  items,
  online,
  disabledReason,
  onAddFiles,
  onPause,
  onResume,
  onRetry,
  onCancel,
  onRefreshVerification,
  onRemove,
  now = Date.now(),
}: UploadQueueProps) {
  const uploadDisabled = !online || Boolean(disabledReason);

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {!online ? (
        <Alert
          type="warning"
          showIcon
          message="当前离线"
          description="上传队列与本地检查草稿保留；恢复网络后可继续断点续传。离线时不允许发起提交。"
        />
      ) : null}
      {disabledReason ? (
        <Alert
          type="info"
          showIcon
          message="当前不能发起上传"
          description={disabledReason}
        />
      ) : null}

      <div className="ddp-upload-actions">
        <label
          className={
            uploadDisabled
              ? "ddp-file-picker ddp-file-picker-disabled"
              : "ddp-file-picker"
          }
        >
          <input
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS}
            className="ddp-file-input"
            disabled={uploadDisabled}
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              if (files.length > 0) {
                onAddFiles(files);
              }
            }}
          />
          <Button
            type="primary"
            icon={<CloudUploadOutlined />}
            disabled={uploadDisabled}
          >
            选择文件上传
          </Button>
        </label>
        <Typography.Text type="secondary">
          扩展名仅用于格式提示；类型、大小、内容签名与 Hash 以服务端校验为准。
        </Typography.Text>
      </div>

      {items.length === 0 ? (
        <Alert
          type="info"
          showIcon
          message="上传队列为空"
          description="选择文件后开始分片上传；页面刷新不会保留本地文件，但已确认分片可在重新选择同一文件后按断点续传。"
        />
      ) : (
        items.map((item) => (
          <QueueItem
            key={item.id}
            item={item}
            online={online}
            now={now}
            onPause={onPause}
            onResume={onResume}
            onRetry={onRetry}
            onCancel={onCancel}
            onRefreshVerification={onRefreshVerification}
            onRemove={onRemove}
          />
        ))
      )}
    </Space>
  );
}
