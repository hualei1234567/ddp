import { Alert, Button, Input, Modal, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useState } from "react";

import type {
  AiSuggestion,
  SuggestionAgreement,
  SuggestionAgreementItem,
} from "../../services/annotationAiParsers";
import PageState from "../ui/PageState";

interface AiSuggestionPanelProps {
  suggestions: AiSuggestion[];
  agreement: SuggestionAgreement | null;
  busySuggestionId: string | null;
  /** 采纳：直接把建议值作为人工值提交。 */
  onAdopt: (suggestion: AiSuggestion) => Promise<void>;
  /** 修订：提交人工填写的新值。 */
  onRevise: (suggestion: AiSuggestion, humanValue: unknown) => Promise<void>;
  onReload: () => void;
}

function formatValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatRate(rate: number | null): string {
  // 契约明确：null 表示没有已决定条目，不是 0%。不得渲染成 0%。
  return rate === null ? "无已决定样本" : `${(rate * 100).toFixed(1)}%`;
}

const agreementColumns: TableColumnsType<SuggestionAgreementItem> = [
  { title: "模型", dataIndex: "model_id", width: 180 },
  { title: "版本", dataIndex: "model_version", width: 120 },
  { title: "建议总数", dataIndex: "total", width: 100 },
  { title: "已决定", dataIndex: "decided", width: 100 },
  {
    title: "采纳 / 修订 / 待处理",
    key: "decisions",
    width: 200,
    render: (_value, item) =>
      `${item.accepted} / ${item.revised} / ${item.pending}`,
  },
  {
    title: "采纳率",
    dataIndex: "acceptance_rate",
    width: 160,
    render: (value: number | null) => formatRate(value),
  },
];

/**
 * 提交详情里的 AI 建议区：建议列表、采纳/修订与按模型版本的一致率。
 *
 * 上限与决策语义以服务端为准；这里不做「采纳=通过」之类的推断，
 * 只把服务端字段原样展示。修订值必须是合法 JSON。
 */
export default function AiSuggestionPanel({
  suggestions,
  agreement,
  busySuggestionId,
  onAdopt,
  onRevise,
  onReload,
}: AiSuggestionPanelProps) {
  const [pending, setPending] = useState<AiSuggestion | null>(null);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const openRevise = (suggestion: AiSuggestion) => {
    setPending(suggestion);
    setDraft(JSON.stringify(suggestion.suggested_value, null, 2));
    setDraftError(null);
    setModalError(null);
  };

  const handleRevise = async () => {
    if (!pending) {
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      setDraftError("修订值必须是合法 JSON。");
      return;
    }
    setDraftError(null);
    setModalError(null);
    setSubmitting(true);
    try {
      await onRevise(pending, parsed);
      setPending(null);
    } catch (caught) {
      setModalError(
        caught instanceof Error ? caught.message : "修订提交失败。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const columns: TableColumnsType<AiSuggestion> = [
    { title: "标签键", dataIndex: "label_key", width: 140 },
    {
      title: "建议值",
      dataIndex: "suggested_value",
      render: (value: unknown) => (
        <Typography.Text code ellipsis>
          {formatValue(value)}
        </Typography.Text>
      ),
    },
    {
      title: "模型",
      key: "model",
      width: 220,
      render: (_value, item) => (
        <Space direction="vertical" size={0}>
          <span>
            {item.model_id} · {item.model_version}
          </span>
          <Typography.Text type="secondary">
            提示词 {item.prompt_version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      width: 100,
      render: (value: number) => `${(value * 100).toFixed(1)}%`,
    },
    {
      title: "人工处理",
      key: "decision",
      width: 220,
      render: (_value, item) =>
        item.human_confirmed ? (
          <Space direction="vertical" size={0}>
            <Tag color="green">已处理</Tag>
            <Typography.Text type="secondary">
              人工值：{formatValue(item.human_value)}
            </Typography.Text>
          </Space>
        ) : (
          <Tag>待处理</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 180,
      render: (_value, item) =>
        item.human_confirmed ? (
          <Typography.Text type="secondary">已处理</Typography.Text>
        ) : (
          <Space>
            <Button
              size="small"
              loading={busySuggestionId === item.suggestion_id}
              onClick={() => void onAdopt(item)}
            >
              采纳
            </Button>
            <Button size="small" onClick={() => openRevise(item)}>
              修订
            </Button>
          </Space>
        ),
    },
  ];

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Space wrap>
        <Typography.Text strong>AI 预标注建议</Typography.Text>
        <Button size="small" onClick={onReload}>
          刷新建议
        </Button>
      </Space>
      {suggestions.length === 0 ? (
        <PageState
          compact
          variant="empty"
          title="暂无 AI 建议"
          description="模型侧尚未为这条提交登记建议；登记由标注 pipeline 写入。"
        />
      ) : (
        <Table<AiSuggestion>
          rowKey="suggestion_id"
          size="small"
          columns={columns}
          dataSource={suggestions}
          pagination={false}
          scroll={{ x: 1180 }}
        />
      )}
      <Typography.Text strong>一致率（按模型版本）</Typography.Text>
      {agreement === null ? (
        <Alert
          type="warning"
          showIcon
          message="一致率暂不可用"
          description="加载失败或服务端未返回；建议列表与人工处理不受影响。"
        />
      ) : agreement.items.length === 0 ? (
        <PageState compact variant="empty" title="暂无一致率统计" />
      ) : (
        <Table<SuggestionAgreementItem>
          rowKey={(item) => `${item.model_id}:${item.model_version}`}
          size="small"
          columns={agreementColumns}
          dataSource={agreement.items}
          pagination={false}
          scroll={{ x: 860 }}
        />
      )}
      <Typography.Text type="secondary">
        采纳率的分母是已决定条数；「无已决定样本」不表示采纳率为 0。
      </Typography.Text>
      <Modal
        title="修订 AI 建议值"
        open={pending !== null}
        okText="提交修订"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setPending(null)}
        onOk={() => void handleRevise()}
      >
        <Space direction="vertical" size={8} className="ddp-stack">
          {modalError ? <Alert type="error" showIcon message={modalError} /> : null}
          <Typography.Paragraph type="secondary">
            修订值以 JSON 提交；与建议值不同即视为修订，服务端记录人工决策。
          </Typography.Paragraph>
          <Input.TextArea
            rows={6}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          {draftError ? (
            <Typography.Text type="danger">{draftError}</Typography.Text>
          ) : null}
        </Space>
      </Modal>
    </Space>
  );
}
