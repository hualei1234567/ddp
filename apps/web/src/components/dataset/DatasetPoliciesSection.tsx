import { PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useState } from "react";

import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import {
  DEDUP_EXACT_KEYS,
  SPLIT_STRATEGIES,
  type DedupPolicy,
  type LeakagePolicy,
  type QualityPolicy,
  type SplitPolicy,
} from "../../types/dataset";
import {
  datasetApi,
  type DedupPolicyInput,
  type LeakagePolicyInput,
  type QualityPolicyInput,
  type SplitPolicyInput,
} from "../../services/datasets";
import ApiErrorState from "../ui/ApiErrorState";

function PolicyHeader({
  title,
  hint,
  onRefresh,
}: {
  title: string;
  hint: string;
  onRefresh: () => void;
}) {
  return (
    <Space direction="vertical" size={2} style={{ width: "100%" }}>
      <Space>
        <Typography.Text strong>{title}</Typography.Text>
        <Button size="small" onClick={onRefresh}>
          刷新
        </Button>
      </Space>
      <Typography.Text type="secondary">{hint}</Typography.Text>
    </Space>
  );
}


export function DedupPoliciesSection({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listDedupPolicies(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload } = useCursorPage(load, (item) => item.dedup_policy_id);
  const [form] = Form.useForm<DedupPolicyInput>();
  const [messageApi, holder] = message.useMessage();
  const [open, setOpen] = useState(false);

  const columns: ColumnsType<DedupPolicy> = [
    { title: "名称", dataIndex: "name", render: (value: string | null) => value ?? "—" },
    { title: "版本", dataIndex: "version", width: 80 },
    {
      title: "精确键",
      dataIndex: "exact_keys",
      render: (value: string[]) => value.map((key) => <Tag key={key}>{key}</Tag>),
    },
    {
      title: "近似重复",
      dataIndex: "near_duplicate_enabled",
      width: 120,
      render: (value: boolean, item) =>
        value ? `${item.near_duplicate_enforcement}` : "关闭",
    },
    { title: "保留规则", dataIndex: "retain_rule", width: 160 },
  ];

  const submit = async () => {
    try {
      const values = await form.validateFields();
      await datasetApi.createDedupPolicy(projectId, datasetId, values);
      messageApi.success("去重策略已创建");
      form.resetFields();
      setOpen(false);
      reload();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <PolicyHeader
        title="去重策略"
        hint="精确去重默认使用 Episode 血缘、Manifest Hash 与 RawAsset Hash；近似重复默认仅告警。"
        onRefresh={reload}
      />
      <Space>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建去重策略
        </Button>
      </Space>
      <Table<DedupPolicy>
        rowKey="dedup_policy_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建去重策略"
        open={open}
        okText="创建"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="exact_keys"
            label="精确键"
            initialValue={[...DEDUP_EXACT_KEYS]}
            rules={[{ required: true, message: "至少选择一个精确键" }]}
          >
            <Select mode="multiple" options={DEDUP_EXACT_KEYS.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item name="near_duplicate_enabled" label="启用近似重复" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Form.Item name="near_duplicate_enforcement" label="近似重复处置" initialValue="WARN_ONLY">
            <Select options={[{ value: "WARN_ONLY" }, { value: "BLOCK" }]} />
          </Form.Item>
          <Form.Item name="retain_rule" label="保留规则" initialValue="NEWEST_ACCEPTED">
            <Select
              options={[
                { value: "NEWEST_ACCEPTED" },
                { value: "HIGHEST_QUALITY" },
                { value: "EARLIEST" },
                { value: "MANUAL" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export function SplitPoliciesSection({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listSplitPolicies(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload } = useCursorPage(load, (item) => item.split_policy_id);
  const [form] = Form.useForm<SplitPolicyInput>();
  const [messageApi, holder] = message.useMessage();
  const [open, setOpen] = useState(false);

  const columns: ColumnsType<SplitPolicy> = [
    { title: "名称", dataIndex: "name", render: (value: string | null) => value ?? "—" },
    { title: "版本", dataIndex: "version", width: 80 },
    { title: "策略", dataIndex: "strategy", width: 110 },
    {
      title: "比例（训练/验证/测试）",
      key: "ratios",
      render: (_, item) =>
        `${item.train_ratio} / ${item.validation_ratio} / ${item.test_ratio}`,
    },
    { title: "种子", dataIndex: "seed", width: 90 },
    {
      title: "分组键",
      dataIndex: "group_by",
      render: (value: string[]) => value.map((key) => <Tag key={key}>{key}</Tag>),
    },
  ];

  const submit = async () => {
    try {
      const values = await form.validateFields();
      await datasetApi.createSplitPolicy(projectId, datasetId, values);
      messageApi.success("划分策略已创建");
      form.resetFields();
      setOpen(false);
      reload();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <PolicyHeader
        title="划分策略"
        hint="默认 Episode 级 80/10/10，空测试集默认失败；seed 与策略版本决定可复现划分。"
        onRefresh={reload}
      />
      <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
        新建划分策略
      </Button>
      <Table<SplitPolicy>
        rowKey="split_policy_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建划分策略"
        open={open}
        okText="创建"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="strategy" label="策略" initialValue="RATIO" rules={[{ required: true }]}>
            <Select options={SPLIT_STRATEGIES.map((value) => ({ value }))} />
          </Form.Item>
          <Space>
            <Form.Item name="train_ratio" label="训练比例" initialValue={0.8}>
              <InputNumber min={0} max={1} step={0.05} />
            </Form.Item>
            <Form.Item name="validation_ratio" label="验证比例" initialValue={0.1}>
              <InputNumber min={0} max={1} step={0.05} />
            </Form.Item>
            <Form.Item name="test_ratio" label="测试比例" initialValue={0.1}>
              <InputNumber min={0} max={1} step={0.05} />
            </Form.Item>
          </Space>
          <Form.Item name="seed" label="随机种子" initialValue={42} rules={[{ required: true }]}>
            <InputNumber min={0} />
          </Form.Item>
          <Form.Item
            name="group_by"
            label="不可拆分分组键"
            initialValue={["EPISODE_LINEAGE"]}
            rules={[{ required: true, message: "至少一个分组键" }]}
          >
            <Select
              mode="multiple"
              options={[
                { value: "EPISODE_LINEAGE" },
                { value: "MANIFEST_SHA256" },
                { value: "RAW_ASSET_SHA256" },
                { value: "SIMILARITY_GROUP" },
                { value: "SCENE_GROUP" },
              ]}
            />
          </Form.Item>
          <Form.Item name="empty_split_policy" label="空划分策略" initialValue="FAIL">
            <Select options={[{ value: "FAIL" }, { value: "ALLOW_EMPTY_TEST" }]} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export function LeakagePoliciesSection({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listLeakagePolicies(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload } = useCursorPage(load, (item) => item.leakage_policy_id);
  const [form] = Form.useForm<LeakagePolicyInput>();
  const [messageApi, holder] = message.useMessage();
  const [open, setOpen] = useState(false);

  const columns: ColumnsType<LeakagePolicy> = [
    { title: "名称", dataIndex: "name", render: (value: string | null) => value ?? "—" },
    { title: "版本", dataIndex: "version", width: 80 },
    {
      title: "硬规则",
      dataIndex: "hard_rules",
      render: (value: string[]) => value.map((key) => <Tag key={key} color="red">{key}</Tag>),
    },
    {
      title: "软规则",
      dataIndex: "soft_rules",
      render: (value: string[]) => value.map((key) => <Tag key={key}>{key}</Tag>),
    },
  ];

  const submit = async () => {
    try {
      const values = await form.validateFields();
      await datasetApi.createLeakagePolicy(projectId, datasetId, values);
      messageApi.success("泄漏策略已创建");
      form.resetFields();
      setOpen(false);
      reload();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <PolicyHeader
        title="泄漏策略"
        hint="血缘/内容 Hash 跨划分为硬规则，不可豁免；场景/时间重叠等软规则仅 PO 可豁免。"
        onRefresh={reload}
      />
      <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
        新建泄漏策略
      </Button>
      <Table<LeakagePolicy>
        rowKey="leakage_policy_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建泄漏策略"
        open={open}
        okText="创建"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="hard_rules"
            label="硬规则"
            initialValue={[
              "EPISODE_LINEAGE_CROSS_SPLIT",
              "MANIFEST_SHA256_CROSS_SPLIT",
              "RAW_ASSET_SHA256_CROSS_SPLIT",
            ]}
            rules={[{ required: true, message: "至少一条硬规则" }]}
          >
            <Select
              mode="multiple"
              options={[
                { value: "EPISODE_LINEAGE_CROSS_SPLIT" },
                { value: "MANIFEST_SHA256_CROSS_SPLIT" },
                { value: "RAW_ASSET_SHA256_CROSS_SPLIT" },
                { value: "SIMILARITY_GROUP_CROSS_SPLIT" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="soft_rules"
            label="软规则"
            initialValue={["SCENE_GROUP_CROSS_SPLIT", "TEMPORAL_OVERLAP_CROSS_SPLIT"]}
          >
            <Select
              mode="multiple"
              options={[
                { value: "SCENE_GROUP_CROSS_SPLIT" },
                { value: "BATCH_GROUP_CROSS_SPLIT" },
                { value: "TEMPORAL_OVERLAP_CROSS_SPLIT" },
                { value: "DUPLICATE_ANNOTATION" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="waiver_allowed_rules"
            label="可豁免软规则"
            initialValue={["SCENE_GROUP_CROSS_SPLIT"]}
          >
            <Select
              mode="multiple"
              options={[
                { value: "SCENE_GROUP_CROSS_SPLIT" },
                { value: "BATCH_GROUP_CROSS_SPLIT" },
                { value: "TEMPORAL_OVERLAP_CROSS_SPLIT" },
                { value: "DUPLICATE_ANNOTATION" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

export function QualityPoliciesSection({
  projectId,
  datasetId,
}: {
  projectId: string;
  datasetId: string;
}) {
  const load = useCallback(
    (options: CursorPageRequestOptions) =>
      datasetApi.listQualityPolicies(projectId, datasetId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, datasetId],
  );
  const { items, loading, error, reload } = useCursorPage(load, (item) => item.quality_policy_id);
  const [form] = Form.useForm<QualityPolicyInput>();
  const [messageApi, holder] = message.useMessage();
  const [open, setOpen] = useState(false);

  const columns: ColumnsType<QualityPolicy> = [
    { title: "名称", dataIndex: "name", render: (value: string | null) => value ?? "—" },
    { title: "版本", dataIndex: "version", width: 80 },
    { title: "最小入选数", dataIndex: "min_selected_count", width: 120 },
    {
      title: "允许 WARN 质量",
      dataIndex: "allow_warn_quality",
      width: 140,
      render: (value: boolean) => (value ? "是" : "否"),
    },
    {
      title: "要求泄漏通过",
      dataIndex: "require_leakage_pass",
      width: 140,
      render: (value: boolean) => (value ? "是" : "否"),
    },
  ];

  const submit = async () => {
    try {
      const values = await form.validateFields();
      await datasetApi.createQualityPolicy(projectId, datasetId, values);
      messageApi.success("质量策略已创建");
      form.resetFields();
      setOpen(false);
      reload();
    } catch (caught) {
      if (caught instanceof Error) messageApi.error(caught.message);
    }
  };

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {holder}
      <PolicyHeader
        title="质量策略"
        hint="控制最小入选数量、是否允许 WARN 质量与是否要求泄漏检查通过。"
        onRefresh={reload}
      />
      <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
        新建质量策略
      </Button>
      <Table<QualityPolicy>
        rowKey="quality_policy_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
      />
      {error ? <ApiErrorState error={error} onRetry={reload} /> : null}
      <Modal
        title="新建质量策略"
        open={open}
        okText="创建"
        cancelText="取消"
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="min_selected_count" label="最小入选数量" initialValue={1}>
            <InputNumber min={1} />
          </Form.Item>
          <Form.Item name="allow_warn_quality" label="允许 WARN 质量" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Form.Item
            name="require_leakage_pass"
            label="要求泄漏检查通过"
            valuePropName="checked"
            initialValue={false}
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
