import {
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import {
  Breadcrumb,
  Button,
  Drawer,
  Grid,
  Layout,
  Menu,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import {
  findNavItem,
  navSections,
  PLATFORM_STAGE_LABEL,
} from "../app/navigation";
import { designTokens } from "../app/theme";
import { useProjectId } from "../hooks/useProjectId";
import { formatIdentifier } from "../services/format";

const { Header, Sider, Content } = Layout;

function buildMenuItems(): MenuProps["items"] {
  return navSections.map((section) => ({
    type: "group" as const,
    key: `group:${section.key}`,
    label: section.label,
    children: section.items.map((item) => ({
      key: item.path,
      icon: item.icon,
      disabled: !item.available,
      label: (
        <span className="ddp-nav-label">
          <span>{item.label}</span>
          {!item.available ? (
            <span className="ddp-nav-badge">待开放</span>
          ) : null}
        </span>
      ),
    })),
  }));
}

const MENU_ITEMS = buildMenuItems();

interface NavigationMenuProps {
  selectedKeys: string[];
  onNavigate: (path: string) => void;
}

function NavigationMenu({ selectedKeys, onNavigate }: NavigationMenuProps) {
  return (
    <Menu
      mode="inline"
      selectedKeys={selectedKeys}
      items={MENU_ITEMS}
      onClick={({ key }) => onNavigate(key)}
      className="ddp-nav"
    />
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "ddp-brand ddp-brand-compact" : "ddp-brand"}>
      <span className="ddp-brand-mark">DDP</span>
      <span className="ddp-brand-text">具身智能数据平台</span>
    </div>
  );
}

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const isDesktop = screens.lg ?? true;
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const projectId = useProjectId();

  const match = findNavItem(location.pathname);
  const pageTitle =
    match?.item.label ??
    (location.pathname === "/forbidden" ? "无权访问" : "页面不存在");
  const sectionTitle = match?.section.label ?? "DDP";
  const selectedKeys = match ? [match.item.path] : [];

  useEffect(() => {
    document.title = `${pageTitle} · DDP`;
  }, [pageTitle]);

  useEffect(() => {
    setMenuOpen(false);
  }, [isDesktop, location.pathname]);

  const handleNavigate = (path: string) => {
    setMenuOpen(false);
    navigate(path);
  };

  const navigation = (
    <NavigationMenu selectedKeys={selectedKeys} onNavigate={handleNavigate} />
  );

  return (
    <>
      <Layout className="ddp-shell">
        {isDesktop ? (
          <Sider
            width={designTokens.layout.siderWidth}
            collapsedWidth={designTokens.layout.siderCollapsedWidth}
            collapsed={collapsed}
            trigger={null}
            theme="light"
            className="ddp-sider"
          >
            <div className="ddp-brand-wrap">
              <Brand compact={collapsed} />
            </div>
            {navigation}
            {!collapsed ? (
              <div className="ddp-sider-footer">
                <Tag color="blue">{PLATFORM_STAGE_LABEL}</Tag>
                <Typography.Text type="secondary">
                  路由、布局与 API 骨架
                </Typography.Text>
              </div>
            ) : null}
          </Sider>
        ) : null}

        <Layout className="ddp-main">
          <Header className="ddp-header">
            <div className="ddp-header-main">
              {isDesktop ? (
                <Button
                  type="text"
                  aria-label={collapsed ? "展开导航" : "收起导航"}
                  icon={
                    collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />
                  }
                  onClick={() => setCollapsed((previous) => !previous)}
                />
              ) : (
                <Button
                  type="text"
                  aria-label="打开导航"
                  icon={<MenuOutlined />}
                  onClick={() => setMenuOpen(true)}
                />
              )}
              <div className="ddp-header-crumbs">
                <Breadcrumb
                  items={[{ title: sectionTitle }, { title: pageTitle }]}
                />
              </div>
            </div>
            <div className="ddp-header-actions">
              <Tooltip
                title={
                  projectId
                    ? `当前项目：${projectId}`
                    : "请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID"
                }
              >
                <Tag
                  className="ddp-context-tag"
                  color={projectId ? "blue" : undefined}
                >
                  {projectId
                    ? `项目 ${formatIdentifier(projectId)}`
                    : "未选择项目"}
                </Tag>
              </Tooltip>
              <Tag color="blue">{PLATFORM_STAGE_LABEL}</Tag>
            </div>
          </Header>
          <Content className="ddp-content">
            <Outlet />
          </Content>
        </Layout>
      </Layout>

      <Drawer
        open={!isDesktop && menuOpen}
        onClose={() => setMenuOpen(false)}
        placement="left"
        width={280}
        title="DDP · 具身智能数据平台"
        className="ddp-drawer"
      >
        {navigation}
      </Drawer>
    </>
  );
}
