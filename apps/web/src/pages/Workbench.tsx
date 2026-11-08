import { ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Descriptions,
  List,
  Row,
  Space,
  Tag,
  Typography,
} from "antd";
import type { ReactNode } from "react";

import PageContainer from "../components/ui/PageContainer";
import PageState from "../components/ui/PageState";
import { useRequest } from "../hooks/useRequest";
import { api, ApiError } from "../services/api";
import { formatPhase, formatTimestamp } from "../services/format";
import {
  createWorkbenchSnapshot,
  hasPartialFailure,
  isFullyForbidden,
  type EndpointSnapshot,
  type WorkbenchSnapshot,
} from "../services/workbench";
import type { HealthResponse, PlatformMeta } from "../types/platform";

const phaseZeroItems = [
  "仓库、分支保护与 PR 流程",
  "后端骨架与统一错误/日志",
  "前端骨架与设计令牌",
  "本地 PostgreSQL / Redis / MinIO",
  "OpenAPI 契约与前端类型",
  "CI 基线（lint/type/test/build/security）",
  "Agent 1+6 团队与平台纳管",
];

async function loadWorkbench(signal: AbortSignal): Promise<WorkbenchSnapshot> {
  const [health, meta] = await Promise.allSettled([
    api.health({ signal }),
    api.meta({ signal }),
  ]);

  if (signal.aborted) {
    throw new ApiError({ kind: "aborted", message: "请求已取消。" });
  }

  return createWorkbenchSnapshot(health, meta);
}

interface EndpointContentProps<T> {
  snapshot: EndpointSnapshot<T>;
  emptyDescription: string;
  onRetry: () => void;
  children: (data: T) => ReactNode;
}

function EndpointContent<T>({
  snapshot,
  emptyDescription,
  onRetry,
  children,
}: EndpointContentProps<T>) {
  if (snapshot.error) {
    if (snapshot.error.isForbidden) {
      return <PageState variant="forbidden" compact />;
    }
    return (
      <PageState
        variant="error"
        compact
        description={snapshot.error.message}
        onRetry={onRetry}
      />
    );
  }

  if (!snapshot.data) {
    return <PageState variant="empty" compact description={emptyDescription} />;
  }

  return <>{children(snapshot.data)}</>;
}

function HealthDetails({ health }: { health: HealthResponse }) {
  return (
    <Descriptions column={1} size="small">
      <Descriptions.Item label="状态">
        <Tag color={health.status === "ok" ? "green" : "orange"}>
          {health.status}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="服务">
        {health.service} {health.version}
      </Descriptions.Item>
      <Descriptions.Item label="环境">{health.env}</Descriptions.Item>
    </Descriptions>
  );
}

function MetaDetails({ meta }: { meta: PlatformMeta }) {
  if (meta.modules.length === 0) {
    return (
      <PageState
        variant="empty"
        compact
        description="平台未返回任何已启用模块。"
      />
    );
  }

  return (
    <Descriptions column={1} size="small">
      <Descriptions.Item label="阶段">
        {formatPhase(meta.phase)}
      </Descriptions.Item>
      <Descriptions.Item label="模块">
        {meta.modules.join("、")}
      </Descriptions.Item>
      <Descriptions.Item label="版本">{meta.version}</Descriptions.Item>
    </Descriptions>
  );
}

interface WorkbenchContentProps {
  snapshot: WorkbenchSnapshot;
  stale: boolean;
  onRetry: () => void;
}

function WorkbenchContent({
  snapshot,
  stale,
  onRetry,
}: WorkbenchContentProps) {
  if (isFullyForbidden(snapshot)) {
    return (
      <PageState
        variant="forbidden"
        description="服务端拒绝了工作台所需的接口访问。请确认账号权限后重试。"
        onRetry={onRetry}
      />
    );
  }

  const partial = hasPartialFailure(snapshot);

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {stale ? (
        <PageState variant="stale" compact onRetry={onRetry} />
      ) : null}
      {partial ? <PageState variant="partial" compact /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="服务状态">
            <EndpointContent
              snapshot={snapshot.health}
              emptyDescription="健康检查未返回数据。"
              onRetry={onRetry}
            >
              {(health) => <HealthDetails health={health} />}
            </EndpointContent>
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="平台元信息">
            <EndpointContent
              snapshot={snapshot.meta}
              emptyDescription="平台元信息未返回数据。"
              onRetry={onRetry}
            >
              {(meta) => <MetaDetails meta={meta} />}
            </EndpointContent>
          </Card>
        </Col>
      </Row>

      <Card title="阶段 0 基建范围">
        <List
          size="small"
          dataSource={phaseZeroItems}
          renderItem={(item) => (
            <List.Item>
              <Space>
                <Tag color="blue">规划</Tag>
                <span>{item}</span>
              </Space>
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}

export default function Workbench() {
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadWorkbench);
  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button
        icon={<ReloadOutlined />}
        onClick={reload}
        loading={refreshing}
      >
        刷新
      </Button>
    </Space>
  );

  let content: ReactNode;
  if (!data && loading) {
    content = <PageState variant="loading" title="正在加载平台状态" />;
  } else if (!data && error) {
    content = error.isForbidden ? (
      <PageState
        variant="forbidden"
        description="服务端拒绝了工作台所需的接口访问。"
        onRetry={reload}
      />
    ) : (
      <PageState variant="error" description={error.message} onRetry={reload} />
    );
  } else if (data) {
    content = (
      <WorkbenchContent snapshot={data} stale={stale} onRetry={reload} />
    );
  } else {
    content = <PageState variant="empty" title="暂无平台数据" />;
  }

  return (
    <PageContainer
      title="DDP 工作台"
      description="具身智能数据闭环平台 · 阶段 0 基建。当前只提供服务骨架，业务模块按 机器纳管 → 采集 → 上传 → 标注 → 训练 逐阶段开放。"
      extra={extra}
    >
      {content}
    </PageContainer>
  );
}
