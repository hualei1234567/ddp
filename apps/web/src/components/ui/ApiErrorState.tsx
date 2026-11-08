import type { ReactNode } from "react";

import type { ApiError } from "../../services/api";
import PageState from "./PageState";

interface ApiErrorStateProps {
  error: ApiError;
  onRetry?: () => void;
  compact?: boolean;
}

function ErrorDescription({ error }: { error: ApiError }): ReactNode {
  return (
    <span>
      {error.message}
      {error.code ? (
        <>
          ；错误码：<code>{error.code}</code>
        </>
      ) : null}
      {error.requestId ? (
        <>
          ；request_id：<code>{error.requestId}</code>
        </>
      ) : null}
    </span>
  );
}

/**
 * 接口错误的统一展示：403 映射为 Forbidden，其它映射为 Error。
 * 文案保留错误码与 request_id，便于现场定位；不展示堆栈或内部路径。
 */
export default function ApiErrorState({
  error,
  onRetry,
  compact = false,
}: ApiErrorStateProps) {
  if (error.isForbidden) {
    return (
      <PageState
        variant="forbidden"
        compact={compact}
        description={<ErrorDescription error={error} />}
      />
    );
  }

  return (
    <PageState
      variant="error"
      compact={compact}
      description={<ErrorDescription error={error} />}
      onRetry={onRetry}
    />
  );
}
