import {
  Alert,
  Button,
  Descriptions,
  Divider,
  Drawer,
  Input,
  List,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useState } from "react";

import { ApiError } from "../services/api";
import {
  clearAccessToken,
  readAccessToken,
  storeAccessToken,
} from "../services/authToken";
import { formatIdentifier } from "../services/format";
import {
  identityApi,
  type CurrentUser,
  type IdentityApi,
} from "../services/identity";
import { projectManagementApi } from "../services/projectManagement";
import type { Project } from "../types/project";

export interface ProjectListPort {
  listProjects: (options?: {
    status?: "ACTIVE" | "ARCHIVED";
    limit?: number;
  }) => Promise<{ items: Project[] }>;
}

export interface AuthSessionDrawerProps {
  open: boolean;
  currentProjectId: string | null;
  currentUser: CurrentUser | null;
  onClose: () => void;
  onSessionChange: (user: CurrentUser | null, hasToken: boolean) => void;
  onSelectProject: (projectId: string) => void;
  identity?: IdentityApi;
  projects?: ProjectListPort;
}

function toErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return "令牌校验失败，请检查网络连接或后端服务状态。";
}

function CurrentIdentitySection({
  user,
  onLogout,
}: {
  user: CurrentUser;
  onLogout: () => void;
}) {
  const isAdmin = user.platform_role === "admin";
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="success"
        showIcon
        message={`已通过服务端认证：${user.display_name}`}
      />
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="显示名称">
          {user.display_name}
        </Descriptions.Item>
        <Descriptions.Item label="平台角色">
          <Tag color={isAdmin ? "gold" : "blue"}>
            {isAdmin ? "平台管理员 (admin)" : "平台成员 (member)"}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="用户 ID">
          <Typography.Text copyable={{ text: user.user_id }} code>
            {formatIdentifier(user.user_id)}
          </Typography.Text>
        </Descriptions.Item>
      </Descriptions>
      <Button danger block onClick={onLogout}>
        退出登录（清除会话令牌）
      </Button>
    </Space>
  );
}

function AccessibleProjectsSection({
  items,
  loading,
  currentProjectId,
  onSelectProject,
}: {
  items: Project[];
  loading: boolean;
  currentProjectId: string | null;
  onSelectProject: (projectId: string) => void;
}) {
  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text strong>可访问项目上下文切换</Typography.Text>
      <List
        size="small"
        bordered
        loading={loading}
        locale={{ emptyText: "暂无可见项目，管理员可在「项目管理」新建项目" }}
        dataSource={items}
        renderItem={(item) => {
          const active = item.project_id === currentProjectId;
          return (
            <List.Item
              actions={[
                <Button
                  key="switch"
                  size="small"
                  type={active ? "default" : "link"}
                  disabled={active}
                  onClick={() => onSelectProject(item.project_id)}
                >
                  {active ? "当前项目" : "切换项目"}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size={6}>
                    <span>{item.name}</span>
                    <Tag>{item.code}</Tag>
                  </Space>
                }
                description={`ID: ${formatIdentifier(item.project_id)}`}
              />
            </List.Item>
          );
        }}
      />
    </Space>
  );
}

export default function AuthSessionDrawer({
  open,
  currentProjectId,
  currentUser,
  onClose,
  onSessionChange,
  onSelectProject,
  identity = identityApi,
  projects = projectManagementApi,
}: AuthSessionDrawerProps) {
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [projectItems, setProjectItems] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    setDraft(readAccessToken() ?? "");
    setErrorText(null);
  }, [open]);

  useEffect(() => {
    if (!open || !currentUser) {
      setProjectItems([]);
      return;
    }
    let cancelled = false;
    setProjectsLoading(true);
    projects
      .listProjects({ status: "ACTIVE", limit: 20 })
      .then((page) => {
        if (!cancelled) {
          setProjectItems(page.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setProjectItems([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setProjectsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, currentUser, projects]);

  const handleVerifyAndLogin = async () => {
    const previousToken = readAccessToken();
    const stored = storeAccessToken(draft);
    if (!stored) {
      setErrorText("请输入有效的 Bearer JWT 令牌。");
      return;
    }
    setSubmitting(true);
    setErrorText(null);
    try {
      const verifiedUser = await identity.getCurrentUser();
      onSessionChange(verifiedUser, true);
      onClose();
    } catch (err) {
      if (previousToken) {
        storeAccessToken(previousToken);
      } else {
        clearAccessToken();
      }
      setErrorText(toErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleLogout = () => {
    clearAccessToken();
    setDraft("");
    setErrorText(null);
    setProjectItems([]);
    onSessionChange(null, false);
  };

  return (
    <Drawer
      title="轻量登录与项目会话"
      placement="right"
      width={400}
      open={open}
      onClose={onClose}
    >
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {currentUser ? (
          <>
            <CurrentIdentitySection
              user={currentUser}
              onLogout={handleLogout}
            />
            <AccessibleProjectsSection
              items={projectItems}
              loading={projectsLoading}
              currentProjectId={currentProjectId}
              onSelectProject={(projectId) => {
                onSelectProject(projectId);
                onClose();
              }}
            />
            <Divider style={{ margin: "4px 0" }} />
          </>
        ) : null}

        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text strong>
            {currentUser ? "切换其他身份令牌" : "使用 Bearer JWT 登录"}
          </Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 4 }}>
            粘贴服务端签发的 HS256 JWT（支持直接粘贴带{" "}
            <Typography.Text code>Bearer </Typography.Text>{" "}
            前缀的文本）。点击登录后将实时请求{" "}
            <Typography.Text code>GET /api/v1/me</Typography.Text>{" "}
            核验身份，令牌仅保存在当前浏览器会话（sessionStorage）。
          </Typography.Paragraph>
          {errorText ? <Alert type="error" showIcon message={errorText} /> : null}
          <Input.TextArea
            rows={4}
            aria-label="访问令牌输入框"
            value={draft}
            placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button
            type="primary"
            block
            loading={submitting}
            onClick={handleVerifyAndLogin}
          >
            验证并登录
          </Button>
        </Space>

        <Divider style={{ margin: "4px 0" }} />
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Typography.Text type="secondary" strong>
            开源学习环境一键签发命令
          </Typography.Text>
          <Typography.Paragraph
            type="secondary"
            copyable={{
              text: 'backend/.venv/bin/python scripts/ops/issue_jwt.py --display-name "开源管理员" --role admin',
            }}
            code
            style={{ marginBottom: 0, wordBreak: "break-all" }}
          >
            backend/.venv/bin/python scripts/ops/issue_jwt.py --display-name
            &quot;开源管理员&quot; --role admin
          </Typography.Paragraph>
        </Space>
      </Space>
    </Drawer>
  );
}
