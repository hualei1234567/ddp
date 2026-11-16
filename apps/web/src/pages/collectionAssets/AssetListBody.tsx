import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import AssetActionBar from "../../components/collectionAssets/AssetActionBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import type { ApiError } from "../../services/api";
import {
  assetId,
  assetKindLabel,
  assetVersionLabel,
  assetVersionText,
  isScenario,
  presentAssetStatus,
} from "../../services/collectionAssetPresentation";
import { assetDetailPath } from "../../services/collectionAssetUrl";
import { formatDateTime } from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";
import {
  COLLECTION_ASSET_STATUS_VALUES,
  type CollectionAsset,
  type CollectionAssetKind,
  type CollectionAssetStatus,
} from "../../types/collectionAsset";

export interface AssetListBodyProps {
  kind: CollectionAssetKind;
  status: CollectionAssetStatus | undefined;
  phase: PagePhase;
  items: CollectionAsset[];
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  stale: boolean;
  busyAssetId: string | null;
  onStatusChange: (value: CollectionAssetStatus | undefined) => void;
  onReload: () => void;
  onLoadMore: () => void;
  onCreate: () => void;
  onAction: (
    asset: CollectionAsset,
    action: string,
    reason: string | null,
  ) => Promise<void>;
  /** 创建弹窗由页面注入，列表本体保持可静态渲染与测试。 */
  createModal?: ReactNode;
}

const STATUS_OPTIONS = COLLECTION_ASSET_STATUS_VALUES.map((value) => ({
  value,
  label: presentAssetStatus(value).label,
}));

/** 场景标签在列表里只展示前几个，完整列表在详情页。 */
const SCENE_LABEL_PREVIEW = 3;

function buildColumns(
  kind: CollectionAssetKind,
  busyAssetId: string | null,
  onAction: AssetListBodyProps["onAction"],
): ColumnsType<CollectionAsset> {
  const versionLabel = assetVersionLabel(kind);
  const columns: ColumnsType<CollectionAsset> = [
    {
      title: "名称 / 编码",
      key: "name",
      render: (_, asset) => (
        <Space direction="vertical" size={0}>
          <Link to={assetDetailPath(kind, assetId(asset))}>{asset.name}</Link>
          <Typography.Text type="secondary">{asset.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (value: string) => (
        <StatusTag value={value} presentation={presentAssetStatus} />
      ),
    },
    {
      title: "版本",
      key: "version",
      width: 140,
      render: (_, asset) => (
        <Space direction="vertical" size={0}>
          {versionLabel ? (
            <Typography.Text type="secondary">{versionLabel}</Typography.Text>
          ) : null}
          <Typography.Text>{assetVersionText(asset)}</Typography.Text>
        </Space>
      ),
    },
  ];

  if (kind === "scenarios") {
    columns.push({
      title: "场景标签",
      key: "scene_labels",
      width: 240,
      render: (_, asset) => {
        if (!isScenario(asset) || asset.scene_labels.length === 0) {
          return "—";
        }
        const preview = asset.scene_labels.slice(0, SCENE_LABEL_PREVIEW);
        const rest = asset.scene_labels.length - preview.length;
        return (
          <Space wrap size={4}>
            {preview.map((label) => (
              <Tag key={label}>{label}</Tag>
            ))}
            {rest > 0 ? (
              <Typography.Text type="secondary">+{rest}</Typography.Text>
            ) : null}
          </Space>
        );
      },
    });
  }

  columns.push(
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 190,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "动作",
      key: "actions",
      width: 200,
      render: (_, asset) => (
        <AssetActionBar
          compact
          actions={asset.allowed_actions}
          busy={busyAssetId === assetId(asset)}
          onAction={(action, reason) => onAction(asset, action, reason)}
        />
      ),
    },
  );

  return columns;
}

/** 采集素材列表的展示层：只接收状态与数据，不发请求。 */
export default function AssetListBody({
  kind,
  status,
  phase,
  items,
  loading,
  refreshing,
  loadingMore,
  hasMore,
  error,
  loadMoreError,
  stale,
  busyAssetId,
  onStatusChange,
  onReload,
  onLoadMore,
  onCreate,
  onAction,
  createModal,
}: AssetListBodyProps) {
  const label = assetKindLabel(kind);
  const forbidden = phase === "forbidden";
  const columns = forbidden
    ? []
    : buildColumns(kind, busyAssetId, onAction);

  return (
    <PageContainer
      title={`采集素材 · ${label}`}
      description="方案 / SOP / 场景 / 动作库的登记与状态管理；动作按钮只按服务端返回的 allowed_actions 渲染，归档为终态。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 150 }}
            options={STATUS_OPTIONS}
            onChange={(value: CollectionAssetStatus | undefined) =>
              onStatusChange(value)
            }
          />
        </Space>
      }
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={forbidden}
            onClick={onCreate}
          >
            新建{label}
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={onReload}>
            刷新
          </Button>
        </Space>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<CollectionAsset>
        rowKey={(asset) => assetId(asset)}
        size="small"
        loading={loading}
        columns={columns}
        dataSource={forbidden ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : `暂无${label}` }}
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
        <PageState
          compact
          variant="partial"
          description={`加载更多失败：${loadMoreError.message}；已展示的数据仍然有效。`}
          onRetry={onLoadMore}
        />
      ) : null}
      {createModal}
    </PageContainer>
  );
}
