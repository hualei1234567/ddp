import { useEffect, useState } from "react";
import {
  Alert,
  Card,
  Col,
  Descriptions,
  List,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";

import { ApiError, api } from "../services/api";
import { formatPhase } from "../services/format";
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

export default function Workbench() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [meta, setMeta] = useState<PlatformMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [healthResponse, metaResponse] = await Promise.all([
          api.health(),
          api.meta(),
        ]);
        if (!cancelled) {
          setHealth(healthResponse);
          setMeta(metaResponse);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof ApiError
              ? `接口返回 ${caught.status}`
              : "无法连接后端服务",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      <Typography.Title level={3} className="ddp-title">
        DDP 工作台
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        具身智能数据闭环平台 · 阶段 0 基建。当前只提供服务骨架，业务模块按
        机器纳管 → 采集 → 上传 → 标注 → 训练 逐阶段开放。
      </Typography.Paragraph>

      {error ? <Alert type="error" showIcon message={error} /> : null}

      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="服务状态" loading={loading}>
            {health ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="状态">
                  <Tag color="green">{health.status}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="服务">
                  {health.service} {health.version}
                </Descriptions.Item>
                <Descriptions.Item label="环境">{health.env}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Spin />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="平台元信息" loading={loading}>
            {meta ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="阶段">
                  {formatPhase(meta.phase)}
                </Descriptions.Item>
                <Descriptions.Item label="模块">
                  {meta.modules.join("、")}
                </Descriptions.Item>
                <Descriptions.Item label="版本">{meta.version}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Spin />
            )}
          </Card>
        </Col>
      </Row>

      <Card title="阶段 0 检查清单">
        <List
          size="small"
          dataSource={phaseZeroItems}
          renderItem={(item) => (
            <List.Item>
              <Space>
                <Tag color="blue">进行中</Tag>
                <span>{item}</span>
              </Space>
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}
