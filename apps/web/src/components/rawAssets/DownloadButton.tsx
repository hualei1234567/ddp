import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Typography } from "antd";
import { useCallback, useState } from "react";

import {
  describeDownloadFailure,
  describeExpiredDownloadLink,
  isDownloadableStatus,
  isDownloadLinkExpired,
  type DownloadFeedback,
} from "../../services/downloadPresentation";
import { formatDateTime } from "../../services/format";
import { rawAssetsApi } from "../../services/rawAssets";
import type { RawAssetStatus } from "../../types/collection";

interface DownloadButtonProps {
  projectId: string;
  assetId: string;
  status: RawAssetStatus;
  online: boolean;
}

function openSignedUrl(url: string): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

/**
 * RawAsset 下载：每次点击都向服务端申请新的短期签名 URL，不持久化 URL。
 * 如果服务端返回的 expires_at 已过期（时钟偏差等），拒绝打开并提示重新获取。
 */
export default function DownloadButton({
  projectId,
  assetId,
  status,
  online,
}: DownloadButtonProps) {
  const [downloading, setDownloading] = useState(false);
  const [feedback, setFeedback] = useState<DownloadFeedback | null>(null);
  const [lastExpiresAt, setLastExpiresAt] = useState<number | null>(null);

  const statusAllowsDownload = isDownloadableStatus(status);
  const disabledReason = !online
    ? "当前离线，无法获取下载地址。"
    : !statusAllowsDownload
      ? "资产当前状态不允许下载；需服务端校验为 VALID/ARCHIVED。"
      : null;

  const handleDownload = useCallback(async () => {
    setDownloading(true);
    setFeedback(null);
    try {
      const signed = await rawAssetsApi.getDownloadUrl(projectId, assetId);
      const expiresAt = Date.parse(signed.expires_at);
      setLastExpiresAt(expiresAt);
      if (isDownloadLinkExpired(expiresAt)) {
        setFeedback(describeExpiredDownloadLink());
        return;
      }
      openSignedUrl(signed.url);
      setFeedback({
        kind: "info",
        title: "已请求下载",
        description: `签名地址有效期至 ${formatDateTime(signed.expires_at)}；URL 未写入本地存储。`,
      });
    } catch (caught) {
      setFeedback(describeDownloadFailure(caught));
    } finally {
      setDownloading(false);
    }
  }, [assetId, projectId]);

  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Space wrap>
        <Button
          type="primary"
          icon={<DownloadOutlined />}
          loading={downloading}
          disabled={disabledReason !== null || downloading}
          onClick={() => void handleDownload()}
        >
          下载原始资产
        </Button>
        {disabledReason ? (
          <Typography.Text type="secondary">{disabledReason}</Typography.Text>
        ) : (
          <Typography.Text type="secondary">
            下载地址短期有效；每次点击重新获取，不在本地持久化。
          </Typography.Text>
        )}
      </Space>
      {lastExpiresAt !== null ? (
        <Typography.Text type="secondary">
          最近获取的地址有效期至 {formatDateTime(new Date(lastExpiresAt).toISOString())}
        </Typography.Text>
      ) : null}
      {feedback ? (
        <Alert
          type={
            feedback.kind === "warning"
              ? "warning"
              : feedback.kind === "info"
                ? "info"
                : "error"
          }
          showIcon
          message={feedback.title}
          description={feedback.description}
          action={
            feedback.kind === "error" && online ? (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => void handleDownload()}
              >
                重试
              </Button>
            ) : undefined
          }
        />
      ) : null}
    </Space>
  );
}
