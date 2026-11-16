import { Form, Input, InputNumber, Modal, Select, Typography, message } from "antd";
import { useState } from "react";

import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import {
  normalizeDescription,
  normalizeSceneLabels,
  parseStepJsonText,
  validateSceneLabels,
} from "../../services/collectionAssetForm";
import { assetKindLabel } from "../../services/collectionAssetPresentation";
import { collectionAssetApi } from "../../services/collectionAssets";
import type {
  CollectionAssetCreateInput,
  CollectionAssetKind,
} from "../../types/collectionAsset";

interface AssetCreateValues {
  code: string;
  name: string;
  description?: string;
  plan_version?: number;
  sop_version?: string;
  spec_version?: string;
  scene_labels?: string[];
  steps?: string;
  actions?: string;
}

interface AssetCreateModalProps {
  kind: CollectionAssetKind;
  projectId: string;
  open: boolean;
  onClose: () => void;
  /** 创建成功后由页面刷新列表。 */
  onCreated: () => void;
}

function stepJsonRule() {
  return {
    validator: (_: unknown, value: string | undefined) => {
      try {
        parseStepJsonText(value ?? "");
        return Promise.resolve();
      } catch (error) {
        return Promise.reject(
          error instanceof Error ? error : new Error("格式不正确"),
        );
      }
    },
  };
}

/**
 * 采集素材创建表单。
 *
 * 四类素材共用 code / name / description，差异字段按 `kind` 渲染：
 * - 采集方案：`plan_version`（创建后可填，之后不可改）；
 * - SOP / 动作库：版本串 + 执行内容（JSON 对象数组，创建后不可改）；
 * - 场景：`scene_labels`（不版本化，后续可改）。
 */
export default function AssetCreateModal({
  kind,
  projectId,
  open,
  onClose,
  onCreated,
}: AssetCreateModalProps) {
  const [form] = Form.useForm<AssetCreateValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const online = useOnlineStatus();
  const label = assetKindLabel(kind);

  const submit = async () => {
    let values: AssetCreateValues;
    try {
      values = await form.validateFields();
    } catch {
      // 表单校验失败：字段下方已有提示，不再弹全局消息。
      return;
    }

    try {
      setSubmitting(true);
      const common = {
        code: values.code.trim(),
        name: values.name.trim(),
        description: normalizeDescription(values.description),
      };
      let input: CollectionAssetCreateInput;
      switch (kind) {
        case "plans":
          input = {
            ...common,
            plan_version: values.plan_version ?? 1,
          };
          break;
        case "sops":
          input = {
            ...common,
            sop_version: (values.sop_version ?? "").trim(),
            steps: parseStepJsonText(values.steps ?? ""),
          };
          break;
        case "scenarios":
          input = {
            ...common,
            scene_labels: normalizeSceneLabels(values.scene_labels ?? []),
          };
          break;
        case "action-specs":
          input = {
            ...common,
            spec_version: (values.spec_version ?? "").trim(),
            actions: parseStepJsonText(values.actions ?? ""),
          };
          break;
      }
      await collectionAssetApi.create(kind, projectId, input);
      messageApi.success(`${label}已创建，初始状态为草稿（DRAFT）`);
      form.resetFields();
      onCreated();
      onClose();
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "创建失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title={`新建${label}`}
        open={open}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ disabled: !online }}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        {!online ? (
          <Typography.Paragraph type="warning">
            当前处于离线状态，无法提交创建请求。
          </Typography.Paragraph>
        ) : null}
        <Form form={form} layout="vertical" requiredMark>
          <Form.Item
            name="code"
            label="编码"
            rules={[
              { required: true, message: "请输入编码" },
              { max: 64, message: "编码最多 64 字符" },
            ]}
          >
            <Input placeholder="项目内唯一业务标识" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: "请输入名称" },
              { max: 200, message: "名称最多 200 字符" },
            ]}
          >
            <Input />
          </Form.Item>

          {kind === "plans" ? (
            <Form.Item
              name="plan_version"
              label="方案版本"
              initialValue={1}
              extra="创建后不再允许修改；内容更新请新建版本行。"
              rules={[{ required: true, message: "请输入方案版本" }]}
            >
              <InputNumber min={1} precision={0} style={{ width: "100%" }} />
            </Form.Item>
          ) : null}

          {kind === "sops" ? (
            <Form.Item
              name="sop_version"
              label="SOP 版本"
              rules={[
                { required: true, message: "请输入 SOP 版本" },
                { max: 64, message: "版本最多 64 字符" },
              ]}
            >
              <Input placeholder="例如 1.0.0" />
            </Form.Item>
          ) : null}

          {kind === "action-specs" ? (
            <Form.Item
              name="spec_version"
              label="动作库版本"
              rules={[
                { required: true, message: "请输入动作库版本" },
                { max: 64, message: "版本最多 64 字符" },
              ]}
            >
              <Input placeholder="例如 1.0.0" />
            </Form.Item>
          ) : null}

          {kind === "sops" ? (
            <Form.Item
              name="steps"
              label="步骤（JSON 对象数组）"
              initialValue="[]"
              extra="任何状态下都不可修改；改内容请新建版本行。"
              rules={[stepJsonRule()]}
            >
              <Input.TextArea rows={4} spellCheck={false} />
            </Form.Item>
          ) : null}

          {kind === "action-specs" ? (
            <Form.Item
              name="actions"
              label="动作（JSON 对象数组）"
              initialValue="[]"
              extra="任何状态下都不可修改；改内容请新建版本行。"
              rules={[stepJsonRule()]}
            >
              <Input.TextArea rows={4} spellCheck={false} />
            </Form.Item>
          ) : null}

          {kind === "scenarios" ? (
            <Form.Item
              name="scene_labels"
              label="场景标签"
              extra="回车添加标签；最多 32 个，单个最多 64 字符。"
              rules={[
                {
                  validator: (_, value: string[] | undefined) => {
                    const error = validateSceneLabels(value ?? []);
                    return error === null
                      ? Promise.resolve()
                      : Promise.reject(new Error(error));
                  },
                },
              ]}
            >
              <Select mode="tags" tokenSeparators={[","]} />
            </Form.Item>
          ) : null}

          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} maxLength={2000} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
