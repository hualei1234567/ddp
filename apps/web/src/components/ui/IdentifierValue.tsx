import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, Space, Tooltip, Typography } from "antd";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import type { To } from "react-router-dom";

import { formatIdentifier } from "../../services/format";

interface IdentifierValueProps {
  value: string | null | undefined;
  /** 用于 Tooltip、复制按钮 aria-label 的字段名，例如“设备 ID”。 */
  label: string;
  /** 已开放的详情路由；未开放的关联对象只提供查看与复制。 */
  to?: To;
  head?: number;
  tail?: number;
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== "undefined" &&
      typeof navigator.clipboard?.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    if (typeof document !== "undefined") {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(area);
      return copied;
    }
  } catch {
    return false;
  }
  return false;
}

/**
 * 长 ID 统一展示：中间省略避免撑破布局，Tooltip 查看完整值，
 * 独立复制按钮提供可访问的触控目标；目标路由已开放时支持跳转。
 */
export default function IdentifierValue({
  value,
  label,
  to,
  head = 8,
  tail = 4,
}: IdentifierValueProps) {
  const [copied, setCopied] = useState(false);
  const text = typeof value === "string" ? value.trim() : "";

  const handleCopy = useCallback(async () => {
    const success = await copyToClipboard(text);
    setCopied(success);
    if (success) {
      window.setTimeout(() => setCopied(false), 2000);
    }
  }, [text]);

  if (text === "") {
    return <Typography.Text type="secondary">—</Typography.Text>;
  }

  const display = (
    <Typography.Text code>{formatIdentifier(text, head, tail)}</Typography.Text>
  );

  return (
    <Space size={4} align="center" className="ddp-identifier">
      <Tooltip
        title={
          <span>
            {label}：{text}
          </span>
        }
      >
        <span className="ddp-identifier-text">
          {to ? (
            <Link
              to={to}
              onClick={(event) => event.stopPropagation()}
            >
              {display}
            </Link>
          ) : (
            display
          )}
        </span>
      </Tooltip>
      <Tooltip title={copied ? "已复制" : "复制完整 ID"}>
        <Button
          type="text"
          size="small"
          className="ddp-icon-button"
          aria-label={`复制${label}`}
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={(event) => {
            // 列表行本身可点击跳转；复制不能触发外层导航。
            event.stopPropagation();
            void handleCopy();
          }}
        />
      </Tooltip>
    </Space>
  );
}
