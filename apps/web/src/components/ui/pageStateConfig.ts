export type PageStateVariant =
  | "loading"
  | "empty"
  | "error"
  | "forbidden"
  | "partial"
  | "stale"
  | "disabled"
  | "pending"
  | "success"
  | "failed";

export type PageStateTone = "info" | "success" | "warning" | "error";

export interface PageStateContent {
  title: string;
  description: string;
  tone: PageStateTone;
}

/**
 * 页面状态统一文案，对应规范 §18.3 要求的状态覆盖。
 * 业务页面只传上下文补充说明，不各自复制一套标题。
 */
export const PAGE_STATE_CONTENT: Record<PageStateVariant, PageStateContent> = {
  loading: {
    title: "正在加载",
    description: "正在获取数据，请稍候。",
    tone: "info",
  },
  empty: {
    title: "暂无数据",
    description: "当前条件下没有可展示的数据，可调整筛选条件或稍后重试。",
    tone: "info",
  },
  error: {
    title: "加载失败",
    description: "数据获取失败，请检查网络或服务状态后重试。",
    tone: "error",
  },
  forbidden: {
    title: "无权访问",
    description: "当前账号没有访问该资源的权限。权限以服务端校验为准。",
    tone: "error",
  },
  partial: {
    title: "部分数据不可用",
    description: "部分接口请求失败，已展示可用的数据，请重试失败部分。",
    tone: "warning",
  },
  stale: {
    title: "数据可能已过期",
    description: "最近一次刷新失败，当前展示的是上次成功获取的数据。",
    tone: "warning",
  },
  disabled: {
    title: "功能尚未开放",
    description: "该功能属于后续阶段，当前仅完成路由与布局骨架。",
    tone: "info",
  },
  pending: {
    title: "处理中",
    description: "操作已提交，请等待处理结果。",
    tone: "info",
  },
  success: {
    title: "操作成功",
    description: "操作已完成。",
    tone: "success",
  },
  failed: {
    title: "操作失败",
    description: "操作未能完成，请检查后重试。",
    tone: "error",
  },
};
