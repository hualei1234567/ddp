import {
  ClusterOutlined,
  DoubleLeftOutlined,
  DoubleRightOutlined,
  DatabaseOutlined,
  PartitionOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { Button, Input, Menu, Tooltip } from "antd";
import type { MenuProps } from "antd";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { navSections, type NavItem, type NavSection } from "../app/navigation";
import {
  COLLAPSIBLE_SECTION_KEYS,
  OPEN_KEYS_STORAGE_KEY,
  sectionSubmenuKey,
} from "./sidebarNavigationShared";

const SECTION_ICONS: Record<string, ReactNode> = {
  plan: <PartitionOutlined />,
  process: <PartitionOutlined />,
  annotate: <SafetyCertificateOutlined />,
  consume: <DatabaseOutlined />,
  resources: <ClusterOutlined />,
  governance: <SafetyCertificateOutlined />,
};

function readStoredOpenKeys(): string[] {
  if (typeof window === "undefined") {
    return COLLAPSIBLE_SECTION_KEYS;
  }
  try {
    const raw = window.localStorage.getItem(OPEN_KEYS_STORAGE_KEY);
    if (!raw) {
      return COLLAPSIBLE_SECTION_KEYS;
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      Array.isArray(parsed) &&
      parsed.every((item) => typeof item === "string")
    ) {
      const valid = new Set(COLLAPSIBLE_SECTION_KEYS);
      return parsed.filter((key) => valid.has(key));
    }
  } catch {
    // 隐私模式或旧缓存格式异常时回落为默认全部展开。
  }
  return COLLAPSIBLE_SECTION_KEYS;
}

function persistOpenKeys(keys: string[]): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(OPEN_KEYS_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // 忽略存储不可用场景，不影响交互。
  }
}

function buildNavLeaf(item: NavItem) {
  return {
    key: item.path,
    icon: item.icon,
    disabled: !item.available,
    label: (
      <span className="ddp-nav-label">
        <span className="ddp-nav-text">{item.label}</span>
        {!item.available ? (
          <span className="ddp-nav-badge">待开放</span>
        ) : null}
      </span>
    ),
  };
}

function buildMenuItems(
  sections: NavSection[],
  query: string,
): { items: MenuProps["items"]; matchedSectionKeys: string[] } {
  const keyword = query.trim().toLowerCase();
  const items: NonNullable<MenuProps["items"]> = [];
  const matchedSectionKeys: string[] = [];

  for (const section of sections) {
    const visibleItems = keyword
      ? section.items.filter(
          (item) =>
            item.label.toLowerCase().includes(keyword) ||
            item.description.toLowerCase().includes(keyword) ||
            item.path.toLowerCase().includes(keyword),
        )
      : section.items;

    if (visibleItems.length === 0) {
      continue;
    }

    const firstItem = visibleItems[0];
    if (section.items.length === 1 && firstItem) {
      items.push(buildNavLeaf(firstItem));
      continue;
    }

    const key = sectionSubmenuKey(section.key);
    matchedSectionKeys.push(key);
    items.push({
      key,
      icon: SECTION_ICONS[section.key],
      label: (
        <span className="ddp-nav-section-title">
          <span>{section.label}</span>
          <span className="ddp-nav-count">{visibleItems.length}</span>
        </span>
      ),
      children: visibleItems.map(buildNavLeaf),
    });
  }

  return { items, matchedSectionKeys };
}

export interface SidebarNavigationProps {
  selectedKeys: string[];
  activeSectionKey: string | null;
  collapsed?: boolean;
  onNavigate: (path: string) => void;
}

export default function SidebarNavigation({
  selectedKeys,
  activeSectionKey,
  collapsed = false,
  onNavigate,
}: SidebarNavigationProps) {
  const [query, setQuery] = useState("");
  const [openKeys, setOpenKeys] = useState<string[]>(() =>
    readStoredOpenKeys(),
  );

  useEffect(() => {
    if (!activeSectionKey) {
      return;
    }
    const targetKey = sectionSubmenuKey(activeSectionKey);
    if (!COLLAPSIBLE_SECTION_KEYS.includes(targetKey)) {
      return;
    }
    setOpenKeys((prev) => {
      if (prev.includes(targetKey)) {
        return prev;
      }
      const next = [...prev, targetKey];
      persistOpenKeys(next);
      return next;
    });
  }, [activeSectionKey]);

  const { items, matchedSectionKeys } = useMemo(
    () => buildMenuItems(navSections, query),
    [query],
  );

  const isSearching = query.trim().length > 0;
  const effectiveOpenKeys = collapsed
    ? undefined
    : isSearching
      ? matchedSectionKeys
      : openKeys;

  const allExpanded = openKeys.length >= COLLAPSIBLE_SECTION_KEYS.length;

  const handleOpenChange: MenuProps["onOpenChange"] = (nextKeys) => {
    if (isSearching) {
      return;
    }
    const normalized = nextKeys.map(String);
    setOpenKeys(normalized);
    persistOpenKeys(normalized);
  };

  const handleToggleAll = () => {
    const next = allExpanded ? [] : [...COLLAPSIBLE_SECTION_KEYS];
    setOpenKeys(next);
    persistOpenKeys(next);
  };

  return (
    <div className="ddp-nav-container">
      {!collapsed ? (
        <div className="ddp-nav-toolbar">
          <Input
            size="small"
            allowClear
            value={query}
            placeholder="检索功能模块..."
            aria-label="检索左侧导航菜单"
            prefix={<SearchOutlined className="ddp-nav-search-icon" />}
            onChange={(event) => setQuery(event.target.value)}
            className="ddp-nav-search"
          />
          <Tooltip title={allExpanded ? "全部收起分组" : "全部展开分组"}>
            <Button
              size="small"
              type="text"
              aria-label={allExpanded ? "全部收起分组" : "全部展开分组"}
              icon={
                allExpanded ? (
                  <DoubleLeftOutlined rotate={90} />
                ) : (
                  <DoubleRightOutlined rotate={90} />
                )
              }
              onClick={handleToggleAll}
              className="ddp-nav-toggle-all"
            />
          </Tooltip>
        </div>
      ) : null}

      <Menu
        mode="inline"
        inlineIndent={18}
        selectedKeys={selectedKeys}
        openKeys={effectiveOpenKeys}
        onOpenChange={handleOpenChange}
        items={items}
        onClick={({ key }) => onNavigate(key)}
        className="ddp-nav"
      />
    </div>
  );
}
