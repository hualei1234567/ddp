import { DeleteOutlined, EditOutlined } from "@ant-design/icons";
import {
  Button,
  Descriptions,
  Divider,
  Popconfirm,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";

import AssetActionBar from "../../components/collectionAssets/AssetActionBar";
import StatusTag from "../../components/ui/StatusTag";
import {
  assetExecutionLabel,
  assetKindLabel,
  assetVersionLabel,
  assetVersionText,
  formatExecutionJson,
  isActionSpec,
  isAssetDeletable,
  isAssetEditable,
  isCollectionPlan,
  isCollectionSop,
  isScenario,
  presentAssetStatus,
} from "../../services/collectionAssetPresentation";
import { formatDateTime, formatIdentifier } from "../../services/format";
import type { DescriptionsProps } from "antd";
import type { ReactNode } from "react";
import type {
  CollectionAsset,
  CollectionAssetKind,
} from "../../types/collectionAsset";

export interface AssetDetailBodyProps {
  kind: CollectionAssetKind;
  asset: CollectionAsset;
  busy: boolean;
  deleting: boolean;
  onAction: (action: string, reason: string | null) => Promise<void>;
  onEdit: () => void;
  onDelete: () => void;
}

function ExecutionContent({
  label,
  items,
}: {
  label: string;
  items: readonly Record<string, unknown>[];
}) {
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Typography.Text type="secondary">
        {label}（任何状态都不可修改；内容变更请新建版本行）
      </Typography.Text>
      <Typography.Paragraph
        style={{ margin: 0, whiteSpace: "pre-wrap", fontFamily: "monospace" }}
      >
        {items.length === 0 ? "（空）" : formatExecutionJson(items)}
      </Typography.Paragraph>
    </Space>
  );
}

/** 采集素材详情的展示层：数据与回调由页面注入，组件不发请求。 */
export default function AssetDetailBody({
  kind,
  asset,
  busy,
  deleting,
  onAction,
  onEdit,
  onDelete,
}: AssetDetailBodyProps) {
  const label = assetKindLabel(kind);
  const versionLabel = assetVersionLabel(kind);
  const executionLabel = assetExecutionLabel(kind);
  const editable = isAssetEditable(asset.status);
  const deletable = isAssetDeletable(asset.status);

  const items: NonNullable<DescriptionsProps["items"]> = [
    { key: "name", label: "名称", children: asset.name },
    { key: "code", label: "编码", children: asset.code },
    {
      key: "status",
      label: "状态",
      children: (
        <StatusTag value={asset.status} presentation={presentAssetStatus} />
      ),
    },
    { key: "revision", label: "记录版本", children: String(asset.version) },
    {
      key: "updated",
      label: "更新时间",
      children: formatDateTime(asset.updated_at),
    },
    {
      key: "created",
      label: "创建时间",
      children: formatDateTime(asset.created_at),
    },
  ];

  if (versionLabel !== "") {
    items.splice(3, 0, {
      key: "content_version",
      label: versionLabel,
      children: assetVersionText(asset),
    });
  }

  if (isCollectionPlan(asset)) {
    items.push({
      key: "owner",
      label: "负责人",
      children: asset.owner_id ? formatIdentifier(asset.owner_id) : "—",
    });
  }

  items.push({
    key: "description",
    label: "描述",
    children: asset.description ?? "—",
  });

  let execution: ReactNode = null;
  if (isCollectionSop(asset) && executionLabel) {
    execution = (
      <ExecutionContent label={executionLabel} items={asset.steps} />
    );
  } else if (isActionSpec(asset) && executionLabel) {
    execution = (
      <ExecutionContent label={executionLabel} items={asset.actions} />
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Descriptions size="small" column={2} items={items} />
      {isScenario(asset) ? (
        <Space direction="vertical" size={4}>
          <Typography.Text type="secondary">
            场景标签（不版本化，可修改；由服务端保序去重）
          </Typography.Text>
          <Space wrap size={4}>
            {asset.scene_labels.length === 0 ? (
              <Typography.Text type="secondary">（空）</Typography.Text>
            ) : (
              asset.scene_labels.map((sceneLabel) => (
                <Tag key={sceneLabel}>{sceneLabel}</Tag>
              ))
            )}
          </Space>
        </Space>
      ) : null}
      {execution}
      {!editable ? (
        <Typography.Text type="secondary">
          已归档素材是历史任务版本快照的事实载体，不再允许修改。
        </Typography.Text>
      ) : null}
      <Divider style={{ margin: "8px 0" }} />
      <Space wrap>
        <Tooltip title={editable ? "" : "已归档素材不可编辑"}>
          <Button
            icon={<EditOutlined />}
            disabled={!editable}
            onClick={onEdit}
          >
            编辑
          </Button>
        </Tooltip>
        <Tooltip title={deletable ? "" : "只有草稿状态的素材可以删除"}>
          <Popconfirm
            title={`确认删除该${label}？`}
            description="删除后不可恢复；被采集任务引用时服务端会拒绝。"
            okText="删除"
            cancelText="取消"
            disabled={!deletable}
            onConfirm={onDelete}
          >
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={!deletable}
              loading={deleting}
            >
              删除
            </Button>
          </Popconfirm>
        </Tooltip>
        <AssetActionBar actions={asset.allowed_actions} busy={busy} onAction={onAction} />
      </Space>
    </Space>
  );
}
