import { ReloadOutlined } from "@ant-design/icons";
import { Button, DatePicker, Input, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import type { ApiError } from "../../services/api";
import { formatActor, presentAuditResult } from "../../services/auditLogPresentation";
import { formatDateTime, formatIdentifier, formatText } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  AUDIT_RESULT_VALUES,
  type AuditLog,
  type AuditLogListFilters,
} from "../../types/auditLog";
import AuditLogDetailDrawer from "./AuditLogDetailDrawer";

export interface AuditLogListBodyProps {
  filters: AuditLogListFilters;
  phase: PagePhase;
  items: AuditLog[];
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  stale: boolean;
  selected: AuditLog | null;
  onFiltersChange: (patch: Partial<AuditLogListFilters>) => void;
  onClearFilters: () => void;
  onReload: () => void;
  onLoadMore: () => void;
  onSelect: (item: AuditLog | null) => void;
  /** 从详情切到"只看该资源"后，由页面决定是否回填过滤条件。 */
  onFocusResource?: (item: AuditLog) => void;
}

const RESULT_OPTIONS = AUDIT_RESULT_VALUES.map((value) => ({
  value,
  label: value,
}));

const columns: ColumnsType<AuditLog> = [
  {
    title: "发生时间",
    dataIndex: "occurred_at",
    width: 180,
    render: (value: string) => formatDateTime(value),
  },
  {
    title: "结果",
    dataIndex: "result",
    width: 100,
    render: (value: string) => (
      <StatusTag value={value} presentation={presentAuditResult} />
    ),
  },
  {
    title: "资源类型",
    dataIndex: "resource_type",
    width: 170,
    render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
  },
  {
    title: "动作",
    dataIndex: "action",
    width: 150,
  },
  {
    title: "状态迁移",
    key: "status",
    width: 170,
    render: (_, item) =>
      item.from_status || item.to_status
        ? `${formatText(item.from_status)} → ${formatText(item.to_status)}`
        : "—",
  },
  {
    title: "操作者",
    key: "actor",
    width: 190,
    render: (_, item) => (
      <Typography.Text>{formatActor(item.actor_role, item.actor_user_id)}</Typography.Text>
    ),
  },
  {
    title: "原因",
    dataIndex: "reason",
    render: (value: string | null) => formatText(value),
  },
  {
    title: "资源 ID",
    dataIndex: "resource_id",
    width: 160,
    render: (value: string) => (
      <Typography.Text code>{formatIdentifier(value)}</Typography.Text>
    ),
  },
];

function toDayRange(filters: AuditLogListFilters): [Dayjs, Dayjs] | null {
  if (!filters.occurred_from || !filters.occurred_to) {
    return null;
  }
  const start = dayjs(filters.occurred_from);
  const end = dayjs(filters.occurred_to);
  return start.isValid() && end.isValid() ? [start, end] : null;
}

/**
 * 审计日志列表展示层。
 *
 * 只有读：没有新建、编辑、删除按钮——审计记录不可篡改。
 * 过滤条件由上层写入 URL（可分享、可回退），本组件只负责触发变更。
 */
export default function AuditLogListBody({
  filters,
  phase,
  items,
  loading,
  refreshing,
  loadingMore,
  hasMore,
  error,
  loadMoreError,
  stale,
  selected,
  onFiltersChange,
  onClearFilters,
  onReload,
  onLoadMore,
  onSelect,
  onFocusResource,
}: AuditLogListBodyProps) {
  const forbidden = phase === "forbidden";
  const hasFilters = Object.values(filters).some(
    (value) => value !== undefined && value !== "",
  );

  return (
    <PageContainer
      title="审计日志"
      description="谁在什么时候对什么资源做了什么。只读、不可篡改；仅项目负责人与平台管理员可查看。"
      filters={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="资源类型，如 RAW_ASSET"
            style={{ width: 220 }}
            defaultValue={filters.resource_type ?? ""}
            disabled={forbidden}
            onSearch={(value) =>
              onFiltersChange({ resource_type: value.trim() || undefined })
            }
          />
          <Input.Search
            allowClear
            placeholder="动作，如 create"
            style={{ width: 180 }}
            defaultValue={filters.action ?? ""}
            disabled={forbidden}
            onSearch={(value) =>
              onFiltersChange({ action: value.trim() || undefined })
            }
          />
          <Select
            allowClear
            value={filters.result}
            style={{ width: 140 }}
            placeholder="全部结果"
            options={RESULT_OPTIONS}
            disabled={forbidden}
            onChange={(value: AuditLogListFilters["result"]) =>
              onFiltersChange({ result: value })
            }
          />
          <DatePicker.RangePicker
            value={toDayRange(filters)}
            disabled={forbidden}
            allowClear
            placeholder={["开始日期", "结束日期"]}
            onChange={(value: [Dayjs | null, Dayjs | null] | null) => {
              if (!value || !value[0] || !value[1]) {
                onFiltersChange({
                  occurred_from: undefined,
                  occurred_to: undefined,
                });
                return;
              }
              // 区间按本地自然日闭区间提交；服务端要求带时区，转 ISO 后即 UTC。
              onFiltersChange({
                occurred_from: value[0].startOf("day").toISOString(),
                occurred_to: value[1].endOf("day").toISOString(),
              });
            }}
          />
          <Button disabled={!hasFilters} onClick={onClearFilters}>
            清空筛选
          </Button>
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={onReload}>
          刷新
        </Button>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<AuditLog>
        rowKey="audit_id"
        size="small"
        loading={loading}
        columns={forbidden ? [] : columns}
        dataSource={forbidden ? [] : items}
        pagination={false}
        scroll={{ x: 1180 }}
        locale={{
          emptyText: forbidden
            ? "无权查看"
            : error
              ? "加载失败"
              : "暂无审计记录",
        }}
        onRow={(item) => ({
          onClick: () => onSelect(item),
          style: { cursor: "pointer" },
        })}
      />
      {error ? <ApiErrorState error={error} onRetry={onReload} /> : null}
      {hasMore ? (
        <Button
          block
          style={{ marginTop: 12 }}
          loading={loadingMore}
          disabled={forbidden}
          onClick={onLoadMore}
        >
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <PageState compact variant="partial" action={<Button onClick={onLoadMore}>重试</Button>} />
      ) : null}
      <AuditLogDetailDrawer
        item={selected}
        onClose={() => onSelect(null)}
        onFocusResource={(item) => {
          onSelect(null);
          onFocusResource?.(item);
        }}
      />
    </PageContainer>
  );
}
