/**
 * 左侧导航可折叠分组的共享常量（SidebarNavigation 与测试共用）。
 *
 * 独立成模块的原因：react-refresh 只允许组件文件导出组件；
 * 把常量放在组件文件里再导出，会让 HMR 失效（lint 规则
 * `react-refresh/only-export-components` 强制，--max-warnings=0）。
 */
import { navSections } from "../app/navigation";

export const OPEN_KEYS_STORAGE_KEY = "ddp.nav.open_keys";

export function sectionSubmenuKey(sectionKey: string): string {
  return `section:${sectionKey}`;
}

export const COLLAPSIBLE_SECTION_KEYS = navSections
  .filter((section) => section.items.length > 1)
  .map((section) => sectionSubmenuKey(section.key));
