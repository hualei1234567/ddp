import { PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { datasetApi, type VersionCreateInput } from "../../services/datasets";
import {
  presentLeakageStatus,
  presentValidityStatus,
  presentVersionStatus,
} from "../../services/datasetPresentation";
import { datasetVersionPath } from "../../services/datasetUrl";
import type { DatasetVersion } from "../../types/dataset";
import ApiErrorState from "../ui/ApiErrorState";
import StatusTag from "./StatusTag";

interface PolicyOption {
  id: string;
  version: number;
  label: string;
}

export default function DatasetVersionsSection({
  projectId,
  datasetId,
  onVersionChange,
}: {
  projectId: string;
  datasetId: string;
  onVersionChange?: () => void;
}) {
  const [refreshToken, setRefreshToken] = useState(0);
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listVersions(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload, stale } = useCursorPage(
    load,
    (item) => item.dataset_version_id,
  );

  const [dedup, setDedup] = useState<PolicyOption[]>([]);
  const [split, setSplit] = useState<PolicyOption[]>([]);
  const [leakage, setLeakage] = useState<PolicyOption[]>([]);
  const [quality, setQuality] = useState<PolicyOption[]>([]);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<VersionCreateInput>();
  const [messageApi, holder] = message.useMessage();
  const [policyReloadToken, setPolicyReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const [dedupPage, splitPage, leakagePage, qualityPage] = await Promise.all([
          datasetApi.listDedupPolicies(projectId, datasetId),
          datasetApi.listSplitPolicies(projectId, datasetId),
          datasetApi.listLeakagePolicies(projectId, datasetId),
          datasetApi.listQualityPolicies(projectId, datasetId),
        ]);
        if (cancelled) return;
        setDedup(
          dedupPage.items.map((item) => ({
            id: item.dedup_policy_id,
            version: item.version,
            label: `${item.name ?? item.dedup_policy_id.slice(0, 8)} v${item.version}`,
          })),
        );
        setSplit(
          splitPage.items.map((item) => ({
            id: item.split_policy_id,
            version: item.version,
            label: `${item.name ?? item.split_policy_id.slice(0, 8)} v${item.version}`,
          })),
        );
        setLeakage(
          leakagePage.items.map((item) => ({
            id: item.leakage_policy_id,
            version: item.version,
            label: `${item.name ?? item.leakage_policy_id.slice(0, 8)} v${item.version}`,
          })),
        );
        setQuality(
          qualityPage.items.map((item) => ({
            id: item.quality_policy_id,
            version: item.version,
            label: `${item.name ?? item.quality_policy_id.slice(0, 8)} v${item.version}`,
          })),
        );
        setPolicyError(null);
      } catch (caught) {
        if (!cancelled) {
          setPolicyError(caught instanceof Error ? caught.message : "策略加载失败");
        }
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [projectId, datasetId, policyReloadToken]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      let filters: Record<string, unknown> = {};
      const rawFilters = (values as unknown as { selection_filters?: unknown })
        .selection_filters;
      if (typeof rawFilters === "string" && rawFilters.trim() !== "") {
        try {
          filters = JSON.parse(rawFilters) as Record<string, unknown>;
        } catch {
          throw new Error("选择过滤条件必须是合法 JSON");
        }
      }
      if (typeof rawFilters === "string") {
        delete (values as unknown as { selection_filters?: unknown })
          .selection_filters;
      }
      // 版本号由策略选择推导，避免用户手填错配。
      const dedupItem = dedup.find((item) => item.id === values.dedup_policy_id);
      const splitItem = split.find((item) => item.id === values.split_policy_id);
      const leakageItem = leakage.find((item) => item.id === values.leakage_policy_id);
      const qualityItem = quality.find((item) => item.id === values.quality_policy_id);
      if (!dedupItem || !splitItem || !leakageItem || !qualityItem) {
        throw new Error("请选择完整的四类策略");
      }
      await datasetApi.createVersion(projectId, datasetId, {
        ...values,
        selection_filters: filters,
        dedup_policy_id: dedupItem.id,
        dedup_policy_version: dedupItem.version,
        split_policy_id: splitItem.id,
        split_policy_version: splitItem.version,
        leakage_policy_id: leakageItem.id,
        leakage_policy_version: leakageItem.version,
        quality_policy_id: qualityItem.id,
        quality_policy_version: qualityItem.version,
      });
      messageApi.success("数据集版本已创建（DRAFT）");
      form.resetFields();
      setOpen(false);
      reload();
      setRefreshToken((value) => value + 1);
      onVersionChange?.();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  const columns: ColumnsType<DatasetVersion> = [
    {
      title: "版本",
      key: "version",
      width: 100,
      render: (_, item) => (
        <Link to={datasetVersionPath(datasetId, item.dataset_version_id)}>
          v{item.dataset_version}
        </Link>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 130,
      render: (_, item) => (
        <StatusTag value={item.status} presentation={presentVersionStatus} />
      ),
    },
    {
      title: "有效性",
      key: "validity",
      width: 110,
      render: (_, item) => (
        <StatusTag value={item.validity_status} presentation={presentValidityStatus} />
      ),
    },
    {
      title: "条目（训练/验证/测试）",
      key: "items",
      render: (_, item) =>
        `${item.item_count}（${item.train_count}/${item.validation_count}/${item.test_count}）`,
    },
    {
      title: "泄漏",
      key: "leakage",
      width: 110,
      render: (_, item) => (
        <StatusTag value={item.leakage_status} presentation={presentLeakageStatus} />
      ),
    },
    {
      title: "构建尝试",
      dataIndex: "build_attempt",
      width: 100,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 190,
      render: (value: string) => value,
    },
  ];

  const ready = dedup.length > 0 && split.length > 0 && leakage.length > 0 && quality.length > 0;

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <Space>
        <Button
          size="small"
          type="primary"
          icon={<PlusOutlined />}
          disabled={!ready}
          onClick={() => setOpen(true)}
        >
          新建版本
        </Button>
        <Button size="small" onClick={reload}>
          刷新
        </Button>
        <Button size="small" onClick={() => setPolicyReloadToken((value) => value + 1)}>
          刷新策略选项
        </Button>
      </Space>
      {!ready ? (
        <Typography.Text type="warning">
          需要先为该数据集创建去重、划分、泄漏与质量策略，才能创建版本。
        </Typography.Text>
      ) : null}
      {policyError ? <Typography.Text type="danger">{policyError}</Typography.Text> : null}
      {stale ? (
        <Typography.Text type="warning">刷新失败，展示上次成功数据。</Typography.Text>
      ) : null}
      <Table<DatasetVersion>
        key={`${datasetId}:${refreshToken}`}
        rowKey="dataset_version_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: "暂无版本" }}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建数据集版本"
        open={open}
        okText="创建"
        cancelText="取消"
        width={620}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="selection_rule_version"
            label="选择规则版本"
            initialValue="ddp.selection/1.0.0"
            rules={[{ required: true }]}
          >
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item name="dedup_policy_id" label="去重策略" rules={[{ required: true }]}>
            <Select options={dedup.map((item) => ({ value: item.id, label: item.label }))} />
          </Form.Item>
          <Form.Item name="split_policy_id" label="划分策略" rules={[{ required: true }]}>
            <Select options={split.map((item) => ({ value: item.id, label: item.label }))} />
          </Form.Item>
          <Form.Item name="leakage_policy_id" label="泄漏策略" rules={[{ required: true }]}>
            <Select options={leakage.map((item) => ({ value: item.id, label: item.label }))} />
          </Form.Item>
          <Form.Item name="quality_policy_id" label="质量策略" rules={[{ required: true }]}>
            <Select options={quality.map((item) => ({ value: item.id, label: item.label }))} />
          </Form.Item>
          <Form.Item name="build_now" label="创建后立即构建" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Form.Item name="supersedes_version_id" label="替代版本 ID（可选）">
            <Input placeholder="UUID" />
          </Form.Item>
          <Form.Item name="selection_filters" label="选择过滤条件（JSON，可选）">
            <Input.TextArea rows={3} placeholder='{"quality_status": ["PASS"]}' />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary">
          版本号由服务端分配；策略版本取当前所选策略的版本。
        </Typography.Text>
      </Modal>
    </Space>
  );
}
