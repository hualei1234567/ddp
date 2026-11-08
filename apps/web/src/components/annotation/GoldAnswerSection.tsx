import { Button, Space, Table, Tag, Typography } from "antd";

import type { ApiError } from "../../services/api";
import type { GoldSet, GoldSetItem } from "../../types/annotation";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";

interface GoldAnswerSectionProps {
  answers: GoldSet | null;
  error: ApiError | null;
  loading: boolean;
  onLoad: () => void;
}

/**
 * 黄金集答案的显式访问区。
 *
 * - 默认不请求答案；
 * - 403 呈现专用 Forbidden 文案与错误码，不展示任何期望标签；
 * - 仅成功响应渲染 expected_labels，且不做本地缓存。
 */
export default function GoldAnswerSection({
  answers,
  error,
  loading,
  onLoad,
}: GoldAnswerSectionProps) {
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Button type="primary" loading={loading} onClick={onLoad}>
        显式查看期望答案（服务端审计）
      </Button>
      <Typography.Text type="secondary">
        列表与详情默认脱敏；答案访问仅限授权角色，且不在本地缓存或日志中保留。
      </Typography.Text>
      {error ? (
        error.status === 403 || error.code === "gold_set_access_denied" ? (
          <PageState
            variant="forbidden"
            title="黄金集期望答案不可见"
            description={
              <span>
                当前账号无权查看该黄金集答案；服务端已拒绝并记录审计。错误码：
                <code>{error.code ?? "gold_set_access_denied"}</code>
                ；request_id：<code>{error.requestId ?? "—"}</code>。
              </span>
            }
          />
        ) : (
          <ApiErrorState error={error} onRetry={onLoad} />
        )
      ) : null}
      {answers ? (
        <Space direction="vertical" size={8} className="ddp-stack">
          <Tag color="red">已授权答案视图</Tag>
          <Table<GoldSetItem>
            rowKey="gold_item_id"
            size="small"
            dataSource={answers.items}
            pagination={false}
            columns={[
              {
                title: "黄金项",
                dataIndex: "gold_item_id",
                width: 220,
                render: (value: string) => (
                  <IdentifierValue value={value} label="黄金项 ID" />
                ),
              },
              {
                title: "Episode",
                dataIndex: "episode_id",
                width: 220,
                render: (value: string) => (
                  <IdentifierValue value={value} label="Episode ID" />
                ),
              },
              {
                title: "期望标签",
                dataIndex: "expected_labels",
                render: (value: unknown) => (
                  <Typography.Text code>{JSON.stringify(value)}</Typography.Text>
                ),
              },
            ]}
          />
        </Space>
      ) : null}
    </Space>
  );
}
