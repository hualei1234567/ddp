import { useEffect, useState } from "react";

/**
 * 浏览器在线状态。只用于提示与禁用提交；真正的网络结果仍以请求失败/成功为准。
 * 在不支持 navigator 的渲染环境（如组件静态测试）中默认在线。
 */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(() =>
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return online;
}
