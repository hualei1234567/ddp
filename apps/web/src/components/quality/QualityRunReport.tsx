import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Space,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  INCOMPLETE_RUN_HINT,
  NOT_APPLICABLE_HINT,
  PASSED_SCOPE_HINT,
  QUALITY_CATEGORY_LABELS,
  formatQualityScore,
  isRunIncomplete,
  notApplicableCategories,
  presentCategoryStatus,
  presentRunStatus,
} from "../../services/qualityRunPresentation";
import type {
  QualityCategorySummary,
  QualityRuleCategory,
  QualityRun,
  QualitySeverity,
} from "../../types/qualityRun";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import QualityRunFindingsTable, {
  type QualityFindingsState,
} from "./QualityRunFindingsTable";
import QualityToneTag from "./QualityToneTag";

export type { QualityFindingsState };

export interface QualityRunReportProps {
  phase: PagePhase;
  run: QualityRun | null;
  refreshing: boolean;
  stale: boolean;
  error: ApiError | null;
  /** POST 触发中的状态；只影响按钮，不改变已有结论。 */
  running: boolean;
  runError: ApiError | null;
  findings: QualityFindingsState;
  category: QualityRuleCategory | null;
  severity: QualitySeverity | null;
  onCategoryChange: (value: QualityRuleCategory | null) => void;
  onSeverityChange: (value: QualitySeverity | null) => void;
  onReload: () => void;
  onRun: () => void;
  onLoadMoreFindings: () => void;
  /** 导入媒体资产页；结论下钻复用既有页面，不新建详情页。 */
  assetsHref: string;
  /** 非当前导入的运行（深链进入其他对象）时禁用下钻与重跑。 */
  disabledReason?: string | null;
}

const categoryColumns: TableColumnsType<QualityCategorySummary> = [
  {
    title: "类别",
    dataIndex: "category",
    width: 210,
    render: (value: QualityCategorySummary["category"]) =>
      QUALITY_CATEGORY_LABELS[value],
  },
  {
    title: "结论",
    dataIndex: "status",
    width: 250,
    render: (value: QualityCategorySummary["status"]) => (
      <QualityToneTag
        presentation={presentCategoryStatus(value)}
        hint={value === "NOT_APPLICABLE" ? NOT_APPLICABLE_HINT : undefined}
      />
    ),
  },
  {
    title: "规则数",
    dataIndex: "finding_count",
    width: 90,
  },
  {
    title: "通过 / 未通过 / 无结论 / 执行失败",
    key: "counts",
    width: 240,
    render: (_value, item) =>
      `${item.pass_count} / ${item.fail_count} / ${item.not_applicable_count} / ${item.error_count}`,
  },
  {
    title: "可判定平均分",
    dataIndex: "score",
    width: 170,
    render: (value: number | null) => formatQualityScore(value),
  },
];

function RunHeader({ run }: { run: QualityRun }) {
  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} size="small" bordered>
        <Descriptions.Item label="运行 ID">
          <IdentifierValue value={run.run_id} label="质量运行 ID" />
        </Descriptions.Item>
        <Descriptions.Item label="整体结论">
          <QualityToneTag presentation={presentRunStatus(run.status)} />
        </Descriptions.Item>
        <Descriptions.Item label="检查对象">
          <Space direction="vertical" size={0}>
            <Typography.Text>{run.subject_type}</Typography.Text>
            <IdentifierValue value={run.subject_id} label="检查对象 ID" />
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="阻断 / 提醒">
          ERROR {run.error_count} · WARN {run.warn_count}
        </Descriptions.Item>
        <Descriptions.Item label="结论总数">
          {run.finding_count}
        </Descriptions.Item>
        <Descriptions.Item label="可判定平均分">
          {formatQualityScore(run.score)}
        </Descriptions.Item>
        <Descriptions.Item label="规则集版本">
          <Typography.Text code ellipsis style={{ maxWidth: 260 }}>
            {run.rule_set_version}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="执行时间">
          {formatDateTime(run.started_at)} → {formatDateTime(run.finished_at)}
        </Descriptions.Item>
        <Descriptions.Item label="创建人">
          <IdentifierValue value={run.created_by} label="创建人 ID" />
        </Descriptions.Item>
      </Descriptions>
      {isRunIncomplete(run) ? (
        <PageState
          compact
          variant="partial"
          title="本次运行没有可判定结论（INCOMPLETE）"
          description={INCOMPLETE_RUN_HINT}
        />
      ) : run.status === "FAILED" ? (
        <PageState
          compact
          variant="partial"
          title="存在 ERROR 级阻断违规"
          description="整体结论为 FAILED；请按结论明细逐条处理，服务端不会把本次运行改判为通过。"
        />
      ) : (
        <Typography.Text type="secondary">{PASSED_SCOPE_HINT}</Typography.Text>
      )}
    </Space>
  );
}

/**
 * 统一质量运行的展示层（不发请求，便于静态渲染测试）。
 *
 * 关键事实必须如实呈现：
 * 1. `INCOMPLETE` / `NOT_APPLICABLE` 都不是通过；
 * 2. 六类聚合与每条结论都来自服务端，本组件不重新判定；
 * 3. 页面自动展示该对象最近一次运行；历史运行可换运行 ID 回看，
 *    展示层本身不推断"哪次运行代表当前状态"。
 */
export default function QualityRunReport({
  phase,
  run,
  refreshing,
  stale,
  error,
  running,
  runError,
  findings,
  category,
  severity,
  onCategoryChange,
  onSeverityChange,
  onReload,
  onRun,
  onLoadMoreFindings,
  assetsHref,
  disabledReason = null,
}: QualityRunReportProps) {
  const rerunButton = (
    <Button
      type="primary"
      icon={<SyncOutlined />}
      loading={running}
      disabled={disabledReason !== null}
      onClick={onRun}
    >
      重新执行检查
    </Button>
  );

  let body;
  if (phase === "forbidden") {
    body = error ? <ApiErrorState error={error} onRetry={onReload} /> : null;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载统一质量检查"
        description="读取六类聚合与逐条结论。"
      />
    );
  } else if (phase === "error" || phase === "not-found") {
    body = error ? (
      <Space direction="vertical" size={12} className="ddp-stack">
        <ApiErrorState error={error} onRetry={onReload} />
        <Space wrap>
          <Button
            type="primary"
            icon={<SyncOutlined />}
            loading={running}
            disabled={disabledReason !== null}
            onClick={onRun}
          >
            执行一次新的质量检查
          </Button>
          <Typography.Text type="secondary">
            本次质量运行无法读取；执行新检查会追加一条运行并更新本页链接。
          </Typography.Text>
        </Space>
      </Space>
    ) : null;
  } else if (run === null) {
    body = (
      <Space direction="vertical" size={12} className="ddp-stack">
        {runError ? (
          <ApiErrorState error={runError} compact onRetry={onRun} />
        ) : null}
        <PageState
          variant="empty"
          title="尚未质检"
          description="该导入还没有统一质量运行记录。点击下方按钮会追加一次新的运行（只追加，不覆盖历史结论）；已有历史运行时会自动展示最近一次。"
          action={
            <Button
              type="primary"
              icon={<SyncOutlined />}
              loading={running}
              onClick={onRun}
            >
              执行质量检查
            </Button>
          }
        />
      </Space>
    );
  } else {
    const unavailable = notApplicableCategories(run);
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {disabledReason ? (
          <PageState
            compact
            variant="disabled"
            title="本页无法下钻该运行"
            description={disabledReason}
          />
        ) : null}
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="刷新失败，展示上次成功获取的质量运行。"
            onRetry={onReload}
          />
        ) : null}
        {runError ? (
          <ApiErrorState error={runError} compact onRetry={onRun} />
        ) : null}
        <RunHeader run={run} />
        {unavailable.length > 0 ? (
          <PageState
            compact
            variant="partial"
            title={`${unavailable.length} 个类别没有结论（不适用）`}
            description={`${unavailable
              .map((item) => QUALITY_CATEGORY_LABELS[item])
              .join("、")} 没有可判定结论，不代表通过。`}
          />
        ) : null}
        <Typography.Title level={5} style={{ margin: 0 }}>
          六类摘要
        </Typography.Title>
        <Table<QualityCategorySummary>
          rowKey="category"
          size="small"
          columns={categoryColumns}
          dataSource={run.categories}
          pagination={false}
          scroll={{ x: 960 }}
        />
        <Space wrap size={12} align="center">
          <Typography.Title level={5} style={{ margin: 0 }}>
            结论明细
          </Typography.Title>
          <Link to={assetsHref}>查看该导入媒体资产</Link>
        </Space>
        <QualityRunFindingsTable
          findings={findings}
          category={category}
          severity={severity}
          onCategoryChange={onCategoryChange}
          onSeverityChange={onSeverityChange}
          onReload={onReload}
          onLoadMore={onLoadMoreFindings}
          assetsHref={assetsHref}
        />
        <Typography.Text type="secondary">
          质量结论由服务端规则判定并以只追加记录保存；本页不重新计算，也不把
          无结论或未执行的规则当作通过。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <Card
      title="统一质量检查（六类）"
      extra={
        <Space wrap>
          {run ? (
            <Button
              icon={<ReloadOutlined />}
              loading={refreshing}
              onClick={onReload}
            >
              刷新
            </Button>
          ) : null}
          {run ? rerunButton : null}
        </Space>
      }
    >
      {body}
    </Card>
  );
}
