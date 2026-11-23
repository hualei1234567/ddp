import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";

import type { ApiError } from "../../services/api";
import type {
  QualityDiffKind,
  QualityDiffStatus,
  QualityFindingDiff,
  QualityRunDiffReport,
} from "../../types/qualityRun";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import QualityToneTag from "./QualityToneTag";

export interface QualityRunDiffReportPanelProps {
  /** 服务端差异报告；null 表示尚未获取（加载/错误/未选择 candidate）。 */
  report: QualityRunDiffReport | null;
  loading: boolean;
  error: ApiError | null;
  /** 请求差异报告；入参是候选运行 ID（基线由页面上下文固定）。 */
  onCompare: (candidateRunId: string) => void;
  /** 页面是否有可用的基线运行（详情页当前运行）。 */
  hasBaseline: boolean;
  /** 加载失败后的重试（页面按最后选择的候选运行重新请求）。 */
  onRetry: () => void;
}

/**
 * 服务端四分类对应的分组展示配置。
 *
 * 标题与「后端实际四类命名」一一对应（NEW / RESOLVED / UNCHANGED /
 * CHANGED）；分组由前端按 `kind` 归集，不重新判定差异类别。
 */
const DIFF_KIND_GROUPS: {
  kind: QualityDiffKind;
  title: string;
  description: string;
  tone: "error" | "success" | "neutral" | "warning";
}[] = [
  {
    kind: "NEW",
    title: "新增问题",
    description: "只在候选运行出现——重跑新引入的问题（算作回归）。",
    tone: "error",
  },
  {
    kind: "RESOLVED",
    title: "已消除问题",
    description: "只在基线运行出现——重跑修复的问题。",
    tone: "success",
  },
  {
    kind: "UNCHANGED",
    title: "无变化",
    description: "两次运行结论相同。",
    tone: "neutral",
  },
  {
    kind: "CHANGED",
    title: "结论变化",
    description: "两次运行都有结论但状态不同；可能是修好也可能是变坏。",
    tone: "warning",
  },
];

/** 两侧结论标签；null（该侧没有结论）不伪造结论，显示「无结论」。 */
function presentDiffStatus(status: QualityDiffStatus | null): string {
  switch (status) {
    case "PASS":
      return "通过";
    case "FAIL":
      return "未通过";
    case "NOT_APPLICABLE":
      return "无结论";
    case "ERROR":
      return "执行失败（无结论）";
    default:
      return "无结论";
  }
}

function diffStatusTone(status: QualityDiffStatus | null) {
  switch (status) {
    case "PASS":
      return { label: presentDiffStatus(status), tone: "success" as const };
    case "FAIL":
      return { label: presentDiffStatus(status), tone: "error" as const };
    case "ERROR":
      return { label: presentDiffStatus(status), tone: "warning" as const };
    default:
      return { label: presentDiffStatus(status), tone: "neutral" as const };
  }
}

const KIND_LABELS: Record<QualityDiffKind, string> = {
  NEW: "新增",
  RESOLVED: "已消除",
  UNCHANGED: "无变化",
  CHANGED: "结论变化",
};

function columnsFor(): TableColumnsType<QualityFindingDiff> {
  return [
    {
      title: "规则",
      dataIndex: "rule_id",
      width: 200,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "对象标识",
      dataIndex: "subject_ref",
      width: 160,
      render: (value: string) =>
        value === "" ? (
          <Typography.Text type="secondary">—（对象级）</Typography.Text>
        ) : (
          <Typography.Text code>{value}</Typography.Text>
        ),
    },
    {
      title: "基线结论",
      dataIndex: "baseline_status",
      width: 130,
      render: (value: QualityDiffStatus | null) => (
        <QualityToneTag presentation={diffStatusTone(value)} />
      ),
    },
    {
      title: "候选结论",
      dataIndex: "candidate_status",
      width: 130,
      render: (value: QualityDiffStatus | null) => (
        <QualityToneTag presentation={diffStatusTone(value)} />
      ),
    },
    {
      title: "分类",
      dataIndex: "kind",
      width: 110,
      render: (value: QualityDiffKind) => KIND_LABELS[value],
    },
  ];
}

/**
 * 差异报告面板（V31-033 / FR-027）。
 *
 * 渲染服务端 `GET /quality-runs/{baseline}/diff/{candidate}` 的结果：
 * 分组按服务端 `kind` 归集，计数取报告字段（解析层已校验一致）；
 * `has_regression` 只统计 NEW（CHANGED 可能是修好，不算回归）。
 */
export default function QualityRunDiffReportPanel({
  report,
  loading,
  error,
  onCompare,
  hasBaseline,
  onRetry,
}: QualityRunDiffReportPanelProps) {
  const columns = columnsFor();

  let body: ReactNode;
  if (loading) {
    body = <PageState variant="loading" title="正在加载差异报告" />;
  } else if (error !== null) {
    body = <ApiErrorState error={error} onRetry={onRetry} />;
  } else if (report === null) {
    body = (
      <PageState
        variant="empty"
        title="尚未生成差异报告"
        description={
          hasBaseline
            ? "选择一个候选运行后对比基线运行，查看重跑前后的结论差异。"
            : "基线运行尚未产出结论（未完成质检），无法对比；没有可信结论就没有可比性。"
        }
      />
    );
  } else {
    const counts: Record<QualityDiffKind, number> = {
      NEW: report.new_count,
      RESOLVED: report.resolved_count,
      UNCHANGED: report.unchanged_count,
      CHANGED: report.changed_count,
    };
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
          <Descriptions.Item label="基线运行">
            <Typography.Text code>{report.baseline_run_id}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="候选运行">
            <Typography.Text code>{report.candidate_run_id}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="回归">
            {report.has_regression
              ? `是（新增 ${report.new_count} 条）`
              : "否"}
          </Descriptions.Item>
        </Descriptions>
        {report.has_regression ? (
          <Alert
            type="error"
            showIcon
            message="重跑引入回归"
            description={`候选运行新增了 ${report.new_count} 条基线没有的问题；结论变化（CHANGED）不计入回归——它可能是修好也可能是变坏，需逐条查看。`}
          />
        ) : (
          <Alert
            type="success"
            showIcon
            message="未发现回归"
            description="候选运行没有引入基线不存在的新问题（NEW 为 0）；结论变化仍可能是变坏，请按需逐条核对。"
          />
        )}
        {report.findings.length === 0 ? (
          <PageState
            variant="empty"
            title="两次运行的结论完全一致"
            description="按 rule_id + 对象标识对齐后没有任何差异；重跑未改变任何结论。"
          />
        ) : (
          DIFF_KIND_GROUPS.map((group) => {
            const items = report.findings.filter(
              (item) => item.kind === group.kind,
            );
            return (
              <Space
                key={group.kind}
                direction="vertical"
                size={8}
                style={{ width: "100%" }}
              >
                <Space wrap size={8} align="center">
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    {group.title}（{counts[group.kind]}）
                  </Typography.Title>
                  <Typography.Text type="secondary">
                    {group.description}
                  </Typography.Text>
                </Space>
                {items.length === 0 ? (
                  <Typography.Text type="secondary">本类无条目</Typography.Text>
                ) : (
                  <Table<QualityFindingDiff>
                    rowKey={(item) => `${item.rule_id}:${item.subject_ref}`}
                    size="small"
                    columns={columns}
                    dataSource={items}
                    pagination={false}
                    scroll={{ x: 720 }}
                  />
                )}
              </Space>
            );
          })
        )}
        <Typography.Text type="secondary">
          差异由服务端按 rule_id + 对象标识对齐并分类；对齐键区分同一规则作用于
          多个对象，前端不重新计算。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <Card
      title="差异报告（基线 vs 候选）"
      extra={
        <Button
          icon={<ReloadOutlined />}
          disabled={report === null}
          onClick={() =>
            report !== null && onCompare(report.candidate_run_id)
          }
        >
          重新对比
        </Button>
      }
    >
      {body}
    </Card>
  );
}
