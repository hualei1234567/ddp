import {
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Breadcrumb,
  Button,
  Drawer,
  Grid,
  Layout,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { findNavItem, PLATFORM_STAGE_LABEL } from "../app/navigation";
import { designTokens } from "../app/theme";
import { useProjectId } from "../hooks/useProjectId";
import { readAccessToken } from "../services/authToken";
import { formatIdentifier } from "../services/format";
import { identityApi, type CurrentUser } from "../services/identity";
import { storeProjectId } from "../services/project";
import AuthSessionDrawer from "./AuthSessionDrawer";
import SidebarNavigation from "./SidebarNavigation";

const { Header, Sider, Content } = Layout;

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
  const [tokenOpen, setTokenOpen] = useState(false);
  const [hasToken, setHasToken] = useState(() => readAccessToken() !== null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const projectId = useProjectId();

  useEffect(() => {
    if (!hasToken) {
      setCurrentUser(null);
      return;
    }
    const controller = new AbortController();
    identityApi
      .getCurrentUser({ signal: controller.signal })
      .then((user) => setCurrentUser(user))
      .catch(() => setCurrentUser(null));
    return () => controller.abort();
  }, [hasToken]);

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

  const navigation = (isCollapsed: boolean) => (
    <SidebarNavigation
      selectedKeys={selectedKeys}
      activeSectionKey={match?.section.key ?? null}
      collapsed={isCollapsed}
      onNavigate={handleNavigate}
    />
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
            {navigation(collapsed)}
            {!collapsed ? (
              <div className="ddp-sider-footer">
                <Tag color="blue">{PLATFORM_STAGE_LABEL}</Tag>
                <Typography.Text type="secondary">
                  采集 · 标注 · 数据集 · 模型闭环
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
              <Tooltip
                title={
                  currentUser
                    ? `已登录：${currentUser.display_name} (${currentUser.platform_role})`
                    : hasToken
                      ? "已设置访问令牌，点击查看身份或切换项目"
                      : "未登录，点击输入 Bearer JWT 登录"
                }
              >
                <Button
                  size="small"
                  type={hasToken ? "default" : "primary"}
                  icon={<UserOutlined />}
                  onClick={() => setTokenOpen(true)}
                >
                  {currentUser
                    ? currentUser.display_name
                    : hasToken
                      ? "令牌已设置"
                      : "轻量登录"}
                </Button>
              </Tooltip>
              <Tag color="blue">{PLATFORM_STAGE_LABEL}</Tag>
            </div>
          </Header>
          <Content className="ddp-content">
            <Outlet />
          </Content>
        </Layout>
      </Layout>

      {contextHolder}
      <AuthSessionDrawer
        open={tokenOpen}
        currentProjectId={projectId}
        currentUser={currentUser}
        onClose={() => setTokenOpen(false)}
        onSessionChange={(user, nextHasToken) => {
          setCurrentUser(user);
          setHasToken(nextHasToken);
          if (user) {
            messageApi.success(`欢迎，${user.display_name}`);
          } else {
            messageApi.info("已退出登录并清除会话令牌");
          }
        }}
        onSelectProject={(nextProjectId) => {
          storeProjectId(nextProjectId);
          const params = new URLSearchParams(location.search);
          params.set("project_id", nextProjectId);
          navigate({
            pathname: location.pathname,
            search: `?${params.toString()}`,
          });
          messageApi.success(`已切换至项目 ${formatIdentifier(nextProjectId)}`);
        }}
      />

      <Drawer
        open={!isDesktop && menuOpen}
        onClose={() => setMenuOpen(false)}
        placement="left"
        width={280}
        title="DDP · 具身智能数据平台"
        className="ddp-drawer"
      >
        {navigation(false)}
      </Drawer>
    </>
  );
}
