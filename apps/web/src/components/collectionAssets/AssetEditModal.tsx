import { Form, Input, Modal, Select, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { ApiError } from "../../services/api";
import {
  assetId,
  assetKindLabel,
  isScenario,
} from "../../services/collectionAssetPresentation";
import {
  normalizeDescription,
  normalizeSceneLabels,
  validateSceneLabels,
} from "../../services/collectionAssetForm";
import { collectionAssetApi } from "../../services/collectionAssets";
import type {
  CollectionAsset,
  CollectionAssetKind,
  CollectionAssetPatchInput,
  ScenarioPatchInput,
} from "../../types/collectionAsset";

interface AssetEditValues {
  name: string;
  description?: string;
  scene_labels?: string[];
}

interface AssetEditModalProps {
  kind: CollectionAssetKind;
  projectId: string;
  asset: CollectionAsset;
  open: boolean;
  onClose: () => void;
  /** 保存成功后由页面刷新详情（version 会递增）。 */
  onSaved: () => void;
}

/**
 * 采集素材编辑表单：只编辑描述性字段。
 *
 * `code`、版本列、`steps` / `actions` 刻意不出现——它们不可改，
 * 出现在表单里只会诱导用户提交注定被 422 拒绝的请求。
 * `ARCHIVED` 状态下页面不展示编辑入口；服务端仍会拒绝越状态修改。
 */
export default function AssetEditModal({
  kind,
  projectId,
  asset,
  open,
  onClose,
  onSaved,
}: AssetEditModalProps) {
  const [form] = Form.useForm<AssetEditValues>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const label = assetKindLabel(kind);
  const editableLabels = isScenario(asset);

  useEffect(() => {
    if (!open) {
      return;
    }
    form.setFieldsValue({
      name: asset.name,
      description: asset.description ?? "",
      scene_labels: isScenario(asset) ? asset.scene_labels : [],
    });
  }, [open, asset, form]);

  const submit = async () => {
    let values: AssetEditValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    try {
      setSubmitting(true);
      const common = {
        name: values.name.trim(),
        description: normalizeDescription(values.description),
      };
      const patch: CollectionAssetPatchInput | ScenarioPatchInput = editableLabels
        ? {
            ...common,
            scene_labels: normalizeSceneLabels(values.scene_labels ?? []),
          }
        : common;
      await collectionAssetApi.update(kind, projectId, assetId(asset), patch, {
        version: asset.version,
      });
      messageApi.success(`${label}已保存`);
      onSaved();
      onClose();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("版本已变化或当前状态不允许修改，请刷新后重试。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "保存失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Modal
        title={`编辑${label}`}
        open={open}
        okText="保存"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={onClose}
        onOk={() => void submit()}
      >
        <Typography.Paragraph type="secondary">
          编码、版本号与执行内容不可修改；内容变更请新建版本行。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" requiredMark>
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
          {editableLabels ? (
            <Form.Item
              name="scene_labels"
              label="场景标签"
              extra="场景不版本化，标签可改；最多 32 个，单个最多 64 字符。"
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
            <Input.TextArea rows={3} maxLength={2000} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
