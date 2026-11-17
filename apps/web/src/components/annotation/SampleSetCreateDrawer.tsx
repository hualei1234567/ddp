import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Form,
  Input,
  Select,
  Space,
  Typography,
} from "antd";
import { useEffect, useState } from "react";

import type { AnnotationSampleSetCreateInput } from "../../services/annotationSchemaApi";
import {
  DATA_SOURCE_VALUES,
  DEDUP_KEY_VALUES,
  SAMPLE_STRATEGY_VALUES,
} from "../../types/annotation";

interface SampleSetFormValues {
  code: string;
  name?: string | null;
  strategy: string;
  dedup_key: string;
  data_source: string;
  selection_rule_version?: string | null;
  include_episode_ids?: string[];
  exclude_episode_ids?: string[];
  scene_labels?: string[];
  warn_confirmed?: boolean;
}

interface SampleSetCreateDrawerProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (input: AnnotationSampleSetCreateInput) => Promise<void>;
}

const strategyOptions = SAMPLE_STRATEGY_VALUES.map((value) => ({
  value,
  label: value,
}));
const dedupOptions = DEDUP_KEY_VALUES.map((value) => ({ value, label: value }));
const dataSourceOptions = DATA_SOURCE_VALUES.map((value) => ({
  value,
  label: value,
}));

function cleanedIds(values: string[] | undefined): string[] {
  return (values ?? []).map((value) => value.trim()).filter((value) => value !== "");
}

function buildFilters(values: SampleSetFormValues): Record<string, unknown> {
  const filters: Record<string, unknown> = {};
  const include = cleanedIds(values.include_episode_ids);
  const exclude = cleanedIds(values.exclude_episode_ids);
  const scenes = cleanedIds(values.scene_labels);
  if (include.length > 0) {
    filters.include_episode_ids = include;
  }
  if (exclude.length > 0) {
    filters.exclude_episode_ids = exclude;
  }
  if (scenes.length > 0) {
    filters.scene_labels = scenes;
  }
  return filters;
}

/**
 * 新建样本集抽屉。
 *
 * 选择规则以 `filters.include_episode_ids` 显式点名 Episode（可选）；
 * 留空则由服务端按其它过滤条件选择。「选择规则版本」留空表示使用服务端默认。
 */
export default function SampleSetCreateDrawer({
  open,
  onClose,
  onSubmit,
}: SampleSetCreateDrawerProps) {
  const [form] = Form.useForm<SampleSetFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setError(null);
    form.resetFields();
    form.setFieldsValue({
      code: "",
      name: null,
      strategy: "FILTER",
      dedup_key: "EPISODE_LINEAGE",
      data_source: "UNKNOWN",
      selection_rule_version: null,
      include_episode_ids: [],
      exclude_episode_ids: [],
      scene_labels: [],
      warn_confirmed: false,
    });
  }, [form, open]);

  const handleSubmit = async () => {
    let values: SampleSetFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const input: AnnotationSampleSetCreateInput = {
        code: values.code.trim(),
        name: values.name?.trim() ? values.name.trim() : null,
        strategy: values.strategy,
        dedup_key: values.dedup_key,
        data_source: values.data_source,
        filters: buildFilters(values),
        warn_confirmed: values.warn_confirmed ?? false,
      };
      const ruleVersion = values.selection_rule_version?.trim();
      if (ruleVersion) {
        input.selection_rule_version = ruleVersion;
      }
      await onSubmit(input);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "创建样本集失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="新建标注样本集"
      width={640}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={submitting} onClick={() => void handleSubmit()}>
            创建
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        {error ? <Alert type="error" showIcon message={error} /> : null}
        <Typography.Paragraph type="secondary">
          新建后为 DRAFT；需先预览再快照，快照后的样本集才能用于创建标注任务。
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item
            name="code"
            label="编码"
            rules={[{ required: true, message: "请填写编码" }]}
          >
            <Input maxLength={64} placeholder="例如 ss-kitchen-001" />
          </Form.Item>
          <Form.Item name="name" label="名称">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="strategy" label="抽样策略">
            <Select options={strategyOptions} />
          </Form.Item>
          <Form.Item name="dedup_key" label="去重键">
            <Select options={dedupOptions} />
          </Form.Item>
          <Form.Item name="data_source" label="数据来源">
            <Select options={dataSourceOptions} />
          </Form.Item>
          <Form.Item
            name="selection_rule_version"
            label="选择规则版本"
            tooltip="留空使用服务端默认版本"
          >
            <Input maxLength={64} placeholder="留空使用服务端默认" />
          </Form.Item>
          <Form.Item
            name="include_episode_ids"
            label="点名 Episode（可选）"
            tooltip="输入 Episode UUID 后回车；留空则由服务端按过滤条件选择"
          >
            <Select mode="tags" open={false} tokenSeparators={[",", " "]} placeholder="回车添加 Episode ID" />
          </Form.Item>
          <Form.Item name="exclude_episode_ids" label="排除 Episode（可选）">
            <Select mode="tags" open={false} tokenSeparators={[",", " "]} placeholder="回车添加 Episode ID" />
          </Form.Item>
          <Form.Item name="scene_labels" label="场景标签（可选）">
            <Select mode="tags" open={false} tokenSeparators={[",", " "]} placeholder="回车添加场景标签" />
          </Form.Item>
          <Form.Item
            name="warn_confirmed"
            valuePropName="checked"
            extra="数据来源为 UNKNOWN / SYNTHETIC 时，服务端可能要求确认后才允许抽样。"
          >
            <Checkbox>已确认数据来源与抽样风险提示</Checkbox>
          </Form.Item>
        </Form>
      </Space>
    </Drawer>
  );
}
