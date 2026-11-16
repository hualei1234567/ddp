import { Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { ApiError } from "../../services/api";
import {
  INCONCLUSIVE_FINDING_HINT,
  presentFindingResult,
  presentRuleSeverity,
  summarizeFindings,
  type QualityTone,
} from "../../services/qualityPresentation";
import { formatText } from "../../services/format";
import type { QualityFinding } from "../../types/quality";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";

const TONE_COLOR: Record<QualityTone, string | undefined> = {
  success: "green",
  error: "red",
  warning: "orange",
  neutral: "default",
};

/**
 * 三态判定标签。
 *
 * `passed === null` 必须单独表达为「无结论」，绝不能落进「通过」分支：
 * 契约 `schemas.stage9.yaml#/QualityFinding` 明文禁止把 `null` 当成通过。
 */
function FindingResultTag({ passed }: { passed: boolean | null }) {
  const presentation = presentFindingResult(passed);
  const tag = (
    <Tag color={TONE_COLOR[presentation.tone]}>{presentation.label}</Tag>
  );
  return presentation.tone === "neutral" ? (
    <Tooltip title={INCONCLUSIVE_FINDING_HINT}>{tag}</Tooltip>
  ) : (
    tag
  );
}

function columns(): TableColumnsType<QualityFinding> {
  return [
    {
      title: "标注记录",
      dataIndex: "annotation_id",
      width: 220,
      render: (value: string) => (
        <IdentifierValue value={value} label="标注记录 ID" />
      ),
    },
    {
      title: "规则",
      key: "rule",
      width: 240,
      render: (_value, finding) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{finding.rule_id}</Typography.Text>
          <Typography.Text type="secondary">
            规则版本 v{finding.rule_version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "级别",
      dataIndex: "severity",
      width: 150,
      render: (value: QualityFinding["severity"]) => {
        const presentation = presentRuleSeverity(value);
        return <Tag color={TONE_COLOR[presentation.tone]}>{presentation.label}</Tag>;
      },
    },
    {
      title: "判定",
      dataIndex: "passed",
      width: 110,
      render: (value: boolean | null) => <FindingResultTag passed={value} />,
    },
    {
      title: "说明",
      dataIndex: "message",
      render: (value: string) => <Typography.Text>{formatText(value)}</Typography.Text>,
    },
  ];
}

export interface QualityFindingTableProps {
  findings: QualityFinding[];
  loading?: boolean;
  /** 请求失败时展示错误态；有旧数据时由页面决定是否保留。 */
  error?: ApiError | null;
  onRetry?: () => void;
}

/**
 * 一次质量检查的逐条结论。
 *
 * 只做展示：判定值来自服务端，本组件不推断、不改写；顶部计数把
 * 「通过 / 未通过 / 无结论」三类分开列出，避免"总数 - 通过 = 未通过"式误读。
 */
export default function QualityFindingTable({
  findings,
  loading = false,
  error = null,
  onRetry,
}: QualityFindingTableProps) {
  const summary = summarizeFindings(findings);

  if (error && findings.length === 0) {
    return <ApiErrorState error={error} onRetry={onRetry} />;
  }

  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Space wrap size={16}>
        <Typography.Text>规则结果共 {summary.total} 条</Typography.Text>
        <Typography.Text type="success">通过 {summary.passedCount}</Typography.Text>
        <Typography.Text type="danger">未通过 {summary.failedCount}</Typography.Text>
        <Typography.Text type="secondary">
          无结论 {summary.inconclusiveCount}
        </Typography.Text>
      </Space>
      {summary.inconclusiveCount > 0 ? (
        <PageState
          compact
          variant="partial"
          title={`${summary.inconclusiveCount} 条规则没有结论`}
          description={INCONCLUSIVE_FINDING_HINT}
        />
      ) : null}
      <Table<QualityFinding>
        rowKey={(finding) =>
          `${finding.annotation_id}:${finding.rule_id}:${finding.rule_version}`
        }
        size="small"
        loading={loading}
        columns={columns()}
        dataSource={findings}
        pagination={false}
        scroll={{ x: 1000 }}
        locale={{
          emptyText: error
            ? "规则结果加载失败"
            : "本次检查没有任何规则结果（不代表通过）",
        }}
      />
      {error && findings.length > 0 ? (
        <PageState
          compact
          variant="stale"
          description={`最近一次刷新失败：${error.message}；展示的是上次成功获取的规则结果。`}
          onRetry={onRetry}
        />
      ) : null}
    </Space>
  );
}
