import { Alert, Button, Result, Skeleton, Spin, Typography } from "antd";
import type { ReactNode } from "react";

import {
  PAGE_STATE_CONTENT,
  type PageStateVariant,
} from "./pageStateConfig";

interface PageStateProps {
  variant: PageStateVariant;
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  onRetry?: () => void;
  /** 卡片或表格内部使用的紧凑形态。 */
  compact?: boolean;
}

const RESULT_STATUS: Record<
  PageStateVariant,
  "success" | "error" | "info" | "warning" | "403"
> = {
  loading: "info",
  empty: "info",
  error: "error",
  forbidden: "403",
  partial: "warning",
  stale: "warning",
  disabled: "info",
  pending: "info",
  success: "success",
  failed: "error",
};

const ALERT_TYPE: Record<
  PageStateVariant,
  "success" | "info" | "warning" | "error"
> = {
  loading: "info",
  empty: "info",
  error: "error",
  forbidden: "error",
  partial: "warning",
  stale: "warning",
  disabled: "info",
  pending: "info",
  success: "success",
  failed: "error",
};

function buildAction(
  action: ReactNode | undefined,
  onRetry: (() => void) | undefined,
): ReactNode {
  if (!action && !onRetry) {
    return undefined;
  }
  return (
    <div className="ddp-state-actions">
      {action}
      {onRetry ? <Button onClick={onRetry}>重试</Button> : null}
    </div>
  );
}

/**
 * 统一页面状态：Loading / Empty / Error / Forbidden / Partial / Stale /
 * Disabled / Pending / Success / Failed。
 */
export default function PageState({
  variant,
  title,
  description,
  action,
  onRetry,
  compact = false,
}: PageStateProps) {
  const content = PAGE_STATE_CONTENT[variant];
  const resolvedTitle = title ?? content.title;
  const resolvedDescription = description ?? content.description;

  if (variant === "loading") {
    return (
      <div
        className={compact ? "ddp-state ddp-state-compact" : "ddp-state"}
        role="status"
        aria-live="polite"
      >
        {compact ? <Spin /> : <Skeleton active title paragraph={{ rows: 4 }} />}
        <Typography.Text type="secondary" className="ddp-state-caption">
          {resolvedTitle}
        </Typography.Text>
      </div>
    );
  }

  const actions = buildAction(action, onRetry);

  if (compact) {
    return (
      <Alert
        className="ddp-state-inline"
        type={ALERT_TYPE[variant]}
        showIcon
        message={resolvedTitle}
        description={resolvedDescription}
        action={actions}
      />
    );
  }

  return (
    <div className="ddp-state">
      <Result
        status={RESULT_STATUS[variant]}
        title={resolvedTitle}
        subTitle={resolvedDescription}
        extra={actions}
      />
    </div>
  );
}
