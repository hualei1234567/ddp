import { Button } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";

import PageState from "../components/ui/PageState";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  failed: boolean;
}

/** 兜住渲染异常，避免整页白屏；错误详情只写控制台，不向用户暴露堆栈。 */
export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("DDP 界面渲染失败", error, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="ddp-error-boundary">
          <PageState
            variant="error"
            title="界面出现异常"
            description="请刷新页面重试；若问题持续，请联系管理员并提供发生时间与操作步骤。"
            action={
              <Button
                type="primary"
                onClick={() => window.location.reload()}
              >
                刷新页面
              </Button>
            }
          />
        </div>
      );
    }

    return this.props.children;
  }
}
