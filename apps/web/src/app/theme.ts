import type { ThemeConfig } from "antd";

/**
 * DDP 设计令牌（TypeScript 侧唯一来源）。
 *
 * 与 `src/styles/tokens.css` 中的 CSS 变量一一对应。修改色值、间距或断点时
 * 必须同步更新两处，避免 AntD 主题与自定义样式漂移。
 */
export const designTokens = {
  color: {
    primary: "#2f6fed",
    primaryStrong: "#1f57c3",
    success: "#18a058",
    warning: "#d97706",
    error: "#d92d20",
    info: "#2f6fed",
    bg: "#f5f7fb",
    surface: "#ffffff",
    border: "#e5e9f2",
    text: "#1b2430",
    textMuted: "#6b7785",
    disabled: "#a6b0bd",
  },
  space: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
    xxl: 32,
  },
  radius: {
    sm: 6,
    md: 8,
    lg: 12,
    pill: 999,
  },
  fontSize: {
    xs: 12,
    sm: 13,
    md: 14,
    lg: 16,
    xl: 20,
    xxl: 24,
  },
  shadow: {
    card: "0 1px 2px rgba(16, 24, 40, 0.04), 0 8px 24px rgba(16, 24, 40, 0.06)",
    raised: "0 12px 32px rgba(16, 24, 40, 0.12)",
  },
  breakpoint: {
    sm: 576,
    md: 768,
    lg: 992,
    xl: 1200,
    xxl: 1600,
  },
  layout: {
    headerHeight: 60,
    siderWidth: 232,
    siderCollapsedWidth: 72,
  },
} as const;

export const fontFamily =
  '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Microsoft YaHei", sans-serif';

/** AntD 主题与设计令牌共用同一组色值和圆角，页面不再各自硬编码。 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: designTokens.color.primary,
    colorSuccess: designTokens.color.success,
    colorWarning: designTokens.color.warning,
    colorError: designTokens.color.error,
    colorInfo: designTokens.color.info,
    colorBgLayout: designTokens.color.bg,
    colorBgContainer: designTokens.color.surface,
    colorBorder: designTokens.color.border,
    colorText: designTokens.color.text,
    colorTextSecondary: designTokens.color.textMuted,
    colorTextDisabled: designTokens.color.disabled,
    borderRadius: designTokens.radius.md,
    borderRadiusLG: designTokens.radius.lg,
    fontFamily,
    fontSize: designTokens.fontSize.md,
  },
  components: {
    Layout: {
      headerBg: designTokens.color.surface,
      bodyBg: designTokens.color.bg,
      siderBg: designTokens.color.surface,
    },
    Menu: {
      itemHeight: 42,
      itemBorderRadius: designTokens.radius.md,
      itemMarginInline: designTokens.space.xs,
      groupTitleColor: designTokens.color.textMuted,
    },
  },
};
