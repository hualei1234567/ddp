import { ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { formatDateTime } from "../../services/format";
import { formatBytes } from "../../services/uploadQueue";
import { resolvePagePhase } from "../../services/pagePhase";
import { rawAssetsApi } from "../../services/rawAssets";
import {
  ASSET_FORMAT_VALUES,
  RAW_ASSET_STATUS_VALUES,
  type AssetFormat,
  type RawAsset,
  type RawAssetStatus,
} from "../../types/collection";

const STATUS_OPTIONS = RAW_ASSET_STATUS_VALUES.map((value) => ({
  value,
  label: value,
}));

/**
 * 数据资产清单：项目内经校验、可用于切片与数据集的原始资产。
 *
 * 此前导航里的「数据资产」是不可进入的占位页，而服务端 `GET /raw-assets`
 * 早已就绪——数据在库里，用户却没有入口。这里只做检索与跳转，不提供
 * 删除/墓碑等高风险操作（服务端也未开放）。
 */
function RawAssetListContent({
  projectId,
  status,
  format,
  onStatusChange,
  onFormatChange,
  onReload,
}: {
  projectId: string;
  status: RawAssetStatus | undefined;
  format: AssetFormat | undefined;
  onStatusChange: (value: RawAssetStatus | undefined) => void;
  onFormatChange: (value: AssetFormat | undefined) => void;
  onReload: () => void;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      rawAssetsApi.listAssets(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
        format,
      }),
    [projectId, status, format],
  );
  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } = useCursorPage(load, (item) => item.asset_id);
  const phase = resolvePagePhase({ hasData: items.length > 0, loading, error });

  const columns: TableColumnsType<RawAsset> = useMemo(
    () => [
      {
        title: "资产",
        key: "asset",
        width: 260,
        render: (_value, record) => (
          <Space direction="vertical" size={0}>
            <Link to={`/raw-assets/${record.asset_id}`}>
              <Typography.Text strong>{record.original_filename}</Typography.Text>
            </Link>
            <Typography.Text type="secondary">{record.code}</Typography.Text>
          </Space>
        ),
      },
      {
        title: "资产 ID",
        dataIndex: "asset_id",
        width: 210,
        render: (value: string) => (
          <IdentifierValue value={value} label="资产 ID" to={`/raw-assets/${value}`} />
        ),
      },
      {
        title: "状态",
        dataIndex: "status",
        width: 130,
        render: (value: string) => <CollectionStatusTag kind="rawAsset" value={value} />,
      },
      { title: "格式", dataIndex: "format", width: 100 },
      {
        title: "大小",
        dataIndex: "size_bytes",
        width: 110,
        render: (value: number) => formatBytes(value),
      },
      { title: "来源", dataIndex: "source_type", width: 110 },
      {
        title: "采集任务",
        dataIndex: "collection_task_id",
        width: 210,
        render: (value: string | null) =>
          value ? (
            <IdentifierValue value={value} label="任务 ID" to={`/collection/tasks/${value}`} />
          ) : (
            <Typography.Text type="secondary">未归属任务</Typography.Text>
          ),
      },
      {
        title: "入库时间",
        dataIndex: "ingested_at",
        width: 170,
        render: (value: string | null) => formatDateTime(value),
      },
      {
        title: "操作",
        key: "actions",
        width: 120,
        fixed: "right",
        render: (_value, record) => (
          <Link to={`/raw-assets/${record.asset_id}`}>查看详情</Link>
        ),
      },
    ],
    [],
  );

  const retry = () => {
    onReload();
    reload();
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = (
      <PageState
        variant="forbidden"
        title="无权查看数据资产"
        description="服务端未允许当前身份读取该项目的数据资产。"
      />
    );
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载数据资产" />;
  } else if (phase === "error") {
    body = error ? <ApiErrorState error={error} onRetry={retry} /> : null;
  } else if (items.length === 0) {
    body = (
      <PageState
        variant="empty"
        title="当前项目还没有数据资产"
        description="数据资产来自上传、设备采集与数据源导入；完成一次导入或上传后即可在这里检索。"
      />
    );
  } else {
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功获取的数据。"
            onRetry={retry}
          />
        ) : null}
        {error && !stale ? <ApiErrorState error={error} onRetry={retry} compact /> : null}
        <Space wrap>
          <Tag color="blue">已加载 {items.length} 条</Tag>
        </Space>
        <Table<RawAsset>
          rowKey="asset_id"
          columns={columns}
          dataSource={items}
          pagination={false}
          scroll={{ x: 1500 }}
        />
        <LoadMoreBar
          loadedCount={items.length}
          unit="条资产"
          hasMore={hasMore}
          loadingMore={loadingMore}
          disabled={loading || refreshing}
          error={loadMoreError}
          onLoadMore={loadMore}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title="数据资产"
      description="项目内经校验的原始资产（上传 / 设备采集 / 数据源导入），是切片、标注与数据集的输入。"
      extra={
        <Space wrap>
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 160 }}
            options={STATUS_OPTIONS}
            value={status}
            onChange={(value) => onStatusChange(value)}
          />
          <Select
            allowClear
            placeholder="格式"
            style={{ width: 140 }}
            options={ASSET_FORMAT_VALUES.map((value) => ({ value, label: value }))}
            value={format}
            onChange={(value) => onFormatChange(value)}
          />
          <Button icon={<ReloadOutlined />} onClick={retry} loading={loading || refreshing}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function RawAssetListPage() {
  const projectId = useProjectId();
  const [searchParams, setSearchParams] = useSearchParams();
  const [reloadToken, setReloadToken] = useState(0);
  if (!projectId) {
    return (
      <PageContainer title="数据资产" description="数据资产必须按项目隔离展示。">
        <ProjectScopeRequired scope="数据资产" />
      </PageContainer>
    );
  }
  const status = (searchParams.get("status") ?? undefined) as RawAssetStatus | undefined;
  const format = (searchParams.get("format") ?? undefined) as AssetFormat | undefined;

  const updateParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    setSearchParams(next, { replace: true });
  };

  return (
    <RawAssetListContent
      key={`assets:${projectId}:${status ?? ""}:${format ?? ""}:${reloadToken}`}
      projectId={projectId}
      status={status}
      format={format}
      onStatusChange={(value) => updateParam("status", value)}
      onFormatChange={(value) => updateParam("format", value)}
      onReload={() => setReloadToken((token) => token + 1)}
    />
  );
}
