import { ReloadOutlined } from "@ant-design/icons";
import { Button, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { supplierApi } from "../../services/suppliers";
import { batchDetailPath } from "../../services/supplierUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { AcceptanceLedgerEntry } from "../../types/supplier";
import { Link } from "react-router-dom";

/** 账本动作的中文展示；未知动作原样显示，不猜测语义。 */
const ACTION_LABEL: Record<string, string> = {
  register: "登记",
  activate: "激活",
  suspend: "暂停",
  resume: "恢复",
  terminate: "终止",
  dispatch: "通知发货",
  arrive: "确认到货",
  "start-screening": "开始验收",
  finalize: "终审",
  rework: "返工",
  close: "关闭",
  "queue-review": "进入审看",
  resubmit: "补件重提",
  "decide:ACCEPT": "裁决：通过",
  "decide:REJECT": "裁决：拒收",
  "decide:NEED_MORE": "裁决：待补件",
};

/** 资源类型的中文展示；未知类型原样显示，不猜测语义。 */
const RESOURCE_LABEL: Record<string, string> = {
  SUPPLIER: "供应商",
  INBOUND_BATCH: "到货批次",
  INBOUND_SEGMENT: "到货片段",
};

function LedgerContent({ projectId }: { projectId: string }) {
  const [searchParams] = useSearchParams();
  const batchId = searchParams.get("batch_id") ?? undefined;
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      supplierApi.listLedger(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        batch_id: batchId,
      }),
    [projectId, batchId],
  );
  const { items, hasMore, loading, refreshing, loadingMore, error, loadMoreError, stale, reload, loadMore } =
    useCursorPage(load, (item) => item.ledger_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: ColumnsType<AcceptanceLedgerEntry> = [
    {
      title: "时间",
      dataIndex: "occurred_at",
      width: 190,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "动作",
      dataIndex: "action",
      width: 140,
      render: (value: string) => <Tag>{ACTION_LABEL[value] ?? value}</Tag>,
    },
    {
      title: "资源",
      key: "resource",
      width: 100,
      render: (_, item) => RESOURCE_LABEL[item.resource_type] ?? item.resource_type,
    },
    {
      title: "状态变化",
      key: "transition",
      width: 200,
      render: (_, item) =>
        item.from_status || item.to_status
          ? `${item.from_status ?? "—"} → ${item.to_status ?? "—"}`
          : "—",
    },
    {
      title: "批次",
      dataIndex: "batch_id",
      width: 200,
      ellipsis: true,
      render: (value: string | null) =>
        value ? <Link to={batchDetailPath(value)}>{value}</Link> : "—",
    },
    { title: "原因", dataIndex: "reason", ellipsis: true, render: (v: string | null) => v ?? "—" },
    {
      title: "request_id",
      dataIndex: "request_id",
      width: 200,
      ellipsis: true,
      render: (value: string | null) => value ?? "—",
    },
  ];

  return (
    <PageContainer
      title="验收核验账本"
      description="只追加的到货验收流水：登记、状态迁移与人工裁决均在此留痕，不可修改或删除。"
      extra={
        <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
          刷新
        </Button>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<AcceptanceLedgerEntry>
        rowKey="ledger_id"
        size="small"
        loading={loading}
        columns={phase === "forbidden" ? [] : columns}
        dataSource={phase === "forbidden" ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无核验记录" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      {hasMore ? (
        <Button block style={{ marginTop: 12 }} loading={loadingMore} onClick={loadMore}>
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <Typography.Text type="danger">加载更多失败：{loadMoreError.message}</Typography.Text>
      ) : null}
    </PageContainer>
  );
}

export default function LedgerPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="验收核验账本" description="账本必须按项目隔离展示。">
        <ProjectScopeRequired scope="验收核验账本" />
      </PageContainer>
    );
  }
  return <LedgerContent projectId={projectId} />;
}
