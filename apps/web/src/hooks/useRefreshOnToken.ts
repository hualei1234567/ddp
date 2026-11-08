import { useEffect, useRef } from "react";

/**
 * 当父级刷新令牌变化时重新加载子数据。
 *
 * 初次挂载不额外请求（子组件自身会完成首屏加载）；令牌变化说明用户
 * 主动刷新了详情页，此时所有已挂载的 Tab 都应同步拉取最新数据。
 */
export function useRefreshOnToken(token: number, reload: () => void): void {
  const previousToken = useRef(token);

  useEffect(() => {
    if (previousToken.current === token) {
      return;
    }
    previousToken.current = token;
    reload();
  }, [token, reload]);
}
