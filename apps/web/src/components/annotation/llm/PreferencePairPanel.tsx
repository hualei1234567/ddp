import { Card, Col, Divider, Input, Row, Space, Tag, Typography } from "antd";
import { useState } from "react";

import {
  PREFERENCE_WINNER_VALUES,
  type PreferencePairAnnotationValue,
  type PreferencePairItemPayload,
  type PreferenceWinner,
} from "../../../types/llmAnnotation";

const { TextArea } = Input;

const WINNER_OPTIONS: PreferenceWinner[] = [...PREFERENCE_WINNER_VALUES];

/**
 * PREFERENCE_PAIR 标注面板：A/B 双栏对比 + winner 选择 + rationale/confidence。
 * 结论写入 JSON 约定值 `{winner, confidence, rationale}`（MEM-DEC-002）。
 * 受控/非受控两用：value 为 null 时内部自管草稿，onChange 回传最新值；
 * winner 未选时回传 null，由调用方决定是否允许提交。
 */
export default function PreferencePairPanel({
  payload,
  value,
  onChange,
}: {
  payload: PreferencePairItemPayload;
  value: PreferencePairAnnotationValue | null;
  onChange?: (value: PreferencePairAnnotationValue | null) => void;
}) {
  const [draftWinner, setDraftWinner] = useState<PreferenceWinner | null>(null);
  const [draftConfidence, setDraftConfidence] = useState<number | null>(null);
  const [draftRationale, setDraftRationale] = useState<string | null>(null);

  // value 存在（父层受控）时以它为准；否则用本地草稿字段。
  const winner = value ? value.winner : draftWinner;
  const confidence = value ? value.confidence : draftConfidence;
  const rationale = value ? value.rationale : draftRationale;

  const emit = (patch: {
    winner?: PreferenceWinner | null;
    confidence?: number | null;
    rationale?: string | null;
  }) => {
    const nextWinner = patch.winner !== undefined ? patch.winner : winner;
    const nextConfidence =
      patch.confidence !== undefined ? patch.confidence : confidence;
    const nextRationale =
      patch.rationale !== undefined ? patch.rationale : rationale;
    setDraftWinner(nextWinner);
    setDraftConfidence(nextConfidence);
    setDraftRationale(nextRationale);
    // winner 尚未选择时不算有效结论，回传 null 让调用方知道还未完成。
    onChange?.(
      nextWinner === null
        ? null
        : {
            winner: nextWinner,
            confidence: nextConfidence,
            rationale: nextRationale,
          },
    );
  };

  return (
    <Card
      size="small"
      title="偏好对比（PREFERENCE_PAIR）"
      data-testid="llm-preference-card"
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        对比维度：
        {payload.criteria.length > 0 ? payload.criteria.join("、") : "未声明"}
      </Typography.Paragraph>
      <Row gutter={12}>
        <Col span={12}>
          <Card
            type="inner"
            size="small"
            title="回答 A（chosen）"
            data-testid="llm-pref-chosen"
          >
            <Typography.Paragraph
              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
            >
              {payload.chosen}
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col span={12}>
          <Card
            type="inner"
            size="small"
            title="回答 B（rejected）"
            data-testid="llm-pref-rejected"
          >
            <Typography.Paragraph
              style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
            >
              {payload.rejected}
            </Typography.Paragraph>
          </Card>
        </Col>
      </Row>
      <Divider style={{ margin: "12px 0" }} />
      <Space direction="vertical" size={8} className="ddp-stack">
        <Space size={8} wrap>
          <Typography.Text>优胜方：</Typography.Text>
          {WINNER_OPTIONS.map((option) => (
            <Tag.CheckableTag
              key={option}
              checked={winner === option}
              data-testid={`llm-pref-winner-${option}`}
              onClick={() => emit({ winner: option })}
            >
              {option}
            </Tag.CheckableTag>
          ))}
        </Space>
        <Space size={8} wrap align="center">
          <Typography.Text>置信度：</Typography.Text>
          <Input
            type="number"
            min={0}
            max={1}
            step={0.1}
            style={{ width: 120 }}
            placeholder="0~1"
            value={confidence === null ? "" : confidence}
            onChange={(event) => {
              const raw = event.target.value;
              emit({ confidence: raw === "" ? null : Number(raw) });
            }}
            data-testid="llm-pref-confidence"
          />
        </Space>
        <TextArea
          rows={2}
          placeholder="选择理由（rationale）"
          value={rationale ?? ""}
          onChange={(event) => emit({ rationale: event.target.value })}
          data-testid="llm-pref-rationale"
        />
      </Space>
      {payload.annotator_rationale ? (
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          预填理由：{payload.annotator_rationale}
        </Typography.Paragraph>
      ) : null}
    </Card>
  );
}
