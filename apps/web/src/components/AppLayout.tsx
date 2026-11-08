import {
  AppstoreOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  HomeOutlined,
  RobotOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import { Layout, Menu, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

const { Header, Sider, Content } = Layout;

const navItems: MenuProps["items"] = [
  { key: "/", icon: <HomeOutlined />, label: "工作台" },
  { key: "/projects", icon: <AppstoreOutlined />, label: "项目管理", disabled: true },
  { key: "/devices", icon: <RobotOutlined />, label: "机器纳管", disabled: true },
  { key: "/collection", icon: <ExperimentOutlined />, label: "数据采集", disabled: true },
  { key: "/ingestion", icon: <CloudUploadOutlined />, label: "数据接入", disabled: true },
  { key: "/annotation", icon: <TagsOutlined />, label: "标注中心", disabled: true },
  { key: "/datasets", icon: <DatabaseOutlined />, label: "数据集与训练", disabled: true },
];

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout className="ddp-shell">
      <Sider width={232} className="ddp-sider" theme="light">
        <div className="ddp-brand">
          <span className="ddp-brand-mark">DDP</span>
          <span className="ddp-brand-text">具身智能数据平台</span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={navItems}
          onClick={({ key }) => navigate(key)}
          className="ddp-nav"
        />
      </Sider>
      <Layout>
        <Header className="ddp-header">
          <Typography.Text strong>阶段 0 · 基建</Typography.Text>
          <Tag color="blue">开发中</Tag>
        </Header>
        <Content className="ddp-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
