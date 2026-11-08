import {
  Alert,
  Button,
  Card,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import {
  ARBITRATION_TRIGGER_VALUES,
  type AnnotationArbitration,
  type AnnotationSubmission,
  type ArbitrationDecision,
  type ArbitrationTrigger,
} from "../../types/annotation";
import { formatDateTime } from "../../services/format";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

export interface ArbitrationCreateFormInput {
  arbiterIds: string[];
  quorum: number;
  trigger: string;
  reviewId: string | null;
}

interface ArbitrationPanelProps {
  submission: AnnotationSubmission;
  arbitrations: AnnotationArbitration[];
  busyAction: string | null;
  onCreate: (input: ArbitrationCreateFormInput) => void;
  onAction: (
    arbitration: AnnotationArbitration,
    action: string,
    reason: string | null,
  ) => void;
  onVote: (
    arbitration: AnnotationArbitration,
    decision: ArbitrationDecision,
    reason: string | null,
  ) => void;
}

const DECISION_OPTIONS: { value: ArbitrationDecision; label: string }[] = [
  { value: "APPROVE", label: "通过" },
  { value: "REJECT", label: "驳回" },
  { value: "REWORK", label: "返工" },
  { value: "ABSTAIN", label: "弃权" },
];

function ArbitrationCard({
  arbitration,
  busyAction,
  onAction,
  onVote,
}: {
  arbitration: AnnotationArbitration;
  busyAction: string | null;
  onAction: ArbitrationPanelProps["onAction"];
  onVote: ArbitrationPanelProps["onVote"];
}) {
  const [decision, setDecision] = useState<ArbitrationDecision>("APPROVE");
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const inArbitration = arbitration.status === "IN_ARBITRATION";
  const pending = arbitration.status === "PENDING";
  const quorumMet = arbitration.effective_vote_count >= arbitration.quorum;
  const prefix = `arb:${arbitration.arbitration_id}`;

  const resolve = (action: string) => {
    if (reason.trim() === "") {
      setLocalError("仲裁结论必须填写原因。");
      return;
    }
    setLocalError(null);
    onAction(arbitration, action, reason.trim());
  };

  return (
    <Card size="small" title={`仲裁 v${arbitration.version}`}>
      <Space direction="vertical" size={8} className="ddp-stack">
        <Space wrap>
          <AnnotationStatusTag value={arbitration.status} prefix="仲裁" />
          <Tag>触发：{arbitration.trigger}</Tag>
          <Tag>
            法定人数 {arbitration.effective_vote_count}/{arbitration.quorum}
          </Tag>
        </Space>
        <Space wrap>
          <Typography.Text>仲裁人：</Typography.Text>
          {arbitration.arbiter_ids.map((arbiterId) => (
            <IdentifierValue key={arbiterId} value={arbiterId} label="仲裁人 ID" />
          ))}
        </Space>
        {arbitration.review_id ? (
          <Space>
            <Typography.Text>关联复核：</Typography.Text>
            <IdentifierValue value={arbitration.review_id} label="复核 ID" />
          </Space>
        ) : null}
        {arbitration.votes.length > 0 ? (
          <Space direction="vertical" size={2}>
            {arbitration.votes.map((vote) => (
              <Typography.Text key={vote.arbiter_id} type="secondary">
                {vote.arbiter_id}：{vote.decision}
                {vote.reason ? `（${vote.reason}）` : ""}
              </Typography.Text>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">暂无投票。</Typography.Text>
        )}
        {arbitration.final_decision ? (
          <Alert
            type={arbitration.final_decision === "APPROVE" ? "success" : "warning"}
            showIcon
            message={`最终结论：${arbitration.final_decision}`}
            description={arbitration.reason ?? undefined}
          />
        ) : null}
        {localError ? <Alert type="error" showIcon message={localError} /> : null}
        {pending ? (
          <Button
            disabled={busyAction !== null}
            loading={busyAction === `${prefix}:claim`}
            onClick={() => onAction(arbitration, "claim", null)}
          >
            领取仲裁
          </Button>
        ) : null}
        {inArbitration ? (
          <Space direction="vertical" size={8} className="ddp-stack">
            <Space wrap>
              <Select<ArbitrationDecision>
                value={decision}
                options={DECISION_OPTIONS}
                onChange={setDecision}
                style={{ width: 120 }}
              />
              <Button
                disabled={busyAction !== null}
                loading={busyAction === `${prefix}:vote`}
                onClick={() => onVote(arbitration, decision, reason.trim() || null)}
              >
                投票
              </Button>
            </Space>
            <Input
              placeholder="结论原因（出具结论必填）"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Space wrap>
              <Button
                type="primary"
                disabled={busyAction !== null || !quorumMet}
                loading={busyAction === `${prefix}:resolve-approved`}
                onClick={() => resolve("resolve-approved")}
              >
                结论：通过
              </Button>
              <Button
                danger
                disabled={busyAction !== null || !quorumMet}
                loading={busyAction === `${prefix}:resolve-rejected`}
                onClick={() => resolve("resolve-rejected")}
              >
                结论：驳回
              </Button>
              <Button
                disabled={busyAction !== null || !quorumMet}
                loading={busyAction === `${prefix}:resolve-rework`}
                onClick={() => resolve("resolve-rework")}
              >
                结论：返工
              </Button>
            </Space>
            {!quorumMet ? (
              <Typography.Text type="secondary">
                投票尚未达到法定人数，服务端会拒绝出具结论。
              </Typography.Text>
            ) : null}
          </Space>
        ) : null}
        <Typography.Text type="secondary">
          更新于 {formatDateTime(arbitration.updated_at)} · 仲裁人不得是标注人或首轮复核人。
        </Typography.Text>
      </Space>
    </Card>
  );
}

/** 仲裁面板：创建仲裁、投票与最终结论；法定人数由服务端判定。 */
export default function ArbitrationPanel({
  submission,
  arbitrations,
  busyAction,
  onCreate,
  onAction,
  onVote,
}: ArbitrationPanelProps) {
  const [arbiterText, setArbiterText] = useState("");
  const [quorum, setQuorum] = useState(1);
  const [trigger, setTrigger] = useState<ArbitrationTrigger>(
    "REVIEWER_DISAGREEMENT",
  );
  const [localError, setLocalError] = useState<string | null>(null);
  const canCreate = submission.status === "ESCALATED";
  const arbiterIds = arbiterText
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item !== "");

  const handleCreate = () => {
    if (arbiterIds.length === 0) {
      setLocalError("至少填写一个仲裁人 UUID。");
      return;
    }
    if (quorum < 1 || quorum > arbiterIds.length) {
      setLocalError("法定人数必须在 1 到仲裁人数之间。");
      return;
    }
    setLocalError(null);
    onCreate({
      arbiterIds,
      quorum,
      trigger,
      reviewId: submission.review_id,
    });
  };

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Card size="small" title="创建仲裁">
        <Space direction="vertical" size={8} className="ddp-stack">
          {!canCreate ? (
            <Alert
              type="info"
              showIcon
              message="当前提交状态不可创建仲裁"
              description="只有 ESCALATED 提交可以创建仲裁；请先由复核人升级。"
            />
          ) : null}
          {localError ? <Alert type="error" showIcon message={localError} /> : null}
          <Space wrap>
            <Input
              placeholder="仲裁人 UUID，多个用英文逗号分隔"
              value={arbiterText}
              onChange={(event) => setArbiterText(event.target.value)}
              style={{ width: 380 }}
              disabled={!canCreate || busyAction !== null}
            />
            <InputNumber
              min={1}
              precision={0}
              value={quorum}
              onChange={(value) => setQuorum(value ?? 1)}
              addonBefore="法定人数"
              disabled={!canCreate || busyAction !== null}
            />
            <Select<ArbitrationTrigger>
              value={trigger}
              options={ARBITRATION_TRIGGER_VALUES.map((value) => ({
                value,
                label: value,
              }))}
              onChange={setTrigger}
              disabled={!canCreate || busyAction !== null}
              style={{ width: 230 }}
            />
            <Button
              type="primary"
              disabled={!canCreate || busyAction !== null}
              loading={busyAction === "arb:create"}
              onClick={handleCreate}
            >
              创建仲裁
            </Button>
          </Space>
          <Typography.Text type="secondary">
            仲裁人不得是标注人或首轮复核人；服务端会校验成员资格与利益冲突。
          </Typography.Text>
        </Space>
      </Card>
      {arbitrations.length === 0 ? (
        <PageState
          compact
          variant="empty"
          title="暂无仲裁记录"
          description="升级仲裁后可在此查看投票与最终结论。"
        />
      ) : (
        arbitrations.map((arbitration) => (
          <ArbitrationCard
            key={arbitration.arbitration_id}
            arbitration={arbitration}
            busyAction={busyAction}
            onAction={onAction}
            onVote={onVote}
          />
        ))
      )}
    </Space>
  );
}
