import { ReloadOutlined, SettingOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  List,
  Select,
  Space,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import type { ApiError } from "../../services/api";
import {
  presentExceeded,
  presentLimit,
  presentQuotaStatus,
  presentRemaining,
  presentUsed,
  resourceKindLabel,
  UNCOMPUTABLE_REMAINING_LABEL,
  UNLIMITED_LIMIT_LABEL,
  UNMEASURED_USED_HINT,
  UNMEASURED_USED_LABEL,
  type UsageRow,
} from "../../services/analyticsPresentation";
import { formatDateTime, formatText } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  QUOTA_STATUS_VALUES,
  type QuotaStatus,
} from "../../types/governance";

export interface UsageQuotaTableProps {
  phase: PagePhase;
  /** 已按状态筛选的行。 */
  rows: UsageRow[];
  /** 筛选前的条目数，用于区分「服务端没有条目」与「筛选后为空」。 */
  totalRows: number;
  statusFilter: QuotaStatus | undefined;
  /** 报表生成时间（`generated_at`），即「数据统计于何时」。 */
  generatedAt: string | null;
  refreshing: boolean;
  stale: boolean;
  error: ApiError | null;
  /** 配额配置（含 If-Match 版本）读取失败；报表仍可用，但修改上限暂不可行。 */
  quotaConfigError: ApiError | null;
  online: boolean;
  onStatusFilterChange: (status: QuotaStatus | undefined) => void;
  onRefresh: () => void;
  onRetryQuotaConfig: () => void;
  onEdit: (row: UsageRow) => void;
}

const STATUS_OPTIONS = QUOTA_STATUS_VALUES.map((value) => ({
  value,
  label: `${presentQuotaStatus(value).label}（${value}）`,
}));

const CONVENTIONS = [
  "「未测量」（used = null）表示服务端尚无可用统计，不是用量为 0；真实测到 0 才会显示 0。",
  "「未配置上限」（limit = null）表示该类资源没有设上限，不是上限为 0；上限为 0 会显示 0。",
  "UNMEASURED 与 OK 是两种结论：前者是「没有结论」，后者是「已测量且在限内」，因此状态标签不做合并。",
  "剩余量超限时由服务端截断为 0，「已超限」以 exceeded 字段为准；“无法判断”不等于“未超限”。",
  "统计截止时间取服务端 measured_at；为空说明该条目没有测量时间，界面不填充当前时间。",
];

/**
 * 用量—配额对照表（纯展示，不发请求，便于静态渲染测试）。
 *
 * 权限以服务端为准：403 时整页 Forbidden；`PUT` 被拒也如实提示，
 * 不通过隐藏按钮来假装「没有这个操作」。
 */
export default function UsageQuotaTable({
  phase,
  rows,
  totalRows,
  statusFilter,
  generatedAt,
  refreshing,
  stale,
  error,
  quotaConfigError,
  online,
  onStatusFilterChange,
  onRefresh,
  onRetryQuotaConfig,
  onEdit,
}: UsageQuotaTableProps) {
  const forbidden = phase === "forbidden";

  const columns: ColumnsType<UsageRow> = [
    {
      title: "资源",
      key: "resource",
      width: 210,
      render: (_value, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {resourceKindLabel(row.entry.resource_kind)}
          </Typography.Text>
          <Typography.Text type="secondary" code>
            {row.entry.resource_kind}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "已用",
      key: "used",
      width: 160,
      render: (_value, row) => {
        const used = presentUsed(row.entry);
        if (row.entry.used === null) {
          return (
            <Tooltip title={UNMEASURED_USED_HINT}>
              <Space direction="vertical" size={0}>
                <Typography.Text>{UNMEASURED_USED_LABEL}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  无可用统计，非 0
                </Typography.Text>
              </Space>
            </Tooltip>
          );
        }
        return <Typography.Text>{used}</Typography.Text>;
      },
    },
    {
      title: "上限",
      key: "limit",
      width: 160,
      render: (_value, row) =>
        row.entry.limit === null ? (
          <Typography.Text type="secondary">
            {UNLIMITED_LIMIT_LABEL}
          </Typography.Text>
        ) : (
          <Typography.Text>{presentLimit(row.entry)}</Typography.Text>
        ),
    },
    {
      title: "剩余",
      key: "remaining",
      width: 150,
      render: (_value, row) =>
        row.entry.remaining === null ? (
          <Typography.Text type="secondary">
            {UNCOMPUTABLE_REMAINING_LABEL}
          </Typography.Text>
        ) : (
          <Typography.Text>{presentRemaining(row.entry)}</Typography.Text>
        ),
    },
    {
      title: "状态",
      key: "status",
      width: 190,
      render: (_value, row) => (
        <Space direction="vertical" size={0}>
          <StatusTag
            value={row.entry.status}
            presentation={presentQuotaStatus}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            超限判定：{presentExceeded(row.entry)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "统计截止",
      key: "measured_at",
      width: 180,
      render: (_value, row) => {
        const measuredAt = row.entry.measured_at;
        return (
          <Typography.Text
            type={measuredAt === null ? "secondary" : undefined}
            title={
              measuredAt === null ? "该条目没有测量时间，界面不填充当前时间" : measuredAt
            }
          >
            {formatDateTime(measuredAt)}
          </Typography.Text>
        );
      },
    },
    {
      title: "统计口径",
      key: "measurement_source",
      width: 200,
      render: (_value, row) => (
        <Typography.Text
          type="secondary"
          ellipsis={{ tooltip: row.entry.measurement_source ?? "" }}
        >
          {formatText(row.entry.measurement_source)}
        </Typography.Text>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 150,
      fixed: "right",
      render: (_value, row) => {
        const disabledReason = !online
          ? "当前离线，无法提交配额修改。"
          : quotaConfigError !== null
            ? "配额配置（含版本号）读取失败，缺少 If-Match 版本，暂时无法修改。"
            : null;
        const button = (
          <Button
            size="small"
            icon={<SettingOutlined />}
            disabled={disabledReason !== null}
            onClick={() => onEdit(row)}
          >
            {row.quotaVersion === null ? "设置上限" : "调整上限"}
          </Button>
        );
        return disabledReason === null ? (
          button
        ) : (
          <Tooltip title={disabledReason}>{button}</Tooltip>
        );
      },
    },
  ];

  const emptyText = forbidden
    ? "无权查看用量数据"
    : error
      ? "加载失败"
      : totalRows === 0
        ? "服务端未返回任何用量条目"
        : "当前筛选条件下没有资源";

  return (
    <PageContainer
      title="经营分析"
      description="项目级资源用量与配额的对照：已用 / 上限 / 剩余 / 状态 / 统计截止时间。用量来自服务端离线统计快照，不在浏览器里重新计算；权限与配额校验以服务端为准。"
      filters={
        <Space wrap>
          <Select
            allowClear
            value={statusFilter}
            style={{ width: 220 }}
            placeholder="全部状态"
            options={STATUS_OPTIONS}
            disabled={forbidden}
            onChange={(value: QuotaStatus | undefined) =>
              onStatusFilterChange(value)
            }
          />
          <Button
            disabled={statusFilter === undefined}
            onClick={() => onStatusFilterChange(undefined)}
          >
            清空筛选
          </Button>
        </Space>
      }
      extra={
        <Space size={16} wrap>
          <Typography.Text type="secondary">
            数据统计于：{generatedAt === null ? "未知" : formatDateTime(generatedAt)}
          </Typography.Text>
          <Button
            icon={<ReloadOutlined />}
            loading={refreshing}
            onClick={onRefresh}
          >
            刷新
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={16} className="ddp-stack">
        {!online ? (
          <PageState
            compact
            variant="disabled"
            title="当前离线"
            description="离线状态下仍可查看已加载的用量数据，但无法提交配额修改。"
          />
        ) : null}
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={onRefresh}
          />
        ) : null}
        {quotaConfigError !== null ? (
          <PageState
            compact
            variant="partial"
            title="配额配置不可用"
            description="用量报表已加载，但配额配置（含 If-Match 版本号）读取失败，因此暂时无法修改上限。"
            action={<Button onClick={onRetryQuotaConfig}>重试读取配额</Button>}
          />
        ) : null}

        {phase === "loading" ? (
          <PageState variant="loading" title="正在加载用量报表" />
        ) : (
          <Table<UsageRow>
            rowKey={(row) => row.entry.resource_kind}
            size="small"
            loading={refreshing}
            columns={forbidden ? [] : columns}
            dataSource={forbidden ? [] : rows}
            pagination={false}
            scroll={{ x: 1400 }}
            locale={{ emptyText }}
          />
        )}

        {error ? <ApiErrorState error={error} onRetry={onRefresh} /> : null}

        <Card title="口径说明" size="small">
          <List
            size="small"
            dataSource={CONVENTIONS}
            renderItem={(text) => (
              <List.Item>
                <Typography.Text type="secondary">{text}</Typography.Text>
              </List.Item>
            )}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
