import { Button, Select, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import {
  NOT_APPLICABLE_HINT,
  QUALITY_CATEGORY_LABELS,
  QUALITY_FILTER_ALL,
  filterFindingsBySeverity,
  findingSubjectLabel,
  formatQualityScore,
  presentFindingStatus,
  presentSeverity,
  readRuleCategoryFilter,
  readSeverityFilter,
} from "../../services/qualityRunPresentation";
import {
  QUALITY_RULE_CATEGORY_VALUES,
  QUALITY_SEVERITY_VALUES,
  type QualityRuleCategory,
  type QualityRunFinding,
  type QualitySeverity,
} from "../../types/qualityRun";
import ApiErrorState from "../ui/ApiErrorState";
import PageState from "../ui/PageState";
import QualityToneTag from "./QualityToneTag";

export interface QualityFindingsState {
  items: QualityRunFinding[];
  loading: boolean;
  /** 切换类别后的第一页重载：保留旧条目并显示表格 loading。 */
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  stale: boolean;
}

export interface QualityRunFindingsTableProps {
  findings: QualityFindingsState;
  category: QualityRuleCategory | null;
  severity: QualitySeverity | null;
  onCategoryChange: (value: QualityRuleCategory | null) => void;
  onSeverityChange: (value: QualitySeverity | null) => void;
  onReload: () => void;
  onLoadMore: () => void;
  /** 导入媒体资产页；结论下钻复用既有页面，不新建详情页。 */
  assetsHref: string;
}

function findingColumns(assetsHref: string): TableColumnsType<QualityRunFinding> {
  return [
    {
      title: "类别",
      dataIndex: "category",
      width: 180,
      render: (value: QualityRunFinding["category"]) =>
        QUALITY_CATEGORY_LABELS[value],
    },
    {
      title: "规则",
      key: "rule",
      width: 220,
      render: (_value, finding) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{finding.rule_id}</Typography.Text>
          <Typography.Text type="secondary">
            规则版本 {finding.rule_version}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "级别",
      dataIndex: "severity",
      width: 130,
      render: (value: QualityRunFinding["severity"]) => (
        <QualityToneTag presentation={presentSeverity(value)} />
      ),
    },
    {
      title: "结果",
      dataIndex: "status",
      width: 160,
      render: (_value, finding) => (
        <Space direction="vertical" size={0}>
          <QualityToneTag
            presentation={presentFindingStatus(finding.status)}
            hint={
              finding.status === "NOT_APPLICABLE" || finding.status === "ERROR"
                ? NOT_APPLICABLE_HINT
                : undefined
            }
          />
          <Typography.Text type="secondary">
            {formatQualityScore(finding.score)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "相关对象",
      key: "subject",
      width: 190,
      render: (_value, finding) => (
        <Space direction="vertical" size={0}>
          <Typography.Text code>{findingSubjectLabel(finding)}</Typography.Text>
          <Link
            to={assetsHref}
            aria-label={`查看 ${findingSubjectLabel(finding)} 的相关媒体资产`}
          >
            查看媒体资产
          </Link>
        </Space>
      ),
    },
    {
      title: "说明",
      dataIndex: "message",
      render: (value: string) => <Typography.Text>{value}</Typography.Text>,
    },
  ];
}

/**
 * 一次质量运行的结论明细与筛选。
 *
 * 类别筛选由服务端执行（契约支持 `category`），切换时重新从第一页拉取；
 * 严重度筛选只能作用于**已加载条目**，因此必须在界面上明确标注范围。
 */
export default function QualityRunFindingsTable({
  findings,
  category,
  severity,
  onCategoryChange,
  onSeverityChange,
  onReload,
  onLoadMore,
  assetsHref,
}: QualityRunFindingsTableProps) {
  const visible = filterFindingsBySeverity(findings.items, severity);
  const severityFilterNarrowsLoaded =
    severity !== null && findings.hasMore && findings.items.length > 0;

  if (findings.error && findings.items.length === 0) {
    return <ApiErrorState error={findings.error} onRetry={onReload} compact />;
  }

  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <Space wrap size={12} align="center">
        <Select
          aria-label="按类别筛选质量结论"
          style={{ width: 240 }}
          value={category ?? QUALITY_FILTER_ALL}
          onChange={(value: string) =>
            onCategoryChange(readRuleCategoryFilter(value))
          }
          options={[
            { value: QUALITY_FILTER_ALL, label: "全部类别" },
            ...QUALITY_RULE_CATEGORY_VALUES.map((value) => ({
              value,
              label: QUALITY_CATEGORY_LABELS[value],
            })),
          ]}
        />
        <Select
          aria-label="按严重度筛选质量结论"
          style={{ width: 200 }}
          value={severity ?? QUALITY_FILTER_ALL}
          onChange={(value: string) =>
            onSeverityChange(readSeverityFilter(value))
          }
          options={[
            { value: QUALITY_FILTER_ALL, label: "全部严重度" },
            ...QUALITY_SEVERITY_VALUES.map((value) => ({
              value,
              label: presentSeverity(value).label,
            })),
          ]}
        />
        <Typography.Text type="secondary">
          已加载 {findings.items.length} 条结论
          {findings.hasMore ? "，还有更早结论未加载" : ""}
          ；严重度筛选作用于已加载条目。
        </Typography.Text>
      </Space>
      {severityFilterNarrowsLoaded ? (
        <PageState
          compact
          variant="partial"
          title="严重度筛选只覆盖已加载结论"
          description={`当前仅加载了 ${findings.items.length} 条结论，更早的结论尚未拉取；加载更多后再判断该严重度是否还有条目。`}
        />
      ) : null}
      {findings.stale ? (
        <PageState
          compact
          variant="stale"
          description="刷新失败，展示上次成功获取的结论明细。"
          onRetry={onReload}
        />
      ) : null}
      <Table<QualityRunFinding>
        rowKey="finding_id"
        size="small"
        loading={findings.loading || findings.refreshing}
        columns={findingColumns(assetsHref)}
        dataSource={visible}
        pagination={false}
        scroll={{ x: 1200 }}
        expandable={{
          rowExpandable: (finding) => finding.evidence !== null,
          expandedRowRender: (finding) => (
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                fontSize: 12,
              }}
            >
              {JSON.stringify(finding.evidence, null, 2)}
            </pre>
          ),
        }}
        locale={{
          emptyText:
            findings.items.length === 0
              ? "本次运行没有返回结论（不代表通过）"
              : "当前筛选没有匹配的结论",
        }}
      />
      {findings.hasMore ? (
        <Button block loading={findings.loadingMore} onClick={onLoadMore}>
          加载更多结论
        </Button>
      ) : null}
      {findings.loadMoreError ? (
        <PageState
          compact
          variant="partial"
          description={`加载更多结论失败：${findings.loadMoreError.message}；已展示的结论仍然有效。`}
          onRetry={onLoadMore}
        />
      ) : null}
    </Space>
  );
}
